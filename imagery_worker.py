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
import argparse
import socket
import json
import math
import time
import random
import sys
from typing import Any, Dict, List, Tuple

from gps_time import utc_ns, utc_iso, sleep_to_next_tick, is_fire_window

# --- Network ---
PORT = 5002

# --- Phase 2: base dropout / robustness ---
# Overridden at runtime by --base-drop-prob; set to 0.0 for clean experiments.
BASE_DROP_PROB = 0.0
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
# XY < 50 m; Z higher than thermal (80 m) so 3D distance is dominated by height difference
DRONE_START_POS: Tuple[float, float, float] = (-25.0, -15.0, 80.0)
DIST_DROP_SLOPE: float = 0.001
MAX_DROP_PROB: float = 0.80
DRONE_STEP_M: float = 6.0        # imagery drone moves slightly faster
DRONE_ALT_MIN_M: float = 10.0
DRONE_ALT_MAX_M: float = 200.0
# Keep XY within this radius of controller
XY_MAX_M: float = 45.0


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
    # Clamp altitude
    z = max(DRONE_ALT_MIN_M, min(DRONE_ALT_MAX_M, z))
    # Clamp XY radius so drone stays within realistic radio range
    xy_dist = math.sqrt(x * x + y * y)
    if xy_dist > XY_MAX_M:
        scale = XY_MAX_M / xy_dist
        x, y = x * scale, y * scale
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

    Fire model: clock-based phase (is_fire_window) correlates with thermal so
    both sensors agree on "fire active" periods.
    During a fire window: 80% chance each detection is labelled 'fire'.
    Outside a fire window: 5% chance (occasional false positive).
    """
    in_fire = is_fire_window()

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
            box = _rand_box(base if i > 0 else None)
            # During fire window: high chance of fire label; outside: low chance
            fire_p = 0.80 if in_fire else 0.05
            label = "fire" if random.random() < fire_p else random.choice(["smoke", "tree", "rock"])
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
        "fire_sim": fire_present,
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
    parser = argparse.ArgumentParser(description="Imagery Worker")
    parser.add_argument("host", help="Controller IP address")
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for reproducible drone trajectory. "
             "Use the same seed across runs to isolate other variables.",
    )
    parser.add_argument(
        "--base-drop-prob", type=float, default=BASE_DROP_PROB,
        help=(
            "Base packet drop probability (before distance scaling). "
            "Set to 0 for clean experiments where only Mininet loss applies. "
            f"Default: {BASE_DROP_PROB}"
        ),
    )
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)
        print(f"[IMAGERY] Random seed set to {args.seed}")

    global BASE_DROP_PROB
    BASE_DROP_PROB = args.base_drop_prob
    print(f"[IMAGERY] base_drop_prob={BASE_DROP_PROB}")

    host = args.host
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
            sleep_to_next_tick(period)
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
            # Ground-truth fire label (mirrors thermal's fire_window field)
            "fire_window": is_fire_window(),
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

        sleep_to_next_tick(period)


if __name__ == "__main__":
    main()
