"""
Wildfire Multi-Drone Simulation — Thermal Worker
Phases implemented:
- Phase 1 (Realism): spatially-evolving fire model (cellular automaton), variable shapes
- Phase 2 (Robustness): dropout simulation + reconnect loop
- Phase 3 (Timing): GPS/UTC-style timestamps (tx_ns) + processing time measurement
- Phase 5 (Distance): 3D drone position simulated; drop probability scales with distance
                      (DISABLED by default — enable with --dist-drop-slope > 0)
- Phase 7 (Clock sync): optional constant offset + Gaussian jitter on tx_ns timestamps
                        to simulate GPS clock calibration errors between drones

Flight model
────────────
The thermal drone follows a deterministic lawnmower survey pattern over the ±40m
survey area at a fixed altitude (40m), sweeping back and forth in X and advancing
in Y by STRIP_WIDTH after each sweep.  This is the standard drone surveillance
flight plan for maximising area coverage.

Small Gaussian GPS noise (0.5m std dev, controlled by --seed) is added to each
position to simulate realistic GPS accuracy and slight wind effects.

Fire model
──────────
Uses fire_model.FireGrid — cellular automaton that spreads from ignition point (20,20).
Both workers share the same fire_seed so their grids are byte-for-byte identical at
every wall-clock step, without any communication.

Sends thermal frames over TCP to controller on port 5001.
"""

from __future__ import annotations
import argparse
import socket
import json
import math
import time
import random
from typing import Any, Dict, List, Tuple

from gps_time import utc_ns, utc_iso, sleep_to_next_tick
from fire_model import FireGrid

# --- Network ---
PORT = 5001

# --- Phase 2: base dropout ---
BASE_DROP_PROB = 0.0
RECONNECT_SLEEP_S = 1.0

# --- Send behavior ---
SEND_HZ = 2

# --- Temperature model ---
BASE_MEAN = 70.0
BASE_STD = 2.0

# --- Fire sensor response ---
FIRE_INFLATION = 10.0
HOTSPOT_MEAN = 130.0   # well above controller's 100°C threshold
HOTSPOT_STD = 3.0

# --- Variable data sizes ---
SHAPES_2D: List[Tuple[int, int]] = [(2, 2), (3, 3), (4, 4), (2, 4)]
LENS_1D: List[int] = [2, 4, 8, 15]
P_SEND_1D = 0.30

# --- Lawnmower survey pattern ---
# Both drones sweep the same XY survey area. The thermal drone stays lower for better
# thermal resolution; the imagery drone flies higher for wider visual coverage.
SURVEY_X_MIN: float = -40.0
SURVEY_X_MAX: float = 40.0
SURVEY_Y_MIN: float = -40.0
SURVEY_Y_MAX: float = 40.0
STRIP_WIDTH_M: float = 12.0    # metres between parallel strips (~7 strips across 80m)
MOVE_SPEED_M: float = 4.0      # metres advanced per timestep (4m/step × 2Hz = 8 m/s)
DRONE_ALTITUDE_M: float = 40.0 # thermal drone cruises lower for heat resolution
GPS_NOISE_STD_M: float = 0.5   # position noise std dev (realistic GPS accuracy, ~0.5m)

# --- Controller position (for distance drop calculation) ---
CONTROLLER_POS: Tuple[float, float, float] = (0.0, 0.0, 0.0)

# --- Phase 5: Distance-based drop (disabled by default) ---
DIST_DROP_SLOPE: float = 0.0
MAX_DROP_PROB: float = 0.80

# --- Phase 7: Clock sync error (overridden by CLI) ---
CLOCK_OFFSET_NS: int = 0
CLOCK_JITTER_NS: float = 0.0


def _distance_3d(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    return math.sqrt(sum((ai - bi) ** 2 for ai, bi in zip(a, b)))


def _drop_prob_from_distance(dist_m: float) -> float:
    p = BASE_DROP_PROB + DIST_DROP_SLOPE * dist_m
    return max(BASE_DROP_PROB, min(MAX_DROP_PROB, p))


def _lawnmower_pos(step: int, altitude: float,
                   x_min: float = SURVEY_X_MIN,
                   x_max: float = SURVEY_X_MAX,
                   y_min: float = SURVEY_Y_MIN,
                   y_max: float = SURVEY_Y_MAX,
                   strip_width: float = STRIP_WIDTH_M,
                   speed: float = MOVE_SPEED_M) -> Tuple[float, float, float]:
    """
    Deterministic lawnmower waypoint at the given step count.

    The drone sweeps X from x_min → x_max (even strips) or x_max → x_min (odd strips),
    advancing Y by strip_width after each full sweep.  The pattern loops indefinitely.

    This is a standard drone surveillance flight plan — it guarantees that every
    part of the survey area, including the fire zone at (20, 20), is covered on
    every pass.
    """
    x_range = x_max - x_min
    y_range = y_max - y_min
    steps_per_strip = max(1, int(x_range / speed))
    n_strips = max(1, int(y_range / strip_width))
    total_steps = steps_per_strip * n_strips

    pos_in_cycle = step % total_steps
    strip_idx = pos_in_cycle // steps_per_strip
    pos_in_strip = pos_in_cycle % steps_per_strip

    t = pos_in_strip / steps_per_strip   # 0.0 → 1.0 across the strip
    if strip_idx % 2 == 0:
        x = x_min + t * x_range          # left to right
    else:
        x = x_max - t * x_range          # right to left

    y = y_min + (strip_idx + 0.5) * strip_width
    y = max(y_min, min(y_max, y))

    return (x, y, altitude)


def _gen_grid(r: int, c: int) -> List[List[float]]:
    return [[random.gauss(BASE_MEAN, BASE_STD) for _ in range(c)] for _ in range(r)]


def _gen_1d(n: int) -> List[float]:
    return [random.gauss(BASE_MEAN, BASE_STD) for _ in range(n)]


def gen_thermal(drone_pos: Tuple[float, float, float],
                fire_grid: FireGrid) -> Dict[str, Any]:
    """
    Generate one thermal frame.

    Detection probability is position-dependent: scales with the number of
    burning cells within 40m of the drone's current horizontal position.
    """
    detect_p = fire_grid.detection_prob((drone_pos[0], drone_pos[1]))
    fire_sim = random.random() < detect_p

    send_1d = (random.random() < P_SEND_1D)

    if send_1d:
        n = random.choice(LENS_1D)
        data = _gen_1d(n)
        if fire_sim:
            data = [x + FIRE_INFLATION for x in data]
            idx = random.randrange(n)
            data[idx] = random.gauss(HOTSPOT_MEAN, HOTSPOT_STD)
        shape = {"type": "1d", "len": n}
    else:
        r, c = random.choice(SHAPES_2D)
        grid = _gen_grid(r, c)
        if fire_sim:
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
        "fire_sim": fire_sim,
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
    global BASE_DROP_PROB, DIST_DROP_SLOPE, CLOCK_OFFSET_NS, CLOCK_JITTER_NS

    parser = argparse.ArgumentParser(description="Thermal Worker")
    parser.add_argument("host", help="Controller IP address")
    parser.add_argument(
        "--seed", type=int, default=None,
        help=(
            "Random seed controlling GPS position noise. "
            "Different seeds give slightly different jittered paths over the "
            "same deterministic lawnmower survey route."
        ),
    )
    parser.add_argument(
        "--fire-seed", type=int, default=0,
        help="Seed for the fire propagation model (must match imagery worker). Default: 0.",
    )
    parser.add_argument(
        "--base-drop-prob", type=float, default=BASE_DROP_PROB,
        help="Base packet drop probability. Default 0.",
    )
    parser.add_argument(
        "--dist-drop-slope", type=float, default=DIST_DROP_SLOPE,
        help="Drop probability per metre from controller. Default 0.0 (disabled).",
    )
    parser.add_argument(
        "--clock-offset-ms", type=float, default=0.0,
        help="Constant GPS clock bias (ms) on this drone's tx_ns.",
    )
    parser.add_argument(
        "--clock-jitter-ms", type=float, default=0.0,
        help="Std dev (ms) of per-message Gaussian noise on tx_ns.",
    )
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)
        print(f"[THERMAL] GPS noise seed: {args.seed}")

    BASE_DROP_PROB = args.base_drop_prob
    DIST_DROP_SLOPE = args.dist_drop_slope
    CLOCK_OFFSET_NS = int(args.clock_offset_ms * 1e6)
    CLOCK_JITTER_NS = args.clock_jitter_ms * 1e6

    fire_grid = FireGrid(seed=args.fire_seed)

    print(f"[THERMAL] Flight: lawnmower survey  altitude={DRONE_ALTITUDE_M}m  "
          f"strip_width={STRIP_WIDTH_M}m  speed={MOVE_SPEED_M}m/step")
    print(f"[THERMAL] Survey bounds: X[{SURVEY_X_MIN},{SURVEY_X_MAX}]  "
          f"Y[{SURVEY_Y_MIN},{SURVEY_Y_MAX}]")
    print(f"[THERMAL] base_drop_prob={BASE_DROP_PROB}  dist_drop_slope={DIST_DROP_SLOPE}")
    print(f"[THERMAL] clock_offset={args.clock_offset_ms}ms  jitter={args.clock_jitter_ms}ms")

    host = args.host
    seq = 0
    period = 1.0 / float(SEND_HZ)

    sock = connect(host)
    print(f"[THERMAL] Connected.")

    while True:
        fire_grid.advance_to_wall_clock_step()

        # Deterministic lawnmower waypoint + small GPS noise from trajectory seed
        base_pos = _lawnmower_pos(seq, DRONE_ALTITUDE_M)
        noise_x = random.gauss(0.0, GPS_NOISE_STD_M)
        noise_y = random.gauss(0.0, GPS_NOISE_STD_M)
        noise_z = random.gauss(0.0, GPS_NOISE_STD_M * 0.5)
        drone_pos: Tuple[float, float, float] = (
            base_pos[0] + noise_x,
            base_pos[1] + noise_y,
            max(10.0, base_pos[2] + noise_z),
        )

        dist_m = _distance_3d(drone_pos, CONTROLLER_POS)
        drop_prob = _drop_prob_from_distance(dist_m)

        if random.random() < drop_prob:
            sleep_to_next_tick(period)
            seq += 1
            continue

        proc_start_ns = utc_ns()
        msg = gen_thermal(drone_pos, fire_grid)
        proc_end_ns = utc_ns()

        raw_tx_ns = utc_ns()
        jitter_ns = int(random.gauss(0, CLOCK_JITTER_NS)) if CLOCK_JITTER_NS > 0 else 0
        tx_ns = raw_tx_ns + CLOCK_OFFSET_NS + jitter_ns

        msg.update({
            "seq": seq,
            "tx_ns": tx_ns,
            "tx_iso": utc_iso(tx_ns),
            "proc_ns": int(proc_end_ns - proc_start_ns),
            "fire_window": fire_grid.is_any_burning(),
            "fire_cell_count": fire_grid.total_burning(),
            "fire_visible_count": fire_grid.burning_near(drone_pos[0], drone_pos[1]),
            "drone_x": round(drone_pos[0], 2),
            "drone_y": round(drone_pos[1], 2),
            "drone_z": round(drone_pos[2], 2),
            "distance_m": round(dist_m, 2),
            "drop_prob": round(drop_prob, 4),
            "clock_offset_ns": CLOCK_OFFSET_NS,
            "clock_jitter_ns": jitter_ns,
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
