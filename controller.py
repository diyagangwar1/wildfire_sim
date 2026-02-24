"""
Wildfire Multi-Drone Simulation — Ground Station Controller

Phase 1: Fusion logic (thermal max temp + imagery fire label + time window)
Phase 2: Port robustness, drop monitoring (rolling window), stop condition, threading + locks
Phase 3: GPS/UTC timestamps, latency breakdown, latency_log.jsonl

Listens on TCP 5001 (thermal) and TCP 5002 (imagery).
"""

from __future__ import annotations

import csv
import json
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
TIME_WINDOW_S = 2.0

# Phase 2: drop monitoring + stop condition
EXPECTED_THERMAL_HZ = 2.0
EXPECTED_IMAGERY_HZ = 2.0
MONITOR_WINDOW_S = 10.0
DROP_STOP_THRESHOLD = 0.50  # stop if drop rate > 50%

# Output logs
LATENCY_LOG_JSONL = "latency_log.jsonl"
FUSION_LOG_CSV = "fusion_log.csv"

# --- Phase 2: shared state (thread-safe with lock) ---
last_thermal: Optional[Dict[str, Any]] = None
last_imagery: Optional[Dict[str, Any]] = None
last_thermal_rx_ns: Optional[int] = None
last_imagery_rx_ns: Optional[int] = None
lock = threading.Lock()

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

# CSV file handle (opened at startup)
logfile = None
csv_writer = None


def clamp01(x: float) -> float:
    """Clamp value to [0, 1] for drop rate."""
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

    # 2D list
    if isinstance(thermal_data, list) and thermal_data and isinstance(thermal_data[0], list):
        r = len(thermal_data)
        c = len(thermal_data[0]) if r > 0 else 0
        m = max(max(row) for row in thermal_data if row)
        return float(m), f"{r}x{c}"

    # 1D list
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


def evaluate() -> None:
    """
    Fuse latest thermal + imagery. Phase 1 logic + Phase 3 latency logging.
    Must be called with lock held.
    """
    global last_thermal, last_imagery, last_thermal_rx_ns, last_imagery_rx_ns, fusion_id

    if not last_thermal or not last_imagery:
        return

    try:
        fusion_start_ns = utc_ns()

        t = last_thermal
        i = last_imagery

        # Use GPS/UTC tx_ns if provided; otherwise fall back to rx time
        t_tx = int(t.get("tx_ns", 0) or 0)
        i_tx = int(i.get("tx_ns", 0) or 0)
        if t_tx <= 0:
            t_tx = last_thermal_rx_ns or 0
        if i_tx <= 0:
            i_tx = last_imagery_rx_ns or 0

        dt_s = abs(t_tx - i_tx) / 1e9

        max_temp, shape_str = safe_max_temp(t.get("data"))
        dets = i.get("detections", [])
        fire = imagery_has_fire(dets)

        decision = (max_temp > TEMP_THRESHOLD) and fire and (dt_s <= TIME_WINDOW_S)

        fusion_end_ns = utc_ns()

        # Phase 3 breakdown
        thermal_proc_ns = int(t.get("proc_ns", 0) or 0)
        imagery_proc_ns = int(i.get("proc_ns", 0) or 0)
        thermal_rx_ns = last_thermal_rx_ns or 0
        imagery_rx_ns = last_imagery_rx_ns or 0

        thermal_net_ns = int(max(0, thermal_rx_ns - t_tx))
        imagery_net_ns = int(max(0, imagery_rx_ns - i_tx))
        fusion_proc_ns = int(fusion_end_ns - fusion_start_ns)
        e2e_ns = int(fusion_end_ns - min(t_tx, i_tx))
        e2e_ms = e2e_ns / 1e6

        num_dets = len(dets) if isinstance(dets, list) else 0

        print(
            f"[FUSION] dt={dt_s:.3f}s temp={max_temp:.1f} fire={fire} "
            f"shape={shape_str} decision={decision}"
        )

        # Phase 1/2 CSV
        iso = utc_iso(fusion_end_ns)
        csv_writer.writerow(
            [fusion_id, iso, f"{dt_s:.6f}", f"{max_temp:.3f}", fire, decision, shape_str, num_dets]
        )
        logfile.flush()

        # Phase 3 JSONL
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
            "decision": decision,
            "thermal_shape": shape_str,
            "num_detections": num_dets,
            "e2e_ns": e2e_ns,
            "e2e_ms": e2e_ms,
        }
        with open(LATENCY_LOG_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

        fusion_id += 1

    except (KeyError, TypeError, ValueError) as e:
        print(f"[FUSION] Error processing data: {e}")


def handle_thermal(conn: socket.socket) -> None:
    """Phase 2: update shared state under lock, append to arrivals, evaluate."""

    def on_msg(msg: Dict[str, Any], rx_ns: int):
        global last_thermal, last_thermal_rx_ns, thermal_arrivals, first_thermal_seen_s
        now_s = rx_ns / 1e9
        with lock:
            if first_thermal_seen_s is None:
                first_thermal_seen_s = now_s
            last_thermal = msg
            last_thermal_rx_ns = rx_ns
            thermal_arrivals.append(now_s)
            prune_old(thermal_arrivals, now_s, MONITOR_WINDOW_S)
            evaluate()

    recv_lines(conn, on_msg)


def handle_imagery(conn: socket.socket) -> None:
    """Phase 2: update shared state under lock, append to arrivals, evaluate."""

    def on_msg(msg: Dict[str, Any], rx_ns: int):
        global last_imagery, last_imagery_rx_ns, imagery_arrivals, first_imagery_seen_s
        now_s = rx_ns / 1e9
        with lock:
            if first_imagery_seen_s is None:
                first_imagery_seen_s = now_s
            last_imagery = msg
            last_imagery_rx_ns = rx_ns
            imagery_arrivals.append(now_s)
            prune_old(imagery_arrivals, now_s, MONITOR_WINDOW_S)
            evaluate()

    recv_lines(conn, on_msg)


def server(port: int, handler, name: str) -> None:
    """
    Phase 2: Run TCP server. If bind fails, print warning and return (controller continues).
    Spawns a thread per connection.
    """
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
    """
    Returns:
      drop_rate (None if stream not started), recv_count, expected_count
    Uses an "effective window" so startup doesn't look like 100% drop.
    """
    if first_seen_s is None:
        return None, 0.0, 0.0

    effective_window = min(window_s, max(0.0, now_s - first_seen_s))
    if effective_window <= 0.0:
        return None, 0.0, 0.0

    # Make sure deque only contains last window_s worth of data
    prune_old(arrivals, now_s, window_s)

    recv_count = float(len(arrivals))
    expected_count = expected_hz * effective_window
    if expected_count <= 0:
        return None, recv_count, expected_count

    drop = clamp01(1.0 - (recv_count / expected_count))
    return drop, recv_count, expected_count


def monitoring_thread() -> None:
    """
    Phase 2: Every MONITOR_WINDOW_S seconds, compute drop rate.
    Fix: do NOT enforce stop until a stream has actually started (seen at least 1 packet).
    Fix: expected packets based on effective window since first packet, to avoid startup false positives.
    """
    print("[MONITOR] Waiting 5s for workers to connect...")
    time.sleep(5)

    start_s = time.time()
    while True:
        time.sleep(MONITOR_WINDOW_S)

        now_s = time.time()
        up_s = now_s - start_s

        with lock:
            t_drop, t_recv, t_exp = _stream_drop_stats(
                "thermal", thermal_arrivals, EXPECTED_THERMAL_HZ, first_thermal_seen_s, now_s, MONITOR_WINDOW_S
            )
            i_drop, i_recv, i_exp = _stream_drop_stats(
                "imagery", imagery_arrivals, EXPECTED_IMAGERY_HZ, first_imagery_seen_s, now_s, MONITOR_WINDOW_S
            )

        # Print readable monitor line
        def fmt(drop: Optional[float], recv: float, exp: float) -> str:
            if drop is None:
                return "not_started"
            return f"{recv:.0f}/{exp:.0f} drop={drop:.0%}"

        print(
            f"[MONITOR] up={up_s:.0f}s window={MONITOR_WINDOW_S:.0f}s "
            f"thermal={fmt(t_drop, t_recv, t_exp)} "
            f"imagery={fmt(i_drop, i_recv, i_exp)}"
        )

        # Enforce stop ONLY if the server is up AND the stream has started
        if thermal_server_up and t_drop is not None and t_drop > DROP_STOP_THRESHOLD:
            print(
                f"[STOP] Thermal drop rate {t_drop:.0%} exceeded {DROP_STOP_THRESHOLD:.0%}. "
                "Stopping simulation."
            )
            logfile.close()
            sys.exit(2)

        if imagery_server_up and i_drop is not None and i_drop > DROP_STOP_THRESHOLD:
            print(
                f"[STOP] Imagery drop rate {i_drop:.0%} exceeded {DROP_STOP_THRESHOLD:.0%}. "
                "Stopping simulation."
            )
            logfile.close()
            sys.exit(2)


def main() -> None:
    global logfile, csv_writer

    if THERMAL_PORT == IMAGERY_PORT:
        raise SystemExit("Thermal and imagery ports must be different.")

    print("[CTRL] Starting...")
    print(f"[CTRL] TEMP_THRESHOLD={TEMP_THRESHOLD} TIME_WINDOW={TIME_WINDOW_S}s")
    print(f"[CTRL] Monitor: window={MONITOR_WINDOW_S}s stop_if_drop>{DROP_STOP_THRESHOLD:.0%}")
    print(f"[CTRL] Expected rates: thermal={EXPECTED_THERMAL_HZ}Hz imagery={EXPECTED_IMAGERY_HZ}Hz")

    # Phase 1/2 CSV
    logfile = open(FUSION_LOG_CSV, "w", newline="", encoding="utf-8")
    csv_writer = csv.writer(logfile)
    csv_writer.writerow(
        ["fusion_id", "utc_iso", "dt_s", "max_temp", "imagery_fire", "decision", "thermal_shape", "num_detections"]
    )

    # Phase 3 JSONL: clear file at startup
    with open(LATENCY_LOG_JSONL, "w", encoding="utf-8") as f:
        f.write("")

    # Phase 2: servers in daemon threads
    threading.Thread(target=server, args=(THERMAL_PORT, handle_thermal, "THERMAL"), daemon=True).start()
    threading.Thread(target=server, args=(IMAGERY_PORT, handle_imagery, "IMAGERY"), daemon=True).start()

    # Phase 2: monitoring thread
    threading.Thread(target=monitoring_thread, daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logfile.close()


if __name__ == "__main__":
    main()