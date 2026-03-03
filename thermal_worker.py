"""
Wildfire Multi-Drone Simulation — Thermal Worker
Phases implemented:
- Phase 1 (Realism): probabilistic fire, variable shapes (2D grids + 1D arrays)
- Phase 2 (Robustness): dropout simulation + reconnect loop
- Phase 3 (Timing): GPS/UTC-style timestamps (tx_ns) + processing time measurement
- Phase 5 (Distance): 3D drone position simulated; drop probability scales linearly with distance

Sends thermal frames over TCP to controller on port 5001.
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
PORT = 5001

# --- Phase 2: base dropout / robustness ---
# Overridden at runtime by --base-drop-prob; set to 0.0 for clean experiments.
# Phase 5 adds distance-based component on top of this value.
BASE_DROP_PROB = 0.0      # overridden by --base-drop-prob (default 0 = clean)
RECONNECT_SLEEP_S = 1.0

# --- Send behavior ---
SEND_HZ = 2

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

# --- Phase 5: Distance-based drop probability ---
# Drone starts at DRONE_START_POS and drifts randomly each step.
# drop_prob = clamp(BASE_DROP_PROB + DIST_DROP_SLOPE * distance_m, 0, MAX_DROP_PROB)
CONTROLLER_POS: Tuple[float, float, float] = (0.0, 0.0, 0.0)
# XY < 50 m from controller; height (Z) can be larger (per prof feedback)
DRONE_START_POS: Tuple[float, float, float] = (30.0, 10.0, 60.0)
DIST_DROP_SLOPE: float = 0.001   # drop_prob per metre of distance
MAX_DROP_PROB: float = 0.80
DRONE_STEP_M: float = 5.0        # random-walk step size (metres per timestep)
DRONE_ALT_MIN_M: float = 10.0
DRONE_ALT_MAX_M: float = 200.0   # taller range so Z can dominate the 3D distance
# Keep XY within this radius of controller so drones stay in realistic radio range
XY_MAX_M: float = 45.0


def _distance_3d(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    return math.sqrt(sum((ai - bi) ** 2 for ai, bi in zip(a, b)))


def _drop_prob_from_distance(dist_m: float) -> float:
    """Linear model: increases with distance, clamped to [BASE_DROP_PROB, MAX_DROP_PROB]."""
    p = BASE_DROP_PROB + DIST_DROP_SLOPE * dist_m
    return max(BASE_DROP_PROB, min(MAX_DROP_PROB, p))


def _random_walk_3d(
    pos: Tuple[float, float, float],
    step_m: float,
) -> Tuple[float, float, float]:
    """Move drone by a random step in 3D; clamp altitude and XY radius."""
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


def _gen_grid(r: int, c: int) -> List[List[float]]:
    grid = [[random.gauss(BASE_MEAN, BASE_STD) for _ in range(c)] for _ in range(r)]
    return grid


def _gen_1d(n: int) -> List[float]:
    arr = [random.gauss(BASE_MEAN, BASE_STD) for _ in range(n)]
    return arr


def gen_thermal() -> Dict[str, Any]:
    """
    Generate one thermal message.

    Fire model: clock-based phase (is_fire_window) keeps thermal and imagery
    correlated — both sensors are in a 'fire active' state at the same time.
    During a fire window: 85% chance of high-temp fire signal.
    Outside a fire window: 10% chance (occasional false positive).
    """
    in_fire = is_fire_window()
    if in_fire:
        fire_sim = random.random() < 0.85
    else:
        fire_sim = random.random() < 0.10

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
    parser = argparse.ArgumentParser(description="Thermal Worker")
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
        print(f"[THERMAL] Random seed set to {args.seed}")

    global BASE_DROP_PROB
    BASE_DROP_PROB = args.base_drop_prob
    print(f"[THERMAL] base_drop_prob={BASE_DROP_PROB}")

    host = args.host
    seq = 0
    period = 1.0 / float(SEND_HZ)

    # Phase 5: drone starts at a fixed position and random-walks
    drone_pos: Tuple[float, float, float] = DRONE_START_POS

    sock = connect(host)
    print(f"[THERMAL] Connected. Start pos={drone_pos}")

    while True:
        # Phase 5: update drone position and compute distance-based drop prob
        drone_pos = _random_walk_3d(drone_pos, DRONE_STEP_M)
        dist_m = _distance_3d(drone_pos, CONTROLLER_POS)
        drop_prob = _drop_prob_from_distance(dist_m)

        # Phase 2 + 5: dropout (now distance-aware)
        if random.random() < drop_prob:
            sleep_to_next_tick(period)
            seq += 1
            continue

        proc_start_ns = utc_ns()
        msg = gen_thermal()
        proc_end_ns = utc_ns()

        tx_ns = utc_ns()
        msg.update({
            "seq": seq,
            "tx_ns": tx_ns,
            "tx_iso": utc_iso(tx_ns),
            "proc_ns": int(proc_end_ns - proc_start_ns),
            # Ground-truth fire label: was the fire window active when this
            # message was sent?  Controller uses this for hit/miss accounting.
            "fire_window": is_fire_window(),
            # Phase 5: drone telemetry
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

        # GPS clock-snap: sleep until the next period-aligned tick so both
        # workers send at the same wall-clock instants (e.g. t=0.0, 0.5, 1.0…)
        sleep_to_next_tick(period)


if __name__ == "__main__":
    main()
