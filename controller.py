"""
Wildfire Multi-Drone Simulation — Ground Station Controller

Phase 1: Fusion logic (thermal max temp + imagery fire label + time window)
Phase 2: Port robustness, drop monitoring (rolling window), stop condition, threading + locks
Phase 3: GPS/UTC timestamps, latency breakdown, latency_log.jsonl
Phase 4: Rolling window thresholding — fire decision requires K consecutive confirmations
Phase 5: (controller-side) Accepts drone position from workers; logs distance info
Phase 6: GPS-based pair matching — buffer last N messages per stream; fuse nearest pair
         within SYNC_THRESHOLD_MS instead of always using "most recent".

Listens on TCP 5001 (thermal) and TCP 5002 (imagery).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import socket
import sys
import threading
import time
from collections import deque
from typing import Any, Dict, Optional, Tuple

from gps_time import utc_iso, utc_ns

THERMAL_PORT = 5001
IMAGERY_PORT = 5002
HOST = "0.0.0.0"

# Phase 1 fusion constants
TEMP_THRESHOLD = 100.0
# With GPS clock-snap workers send within ~5 ms of each other, so matched pairs
# always have dt_s << 0.5s.  Keep 0.5s (half the send period) as a safety margin
# rather than the old 2s which was larger than the send period itself.
TIME_WINDOW_S = 0.5

# Phase 2: drop monitoring + stop condition
EXPECTED_THERMAL_HZ = 2.0
EXPECTED_IMAGERY_HZ = 2.0
MONITOR_WINDOW_S = 10.0
DROP_STOP_THRESHOLD = 0.50  # stop if drop rate > 50%

# Phase 4: Rolling-window thresholding
FIRE_WINDOW_K: int = 5      # sliding window size (number of fusion events)
FIRE_CONFIRM_K: int = 3     # minimum confirmations required within the window

# Phase 6: GPS-based pair matching
# Keep the last SYNC_BUFFER_SIZE messages per stream.
# Only fuse a pair if |tx_thermal - tx_imagery| <= SYNC_THRESHOLD_MS.
# Set SYNC_THRESHOLD_MS very large (e.g. 2000) to get old "nearest always fused" behaviour.
SYNC_BUFFER_SIZE: int = 10
SYNC_THRESHOLD_MS: float = 5.0  # overridden by --sync-threshold-ms

# Output logs (paths set at startup from --outdir)
LATENCY_LOG_JSONL = "latency_log.jsonl"
FUSION_LOG_CSV = "fusion_log.csv"

# --- Shared state (thread-safe with lock) ---
lock = threading.Lock()

# Phase 6: per-stream message buffers (replaces last_thermal / last_imagery)
thermal_buffer: deque = deque(maxlen=SYNC_BUFFER_SIZE)
imagery_buffer: deque = deque(maxlen=SYNC_BUFFER_SIZE)

# Keep a small set of recently fused pairs to prevent re-fusion when the
# buffer rotates and the same semantic pair reappears with a different entry.
fused_pairs: deque = deque(maxlen=20)   # stores (thermal_tx_ns, imagery_tx_ns)

# Phase 2: rolling-window packet arrival timestamps (seconds)
thermal_arrivals: deque = deque()
imagery_arrivals: deque = deque()

# Phase 2: first-seen timestamps (seconds) to avoid "startup false drop"
first_thermal_seen_s: Optional[float] = None
first_imagery_seen_s: Optional[float] = None

# Phase 2: server health (bind success)
thermal_server_up = False
imagery_server_up = False

# Phase 3: fusion id for logging
fusion_id = 0

# Phase 4: sliding window of per-event raw fire signals
fire_signal_window: deque = deque(maxlen=FIRE_WINDOW_K)

# CSV file handle (opened at startup)
logfile = None
csv_writer = None


def clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def prune_old(arrivals: deque, now_s: float, window_s: float) -> None:
    """Remove timestamps older than window_s from arrivals deque."""
    cutoff = now_s - window_s
    while arrivals and arrivals[0] < cutoff:
        arrivals.popleft()


def safe_max_temp(thermal_data: Any) -> Tuple[float, str]:
    """Handle 2D grid or 1D list. Returns (max_temp, shape_str)."""
    if thermal_data is None:
        return float("-inf"), "none"
    if isinstance(thermal_data, list) and thermal_data and isinstance(thermal_data[0], list):
        r = len(thermal_data)
        c = len(thermal_data[0]) if r > 0 else 0
        m = max(max(row) for row in thermal_data if row)
        return float(m), f"{r}x{c}"
    if isinstance(thermal_data, list):
        m = max(thermal_data) if thermal_data else float("-inf")
        return float(m), f"1d:{len(thermal_data)}"
    return float("-inf"), "unknown"


def imagery_has_fire(dets: Any) -> bool:
    if not dets or not isinstance(dets, list):
        return False
    for d in dets:
        if isinstance(d, dict) and d.get("label") == "fire":
            return True
    return False


def recv_lines(conn: socket.socket, on_msg) -> None:
    """Read newline-delimited JSON; call on_msg(msg, rx_ns) for each valid message."""
    buf = ""
    while True:
        chunk = conn.recv(4096)
        if not chunk:
            return
        buf += chunk.decode(errors="ignore")
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            rx_ns = utc_ns()
            on_msg(msg, rx_ns)


def try_evaluate() -> None:
    """
    Phase 6: GPS-based matching.
    Find the best (thermal, imagery) pair within SYNC_THRESHOLD_MS by tx_ns.
    Skip if no pair within threshold, or if the best pair was already fused.
    Must be called with lock held.
    """
    global fusion_id, fire_signal_window

    if not thermal_buffer or not imagery_buffer:
        return

    threshold_ns = int(SYNC_THRESHOLD_MS * 1e6)

    # Find the pair with the smallest |tx_thermal - tx_imagery|
    best_t: Optional[Dict] = None
    best_i: Optional[Dict] = None
    best_dt_ns = threshold_ns + 1  # sentinel: larger than allowed threshold

    for t in thermal_buffer:
        t_tx = int(t.get("tx_ns", 0) or 0)
        for i_msg in imagery_buffer:
            i_tx = int(i_msg.get("tx_ns", 0) or 0)
            dt_ns = abs(t_tx - i_tx)
            if dt_ns < best_dt_ns:
                best_dt_ns = dt_ns
                best_t, best_i = t, i_msg

    # No pair found within threshold — wait for more data
    if best_t is None or best_dt_ns > threshold_ns:
        return

    # Don't re-fuse a pair we've already processed
    t_tx = int(best_t.get("tx_ns", 0) or 0)
    i_tx = int(best_i.get("tx_ns", 0) or 0)
    pair_key = (t_tx, i_tx)
    if pair_key in fused_pairs:
        return
    fused_pairs.append(pair_key)

    try:
        fusion_start_ns = utc_ns()

        t = best_t
        i = best_i

        # Fall back to rx_ns if tx_ns is missing/zero
        if t_tx <= 0:
            t_tx = int(t.get("_rx_ns", 0) or 0)
        if i_tx <= 0:
            i_tx = int(i.get("_rx_ns", 0) or 0)

        dt_s = abs(t_tx - i_tx) / 1e9

        max_temp, shape_str = safe_max_temp(t.get("data"))
        dets = i.get("detections", [])
        fire = imagery_has_fire(dets)

        # Phase 4: per-event raw signal
        raw_signal = (max_temp > TEMP_THRESHOLD) and fire and (dt_s <= TIME_WINDOW_S)
        fire_signal_window.append(raw_signal)
        confirmations = sum(fire_signal_window)
        decision = confirmations >= FIRE_CONFIRM_K
        window_fill = len(fire_signal_window)

        fusion_end_ns = utc_ns()

        # Phase 3: latency breakdown
        thermal_proc_ns = int(t.get("proc_ns", 0) or 0)
        imagery_proc_ns = int(i.get("proc_ns", 0) or 0)
        thermal_rx_ns = int(t.get("_rx_ns", 0) or 0)
        imagery_rx_ns = int(i.get("_rx_ns", 0) or 0)

        thermal_net_ns = int(max(0, thermal_rx_ns - t_tx))
        imagery_net_ns = int(max(0, imagery_rx_ns - i_tx))
        fusion_proc_ns = int(fusion_end_ns - fusion_start_ns)
        e2e_ns = int(fusion_end_ns - min(t_tx, i_tx))
        e2e_ms = e2e_ns / 1e6

        num_dets = len(dets) if isinstance(dets, list) else 0

        # Phase 5: drone distances
        thermal_dist = t.get("distance_m")
        imagery_dist = i.get("distance_m")

        # Ground-truth fire label: was the shared fire window active when
        # these messages were sent?  Both workers use the same clock-based
        # schedule so their values should agree; use thermal's as canonical.
        fire_window = bool(t.get("fire_window", False))

        # Hit/miss classification against ground truth
        # TP = fire_window AND decision  (correctly detected)
        # FN = fire_window AND NOT decision  (missed)
        # FP = NOT fire_window AND decision  (false alarm)
        # TN = NOT fire_window AND NOT decision  (correctly quiet)
        hit_miss = (
            "TP" if fire_window and decision else
            "FN" if fire_window and not decision else
            "FP" if not fire_window and decision else
            "TN"
        )

        print(
            f"[FUSION] dt={dt_s:.3f}s temp={max_temp:.1f} fire={fire} "
            f"shape={shape_str} raw={raw_signal} "
            f"window={confirmations}/{window_fill} decision={decision} "
            f"gt={'FIRE' if fire_window else 'none'} [{hit_miss}]"
        )

        iso = utc_iso(fusion_end_ns)
        csv_writer.writerow([
            fusion_id, iso, f"{dt_s:.6f}", f"{max_temp:.3f}", fire,
            raw_signal, confirmations, window_fill, decision,
            shape_str, num_dets,
            "" if thermal_dist is None else f"{thermal_dist:.1f}",
            "" if imagery_dist is None else f"{imagery_dist:.1f}",
            fire_window, hit_miss,
        ])
        logfile.flush()

        rec = {
            "fusion_id": fusion_id,
            "fusion_done_ns": fusion_end_ns,
            "fusion_done_iso": iso,
            "thermal_seq": t.get("seq"),
            "imagery_seq": i.get("seq"),
            "thermal_tx_ns": t_tx,
            "imagery_tx_ns": i_tx,
            "thermal_rx_ns": thermal_rx_ns,
            "imagery_rx_ns": imagery_rx_ns,
            "thermal_proc_ns": thermal_proc_ns,
            "imagery_proc_ns": imagery_proc_ns,
            "thermal_net_ns": thermal_net_ns,
            "imagery_net_ns": imagery_net_ns,
            "fusion_proc_ns": fusion_proc_ns,
            "dt_s": dt_s,
            "max_temp": max_temp,
            "imagery_fire": fire,
            "raw_signal": raw_signal,
            "window_confirmations": confirmations,
            "window_fill": window_fill,
            "fire_window_k": FIRE_WINDOW_K,
            "fire_confirm_k": FIRE_CONFIRM_K,
            "decision": decision,
            "thermal_shape": shape_str,
            "num_detections": num_dets,
            "e2e_ns": e2e_ns,
            "e2e_ms": e2e_ms,
            "thermal_distance_m": thermal_dist,
            "imagery_distance_m": imagery_dist,
            "fire_window": fire_window,
            "hit_miss": hit_miss,
        }
        with open(LATENCY_LOG_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

        fusion_id += 1

    except (KeyError, TypeError, ValueError) as e:
        print(f"[FUSION] Error: {e}")


def handle_thermal(conn: socket.socket) -> None:
    def on_msg(msg: Dict[str, Any], rx_ns: int):
        global first_thermal_seen_s
        now_s = rx_ns / 1e9
        msg["_rx_ns"] = rx_ns  # store receive time for latency calc
        with lock:
            if first_thermal_seen_s is None:
                first_thermal_seen_s = now_s
            thermal_buffer.append(msg)
            thermal_arrivals.append(now_s)
            prune_old(thermal_arrivals, now_s, MONITOR_WINDOW_S)
            try_evaluate()

    recv_lines(conn, on_msg)


def handle_imagery(conn: socket.socket) -> None:
    def on_msg(msg: Dict[str, Any], rx_ns: int):
        global first_imagery_seen_s
        now_s = rx_ns / 1e9
        msg["_rx_ns"] = rx_ns
        with lock:
            if first_imagery_seen_s is None:
                first_imagery_seen_s = now_s
            imagery_buffer.append(msg)
            imagery_arrivals.append(now_s)
            prune_old(imagery_arrivals, now_s, MONITOR_WINDOW_S)
            try_evaluate()

    recv_lines(conn, on_msg)


def server(port: int, handler, name: str) -> None:
    """Phase 2: TCP server. If bind fails, continue (controller keeps running)."""
    global thermal_server_up, imagery_server_up

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        s.bind((HOST, port))
    except OSError as e:
        print(f"[SERVER] {name} bind failed on {HOST}:{port}: {e}")
        print(f"[SERVER] {name} server will be DOWN, but controller will continue.")
        return

    s.listen(5)
    print(f"[SERVER] {name} listening on {HOST}:{port}")

    if name == "THERMAL":
        thermal_server_up = True
    if name == "IMAGERY":
        imagery_server_up = True

    while True:
        conn, addr = s.accept()
        print(f"[SERVER] {name} connection from {addr}")
        threading.Thread(target=handler, args=(conn,), daemon=True).start()


def _stream_drop_stats(
    stream_name: str,
    arrivals: deque,
    expected_hz: float,
    first_seen_s: Optional[float],
    now_s: float,
    window_s: float,
) -> Tuple[Optional[float], float, float]:
    if first_seen_s is None:
        return None, 0.0, 0.0
    effective_window = min(window_s, max(0.0, now_s - first_seen_s))
    if effective_window <= 0.0:
        return None, 0.0, 0.0
    prune_old(arrivals, now_s, window_s)
    recv_count = float(len(arrivals))
    expected_count = expected_hz * effective_window
    if expected_count <= 0:
        return None, recv_count, expected_count
    drop = clamp01(1.0 - (recv_count / expected_count))
    return drop, recv_count, expected_count


def monitoring_thread() -> None:
    """Phase 2: Every MONITOR_WINDOW_S seconds, check drop rates."""
    print("[MONITOR] Waiting 5s for workers to connect...")
    time.sleep(5)

    start_s = time.time()
    while True:
        time.sleep(MONITOR_WINDOW_S)
        now_s = time.time()
        up_s = now_s - start_s

        with lock:
            t_drop, t_recv, t_exp = _stream_drop_stats(
                "thermal", thermal_arrivals, EXPECTED_THERMAL_HZ,
                first_thermal_seen_s, now_s, MONITOR_WINDOW_S,
            )
            i_drop, i_recv, i_exp = _stream_drop_stats(
                "imagery", imagery_arrivals, EXPECTED_IMAGERY_HZ,
                first_imagery_seen_s, now_s, MONITOR_WINDOW_S,
            )

        def fmt(drop: Optional[float], recv: float, exp: float) -> str:
            if drop is None:
                return "not_started"
            return f"{recv:.0f}/{exp:.0f} drop={drop:.0%}"

        print(
            f"[MONITOR] up={up_s:.0f}s window={MONITOR_WINDOW_S:.0f}s "
            f"thermal={fmt(t_drop, t_recv, t_exp)} "
            f"imagery={fmt(i_drop, i_recv, i_exp)}"
        )

        if thermal_server_up and t_drop is not None and t_drop > DROP_STOP_THRESHOLD:
            print(f"[STOP] Thermal drop {t_drop:.0%} > {DROP_STOP_THRESHOLD:.0%}. Stopping.")
            logfile.close()
            sys.exit(2)

        if imagery_server_up and i_drop is not None and i_drop > DROP_STOP_THRESHOLD:
            print(f"[STOP] Imagery drop {i_drop:.0%} > {DROP_STOP_THRESHOLD:.0%}. Stopping.")
            logfile.close()
            sys.exit(2)


def main() -> None:
    global logfile, csv_writer, LATENCY_LOG_JSONL, FUSION_LOG_CSV, SYNC_THRESHOLD_MS

    parser = argparse.ArgumentParser(description="Wildfire Controller")
    parser.add_argument(
        "--outdir", default=".",
        help="Directory to write fusion_log.csv and latency_log.jsonl",
    )
    parser.add_argument(
        "--sync-threshold-ms", type=float, default=SYNC_THRESHOLD_MS,
        help=(
            "Max |tx_thermal - tx_imagery| (ms) to accept as a matched pair. "
            "Use a large value (e.g. 2000) to approximate the old 'most-recent' behaviour. "
            f"Default: {SYNC_THRESHOLD_MS}"
        ),
    )
    args = parser.parse_args()

    SYNC_THRESHOLD_MS = args.sync_threshold_ms

    if os.path.exists(args.outdir) and not os.path.isdir(args.outdir):
        os.remove(args.outdir)
    os.makedirs(args.outdir, exist_ok=True)
    FUSION_LOG_CSV = os.path.join(args.outdir, "fusion_log.csv")
    LATENCY_LOG_JSONL = os.path.join(args.outdir, "latency_log.jsonl")

    if THERMAL_PORT == IMAGERY_PORT:
        raise SystemExit("Thermal and imagery ports must be different.")

    print("[CTRL] Starting...")
    print(f"[CTRL] Output dir: {os.path.abspath(args.outdir)}")
    print(f"[CTRL] TEMP_THRESHOLD={TEMP_THRESHOLD}  TIME_WINDOW={TIME_WINDOW_S}s  "
          f"(pair dt must be <= {TIME_WINDOW_S}s AND <= {SYNC_THRESHOLD_MS}ms sync threshold)")
    print(f"[CTRL] Monitor: window={MONITOR_WINDOW_S}s  stop_if_drop>{DROP_STOP_THRESHOLD:.0%}")
    print(f"[CTRL] Expected rates: thermal={EXPECTED_THERMAL_HZ}Hz  imagery={EXPECTED_IMAGERY_HZ}Hz")
    print(f"[CTRL] Phase 4 rolling window: K={FIRE_WINDOW_K}  confirm={FIRE_CONFIRM_K}")
    print(f"[CTRL] GPS sync: buffer={SYNC_BUFFER_SIZE}  threshold={SYNC_THRESHOLD_MS}ms")

    logfile = open(FUSION_LOG_CSV, "w", newline="", encoding="utf-8")
    csv_writer = csv.writer(logfile)
    csv_writer.writerow([
        "fusion_id", "utc_iso", "dt_s", "max_temp", "imagery_fire",
        "raw_signal", "window_confirmations", "window_fill", "decision",
        "thermal_shape", "num_detections",
        "thermal_distance_m", "imagery_distance_m",
        "fire_window", "hit_miss",
    ])

    with open(LATENCY_LOG_JSONL, "w", encoding="utf-8") as f:
        f.write("")

    threading.Thread(target=server, args=(THERMAL_PORT, handle_thermal, "THERMAL"), daemon=True).start()
    threading.Thread(target=server, args=(IMAGERY_PORT, handle_imagery, "IMAGERY"), daemon=True).start()
    threading.Thread(target=monitoring_thread, daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logfile.close()


if __name__ == "__main__":
    main()
