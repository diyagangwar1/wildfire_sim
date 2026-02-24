"""
Wildfire Multi-Drone Simulation — Ground Station Controller

Phase 1: Fusion logic (thermal max temp + imagery fire label + time window)
Phase 2: Port robustness, drop monitoring (rolling window), stop condition, threading + locks
Phase 3: GPS/UTC timestamps, latency breakdown, latency_log.jsonl

Listens on TCP 5001 (thermal) and TCP 5002 (imagery).
"""

from __future__ import annotations
import socket
import json
import sys
import threading
import time
import csv
import os
from collections import deque
from typing import Any, Dict, Optional, Tuple

from gps_time import utc_ns, utc_iso

THERMAL_PORT = 5001
IMAGERY_PORT = 5002
HOST = "0.0.0.0"

# Fusion constants
TEMP_THRESHOLD = 100.0
TIME_WINDOW_S = 2.0

# Phase 2: drop monitoring + stop condition
EXPECTED_THERMAL_HZ = 2.0
EXPECTED_IMAGERY_HZ = 2.0
MONITOR_WINDOW_S = 10.0
DROP_STOP_THRESHOLD = 0.50   # stop if drop rate > 50%

# Output logs
LATENCY_LOG_JSONL = "latency_log.jsonl"
FUSION_LOG_CSV = "fusion_log.csv"

# --- Phase 2: shared state (thread-safe with lock) ---
last_thermal: Optional[Dict[str, Any]] = None
last_imagery: Optional[Dict[str, Any]] = None
last_thermal_rx_ns: Optional[int] = None
last_imagery_rx_ns: Optional[int] = None
lock = threading.Lock()

# Phase 2: rolling-window packet arrival timestamps
thermal_arrivals: deque = deque()
imagery_arrivals: deque = deque()

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


def prune_old(arrivals: deque, now: float, window_s: float) -> None:
    """Remove timestamps older than window_s from arrivals deque."""
    cutoff = now - window_s
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


def recv_lines(conn, on_msg) -> None:
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

        print(f"[FUSION] dt={dt_s:.3f}s temp={max_temp:.1f} fire={fire} shape={shape_str} decision={decision}")

        # Phase 1/2 CSV
        iso = utc_iso(fusion_end_ns)
        csv_writer.writerow([
            fusion_id, iso, f"{dt_s:.6f}", f"{max_temp:.3f}", fire, decision, shape_str, num_dets
        ])
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


def handle_thermal(conn) -> None:
    """Phase 2: on each thermal msg, update shared state under lock, append to arrivals, evaluate."""

    def on_msg(msg, rx_ns: int):
        global last_thermal, last_thermal_rx_ns, thermal_arrivals
        now_s = rx_ns / 1e9
        with lock:
            last_thermal = msg
            last_thermal_rx_ns = rx_ns
            thermal_arrivals.append(now_s)
            prune_old(thermal_arrivals, now_s, MONITOR_WINDOW_S)
            evaluate()

    recv_lines(conn, on_msg)


def handle_imagery(conn) -> None:
    """Phase 2: on each imagery msg, update shared state under lock, append to arrivals, evaluate."""

    def on_msg(msg, rx_ns: int):
        global last_imagery, last_imagery_rx_ns, imagery_arrivals
        now_s = rx_ns / 1e9
        with lock:
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


def monitoring_thread() -> None:
    """
    Phase 2: Every MONITOR_WINDOW_S seconds, compute drop rate.
    If drop > 50% for either stream, stop simulation.
    """
    print("[MONITOR] Waiting 5s for workers to connect...")
    time.sleep(5)

    start = time.time()
    while True:
        time.sleep(MONITOR_WINDOW_S)

        now = time.time()
        with lock:
            prune_old(thermal_arrivals, now, MONITOR_WINDOW_S)
            prune_old(imagery_arrivals, now, MONITOR_WINDOW_S)

            thermal_recv = len(thermal_arrivals)
            imagery_recv = len(imagery_arrivals)

        thermal_expected = EXPECTED_THERMAL_HZ * MONITOR_WINDOW_S
        imagery_expected = EXPECTED_IMAGERY_HZ * MONITOR_WINDOW_S

        thermal_drop = clamp01(1.0 - (thermal_recv / thermal_expected if thermal_expected > 0 else 0.0))
        imagery_drop = clamp01(1.0 - (imagery_recv / imagery_expected if imagery_expected > 0 else 0.0))

        up = time.time() - start
        print(f"[MONITOR] up={up:.0f}s window={MONITOR_WINDOW_S:.0f}s "
              f"thermal_recv={thermal_recv:.0f}/{thermal_expected:.0f} drop={thermal_drop:.0%} "
              f"imagery_recv={imagery_recv:.0f}/{imagery_expected:.0f} drop={imagery_drop:.0%}")

        if thermal_server_up and thermal_drop > DROP_STOP_THRESHOLD:
            print(f"[STOP] Thermal drop rate {thermal_drop:.0%} exceeded {DROP_STOP_THRESHOLD:.0%}. Stopping simulation.")
            logfile.close()
            os._exit(2)

        if imagery_server_up and imagery_drop > DROP_STOP_THRESHOLD:
            print(f"[STOP] Imagery drop rate {imagery_drop:.0%} exceeded {DROP_STOP_THRESHOLD:.0%}. Stopping simulation.")
            logfile.close()
            os._exit(2)


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
    csv_writer.writerow([
        "fusion_id", "utc_iso", "dt_s", "max_temp", "imagery_fire", "decision",
        "thermal_shape", "num_detections"
    ])

    # Phase 3 JSONL
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
