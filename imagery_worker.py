"""
Wildfire Multi-Drone Simulation — Imagery Worker
Phases implemented:
- Phase 1 (Realism): variable detection counts, empty frames, overlapping boxes
- Phase 2 (Robustness): dropout simulation + reconnect loop
- Phase 3 (Timing): GPS/UTC-style timestamps (tx_ns) + processing time measurement

Sends imagery detections over TCP to controller on port 5002.
"""

from __future__ import annotations
import socket
import json
import time
import random
import sys
from typing import Any, Dict, List, Tuple

from gps_time import utc_ns, utc_iso

# --- Network ---
PORT = 5002

# --- Phase 2: dropouts / robustness ---
DROP_PROB = 0.10
RECONNECT_SLEEP_S = 1.0

# --- Send behavior ---
SEND_HZ = 2

# --- Phase 1 realism controls ---
P_EMPTY = 0.20           # 20% chance no detections
P_FIRE_LABEL = 0.35      # chance a detection is labeled fire (otherwise "smoke"/"tree"/etc.)
MAX_DETECTIONS = 3


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

    sock = connect(host)

    while True:
        if random.random() < DROP_PROB:
            time.sleep(period)
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
