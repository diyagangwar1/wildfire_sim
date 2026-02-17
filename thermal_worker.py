"""
Wildfire Multi-Drone Simulation — Thermal Worker
Phases implemented:
- Phase 1 (Realism): probabilistic fire, variable shapes (2D grids + 1D arrays)
- Phase 2 (Robustness): dropout simulation + reconnect loop
- Phase 3 (Timing): GPS/UTC-style timestamps (tx_ns) + processing time measurement

Sends thermal frames over TCP to controller on port 5001.
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
PORT = 5001

# --- Phase 2: dropouts / robustness ---
DROP_PROB = 0.10          # simulate link loss at app layer (in addition to Mininet loss)
RECONNECT_SLEEP_S = 1.0   # if controller is down, retry

# --- Send behavior ---
SEND_HZ = 2               # send rate in Hz

# --- Temperature model ---
BASE_MEAN = 70.0
BASE_STD = 2.0

# --- Probabilistic fire model ---
FIRE_P_THRESHOLD = 0.6
FIRE_INFLATION = 10.0
HOTSPOT_MEAN = 130.0      # should exceed controller threshold of 100
HOTSPOT_STD = 3.0

# --- Variable data sizes ---
SHAPES_2D: List[Tuple[int, int]] = [(2, 2), (3, 3), (4, 4), (2, 4)]
LENS_1D: List[int] = [2, 4, 8, 15]
P_SEND_1D = 0.30


def _gen_grid(r: int, c: int) -> List[List[float]]:
    grid = [[random.gauss(BASE_MEAN, BASE_STD) for _ in range(c)] for _ in range(r)]
    return grid


def _gen_1d(n: int) -> List[float]:
    arr = [random.gauss(BASE_MEAN, BASE_STD) for _ in range(n)]
    return arr


def gen_thermal() -> Dict[str, Any]:
    """
    Generate one thermal message.
    Phase 1 realism: variable shapes + probabilistic fire.
    Phase 3 timing: caller measures proc_ns; we include the decision fire_sim for debug.
    """
    fire_sim = (random.random() > FIRE_P_THRESHOLD)

    send_1d = (random.random() < P_SEND_1D)

    if send_1d:
        n = random.choice(LENS_1D)
        data = _gen_1d(n)

        if fire_sim:
            # Inflate baseline
            data = [x + FIRE_INFLATION for x in data]
            # Add a hotspot at a random index
            idx = random.randrange(n)
            data[idx] = random.gauss(HOTSPOT_MEAN, HOTSPOT_STD)

        shape = {"type": "1d", "len": n}
    else:
        r, c = random.choice(SHAPES_2D)
        grid = _gen_grid(r, c)

        if fire_sim:
            # Inflate baseline + insert a hotspot somewhere
            for i in range(r):
                for j in range(c):
                    grid[i][j] += FIRE_INFLATION
            hi = random.randrange(r)
            hj = random.randrange(c)
            grid[hi][hj] = random.gauss(HOTSPOT_MEAN, HOTSPOT_STD)

        data = grid
        shape = {"type": "2d", "rows": r, "cols": c}

    return {
        "sensor": "thermal",
        "shape": shape,
        "data": data,
        "fire_sim": fire_sim,  # debug/analysis only
    }


def connect(host: str) -> socket.socket:
    """Phase 2 robustness: keep trying until controller accepts."""
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((host, PORT))
            return s
        except OSError:
            time.sleep(RECONNECT_SLEEP_S)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 thermal_worker.py <CONTROLLER_IP>")
        sys.exit(1)

    host = sys.argv[1]
    seq = 0
    period = 1.0 / float(SEND_HZ)

    sock = connect(host)

    while True:
        # Phase 2: dropout simulation
        if random.random() < DROP_PROB:
            time.sleep(period)
            continue

        # Phase 3: timing
        proc_start_ns = utc_ns()
        msg = gen_thermal()
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
            # Phase 2: reconnect if controller/mininet link restarts
            try:
                sock.close()
            except Exception:
                pass
            sock = connect(host)

        time.sleep(period)


if __name__ == "__main__":
    main()
