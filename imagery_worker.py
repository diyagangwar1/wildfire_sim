"""
Wildfire Multi-Drone Simulation — Imagery Worker
Phases implemented:
- Phase 1 (Realism): variable detection counts, empty frames, overlapping boxes
- Phase 2 (Robustness): dropout simulation + reconnect loop
- Phase 3 (Timing): GPS/UTC-style timestamps (tx_ns) + processing time measurement
- Phase 5 (Distance): 3D drone position simulated; drop probability scales linearly with distance

Sends imagery detections over TCP to controller on port 5002.
"""

from __future__ import annotations
import socket
import json
import math
import time
import random
import sys
from typing import Any, Dict, List, Tuple

from gps_time import utc_ns, utc_iso

# --- Network ---
PORT = 5002

# --- Phase 2: base dropout / robustness ---
BASE_DROP_PROB = 0.05
RECONNECT_SLEEP_S = 1.0

# --- Send behavior ---
SEND_HZ = 2

# --- Phase 1 realism controls ---
P_EMPTY = 0.20           # 20% chance no detections
P_FIRE_LABEL = 0.35      # chance a detection is labeled fire (otherwise "smoke"/"tree"/etc.)
MAX_DETECTIONS = 3

# --- Phase 5: Distance-based drop probability ---
# Imagery drone starts at a different position from thermal drone.
CONTROLLER_POS: Tuple[float, float, float] = (0.0, 0.0, 0.0)
DRONE_START_POS: Tuple[float, float, float] = (-60.0, 40.0, 25.0)  # different start from thermal
DIST_DROP_SLOPE: float = 0.001   # drop_prob per metre
MAX_DROP_PROB: float = 0.80
DRONE_STEP_M: float = 6.0       # imagery drone moves slightly faster
DRONE_ALT_MIN_M: float = 10.0
DRONE_ALT_MAX_M: float = 120.0


def _distance_3d(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    return math.sqrt(sum((ai - bi) ** 2 for ai, bi in zip(a, b)))


def _drop_prob_from_distance(dist_m: float) -> float:
    p = BASE_DROP_PROB + DIST_DROP_SLOPE * dist_m
    return max(BASE_DROP_PROB, min(MAX_DROP_PROB, p))


def _random_walk_3d(
    pos: Tuple[float, float, float],
    step_m: float,
) -> Tuple[float, float, float]:
    dx = random.uniform(-step_m, step_m)
    dy = random.uniform(-step_m, step_m)
    dz = random.uniform(-step_m / 2, step_m / 2)
    x, y, z = pos[0] + dx, pos[1] + dy, pos[2] + dz
    z = max(DRONE_ALT_MIN_M, min(DRONE_ALT_MAX_M, z))
    return (x, y, z)


def _rand_box(base: Tuple[int, int, int, int] | None = None) -> Tuple[int, int, int, int]:
    """
    Generate a bounding box (x1,y1,x2,y2).
    If base provided, generate a box near it to create overlap.
    """
    if base is None:
        x1 = random.randint(0, 80)
        y1 = random.randint(0, 80)
        w = random.randint(10, 40)
        h = random.randint(10, 40)
        return (x1, y1, x1 + w, y1 + h)

    bx1, by1, bx2, by2 = base
    # Jitter around base to create overlap
    jitter = 10
    x1 = max(0, bx1 + random.randint(-jitter, jitter))
    y1 = max(0, by1 + random.randint(-jitter, jitter))
    w = max(5, (bx2 - bx1) + random.randint(-jitter, jitter))
    h = max(5, (by2 - by1) + random.randint(-jitter, jitter))
    return (x1, y1, x1 + w, y1 + h)


def gen_imagery() -> Dict[str, Any]:
    """
    Generate one imagery message with variable detections.
    """
    if random.random() < P_EMPTY:
        detections: List[Dict[str, Any]] = []
        shape = {"num_detections": 0}
        fire_present = False
    else:
        n = random.randint(1, MAX_DETECTIONS)
        detections = []
        base = _rand_box(None)

        fire_present = False
        for i in range(n):
            # Overlap: use base for most boxes
            box = _rand_box(base if i > 0 else None)
            label = "fire" if random.random() < P_FIRE_LABEL else random.choice(["smoke", "tree", "rock"])
            conf = round(random.uniform(0.4, 0.99), 3)

            if label == "fire":
                fire_present = True

            detections.append({
                "label": label,
                "conf": conf,
                "bbox": list(box),
            })

        shape = {"num_detections": n}

    return {
        "sensor": "imagery",
        "shape": shape,
        "detections": detections,
        "fire_sim": fire_present,  # debug/analysis only
    }


def connect(host: str) -> socket.socket:
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((host, PORT))
            return s
        except OSError:
            time.sleep(RECONNECT_SLEEP_S)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 imagery_worker.py <CONTROLLER_IP>")
        sys.exit(1)

    host = sys.argv[1]
    seq = 0
    period = 1.0 / float(SEND_HZ)

    # Phase 5: drone starts at fixed position and random-walks
    drone_pos: Tuple[float, float, float] = DRONE_START_POS

    sock = connect(host)
    print(f"[IMAGERY] Connected. Start pos={drone_pos}")

    while True:
        # Phase 5: update position and compute distance-based drop probability
        drone_pos = _random_walk_3d(drone_pos, DRONE_STEP_M)
        dist_m = _distance_3d(drone_pos, CONTROLLER_POS)
        drop_prob = _drop_prob_from_distance(dist_m)

        if random.random() < drop_prob:
            time.sleep(period)
            seq += 1
            continue

        proc_start_ns = utc_ns()
        msg = gen_imagery()
        proc_end_ns = utc_ns()

        tx_ns = utc_ns()
        msg.update({
            "seq": seq,
            "tx_ns": tx_ns,
            "tx_iso": utc_iso(tx_ns),
            "proc_ns": int(proc_end_ns - proc_start_ns),
            # Phase 5 telemetry
            "drone_x": round(drone_pos[0], 2),
            "drone_y": round(drone_pos[1], 2),
            "drone_z": round(drone_pos[2], 2),
            "distance_m": round(dist_m, 2),
            "drop_prob": round(drop_prob, 4),
        })
        seq += 1

        line = (json.dumps(msg) + "\n").encode()
        try:
            sock.sendall(line)
        except OSError:
            try:
                sock.close()
            except Exception:
                pass
            sock = connect(host)

        time.sleep(period)


if __name__ == "__main__":
    main()
