"""
Wildfire Multi-Drone Simulation — Imagery Worker
Phases implemented:
- Phase 1 (Realism): spatially-evolving fire model (cellular automaton), variable detections
- Phase 2 (Robustness): dropout simulation + reconnect loop
- Phase 3 (Timing): GPS/UTC-style timestamps (tx_ns) + processing time measurement
- Phase 5 (Distance): 3D drone position simulated; drop probability scales with distance
                      (DISABLED by default — enable with --dist-drop-slope > 0)
- Phase 7 (Clock sync): optional constant offset + Gaussian jitter on tx_ns timestamps
                        to simulate GPS clock calibration errors between drones

Flight model
────────────
The imagery drone follows the SAME lawnmower XY survey pattern as the thermal drone
but at a higher altitude (80m) for wider visual field of view.  Both drones cover
the same ground simultaneously, ensuring they can both detect the same fire event.
The trajectory seed controls small Gaussian GPS noise (0.5m std dev).

Fire model
──────────
Same FireGrid as thermal_worker (identical fire_seed) — both sensors always agree
on what the fire looks like without any communication.

Sends imagery detections over TCP to controller on port 5002.
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
PORT = 5002

# --- Phase 2: base dropout ---
BASE_DROP_PROB = 0.0
RECONNECT_SLEEP_S = 1.0

# --- Send behavior ---
SEND_HZ = 2

# --- Phase 1 realism controls ---
P_EMPTY = 0.20
MAX_DETECTIONS = 3

# --- Lawnmower survey pattern ---
# Same XY bounds and sweep parameters as thermal_worker.
# Imagery drone flies HIGHER for wider visual coverage (80m vs thermal's 40m).
SURVEY_X_MIN: float = -40.0
SURVEY_X_MAX: float = 40.0
SURVEY_Y_MIN: float = -40.0
SURVEY_Y_MAX: float = 40.0
STRIP_WIDTH_M: float = 12.0
MOVE_SPEED_M: float = 4.0
DRONE_ALTITUDE_M: float = 80.0  # imagery drone higher than thermal for wider FOV
GPS_NOISE_STD_M: float = 0.5

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
    Deterministic lawnmower waypoint — same formula as thermal_worker.
    Both drones cover the same XY area, thermal at 40m, imagery at 80m altitude.
    """
    x_range = x_max - x_min
    y_range = y_max - y_min
    steps_per_strip = max(1, int(x_range / speed))
    n_strips = max(1, int(y_range / strip_width))
    total_steps = steps_per_strip * n_strips

    pos_in_cycle = step % total_steps
    strip_idx = pos_in_cycle // steps_per_strip
    pos_in_strip = pos_in_cycle % steps_per_strip

    t = pos_in_strip / steps_per_strip
    if strip_idx % 2 == 0:
        x = x_min + t * x_range
    else:
        x = x_max - t * x_range

    y = y_min + (strip_idx + 0.5) * strip_width
    y = max(y_min, min(y_max, y))

    return (x, y, altitude)


def _rand_box(base: Tuple[int, int, int, int] | None = None) -> Tuple[int, int, int, int]:
    if base is None:
        x1 = random.randint(0, 80)
        y1 = random.randint(0, 80)
        w = random.randint(10, 40)
        h = random.randint(10, 40)
        return (x1, y1, x1 + w, y1 + h)
    bx1, by1, bx2, by2 = base
    jitter = 10
    x1 = max(0, bx1 + random.randint(-jitter, jitter))
    y1 = max(0, by1 + random.randint(-jitter, jitter))
    w = max(5, (bx2 - bx1) + random.randint(-jitter, jitter))
    h = max(5, (by2 - by1) + random.randint(-jitter, jitter))
    return (x1, y1, x1 + w, y1 + h)


def gen_imagery(drone_pos: Tuple[float, float, float],
                fire_grid: FireGrid) -> Dict[str, Any]:
    """
    Generate one imagery frame.

    fire_sim: drawn from spatial detection probability (FireGrid).
    Per-detection label: 80% fire if fire_sim, 5% otherwise.
    """
    detect_p = fire_grid.detection_prob((drone_pos[0], drone_pos[1]))
    fire_sim = random.random() < detect_p

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
            fire_p = 0.80 if fire_sim else 0.05
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
    global BASE_DROP_PROB, DIST_DROP_SLOPE, CLOCK_OFFSET_NS, CLOCK_JITTER_NS

    parser = argparse.ArgumentParser(description="Imagery Worker")
    parser.add_argument("host", help="Controller IP address")
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Random seed controlling GPS position noise (not the fire spread).",
    )
    parser.add_argument(
        "--fire-seed", type=int, default=0,
        help="Seed for the fire propagation model (must match thermal worker). Default: 0.",
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
        print(f"[IMAGERY] GPS noise seed: {args.seed}")

    BASE_DROP_PROB = args.base_drop_prob
    DIST_DROP_SLOPE = args.dist_drop_slope
    CLOCK_OFFSET_NS = int(args.clock_offset_ms * 1e6)
    CLOCK_JITTER_NS = args.clock_jitter_ms * 1e6

    fire_grid = FireGrid(seed=args.fire_seed)

    print(f"[IMAGERY] Flight: lawnmower survey  altitude={DRONE_ALTITUDE_M}m  "
          f"strip_width={STRIP_WIDTH_M}m  speed={MOVE_SPEED_M}m/step")
    print(f"[IMAGERY] Survey bounds: X[{SURVEY_X_MIN},{SURVEY_X_MAX}]  "
          f"Y[{SURVEY_Y_MIN},{SURVEY_Y_MAX}]")
    print(f"[IMAGERY] base_drop_prob={BASE_DROP_PROB}  dist_drop_slope={DIST_DROP_SLOPE}")
    print(f"[IMAGERY] clock_offset={args.clock_offset_ms}ms  jitter={args.clock_jitter_ms}ms")

    host = args.host
    seq = 0
    period = 1.0 / float(SEND_HZ)

    sock = connect(host)
    print(f"[IMAGERY] Connected.")

    while True:
        fire_grid.advance_to_wall_clock_step()

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
        msg = gen_imagery(drone_pos, fire_grid)
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
