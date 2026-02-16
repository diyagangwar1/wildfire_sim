"""
Wildfire Multi-Drone Simulation - Fusion Controller

Listens for thermal and imagery streams from drone workers, fuses data using
temporal alignment (TIME_WINDOW), and logs fire detection decisions.
Handles variable-shaped thermal data (1D/2D) and empty imagery detections.
"""

import socket, json, threading, time, csv

# --- Network configuration ---
THERMAL_PORT = 5001
IMAGERY_PORT = 5002
HOST = "0.0.0.0"

# --- Fusion parameters ---
TEMP_THRESHOLD = 100.0   # °C - thermal must exceed this for fire
TIME_WINDOW = 2.0       # seconds - max allowed skew between thermal/imagery

# --- Shared state (thread-safe) ---
last_thermal = None
last_imagery = None
lock = threading.Lock()

# --- CSV logging ---
logfile = open("fusion_log.csv", "w", newline="")
w = csv.writer(logfile)
w.writerow([
    "recv_time", "thermal_ts", "imagery_ts", "delta_t",
    "max_temp", "fire_detected", "num_detections", "thermal_shape", "decision"
])

# --- Packet rate monitoring ---
_packet_counts = {"thermal": 0, "imagery": 0}
_last_rate_check = time.time()
RATE_CHECK_INTERVAL = 10.0


def max_temp_from_grid(g):
    """
    Extract maximum temperature from variable-shaped thermal data.
    Supports both 1D arrays and 2D grids; returns -inf for empty/invalid input.
    """
    if not isinstance(g, list) or not g:
        return float("-inf")
    if isinstance(g[0], list):  # 2D grid
        m = float("-inf")
        for row in g:
            if row:
                m = max(m, max(row))
        return m
    return max(g)  # 1D array


def _format_shape(shape):
    """Format shape list [r, c] as 'RxC' string (e.g. '4x4', '8x1')."""
    if not shape or len(shape) < 2:
        return "?"
    return f"{shape[0]}x{shape[1]}"


def _check_packet_rates():
    """Print packet rates every RATE_CHECK_INTERVAL seconds."""
    global _packet_counts, _last_rate_check
    now = time.time()
    if now - _last_rate_check >= RATE_CHECK_INTERVAL:
        print(f"[RATE] thermal={_packet_counts['thermal']} imagery={_packet_counts['imagery']} (last {RATE_CHECK_INTERVAL:.0f}s)")
        _packet_counts["thermal"] = 0
        _packet_counts["imagery"] = 0
        _last_rate_check = now


def evaluate():
    """
    Fuse latest thermal and imagery data. Fire decision requires:
    - max_temp > TEMP_THRESHOLD
    - at least one 'fire' detection in imagery
    - timestamps within TIME_WINDOW
    """
    global last_thermal, last_imagery
    if not last_thermal or not last_imagery:
        return

    try:
        _evaluate_inner()
    except (KeyError, TypeError, ValueError) as e:
        print(f"[FUSION] Error processing data: {e}")


def _evaluate_inner():
    """Inner fusion logic; called from evaluate() with try/except."""
    global last_thermal, last_imagery
    dt = abs(last_thermal["timestamp"] - last_imagery["timestamp"])
    max_temp = max_temp_from_grid(last_thermal.get("grid", []))
    thermal_ok = max_temp > TEMP_THRESHOLD

    # Empty detections [] are handled: any([]) -> False, len([]) -> 0
    dets = last_imagery.get("detections", [])
    fire_detected = any(isinstance(d, dict) and d.get("label") == "fire" for d in dets)
    imagery_ok = fire_detected

    decision = thermal_ok and imagery_ok and dt <= TIME_WINDOW

    recv_time = time.time()
    shape_str = _format_shape(last_thermal.get("shape", ["?", "?"]))

    w.writerow([
        recv_time,
        last_thermal["timestamp"],
        last_imagery["timestamp"],
        dt,
        max_temp,
        fire_detected,
        len(dets) if isinstance(dets, list) else 0,
        shape_str,
        decision,
    ])
    logfile.flush()

    print(f"[FUSION] dt={dt:.3f}s temp={max_temp:.1f} fire={fire_detected} shape={shape_str} decision={decision}")


def recv_lines(conn, on_msg):
    """
    Read newline-delimited JSON messages from socket.
    Ignores malformed lines; calls on_msg for each valid JSON object.
    """
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
            on_msg(msg)


def handle_thermal(conn):
    def on_msg(msg):
        global last_thermal, _packet_counts
        with lock:
            last_thermal = msg
            _packet_counts["thermal"] += 1
            _check_packet_rates()
            evaluate()
    recv_lines(conn, on_msg)


def handle_imagery(conn):
    def on_msg(msg):
        global last_imagery, _packet_counts
        with lock:
            last_imagery = msg
            _packet_counts["imagery"] += 1
            _check_packet_rates()
            evaluate()
    recv_lines(conn, on_msg)


def server(port, handler):
    """Run TCP server on given port; spawns a thread per connection."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((HOST, port))
    s.listen(5)
    print(f"[SERVER] Listening on {HOST}:{port}")
    while True:
        conn, addr = s.accept()
        print(f"[SERVER] Connection from {addr} on port {port}")
        threading.Thread(target=handler, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    # Port safety: prevent misconfiguration
    if THERMAL_PORT == IMAGERY_PORT:
        raise SystemExit("Thermal and imagery ports must be different.")

    threading.Thread(target=server, args=(THERMAL_PORT, handle_thermal), daemon=True).start()
    threading.Thread(target=server, args=(IMAGERY_PORT, handle_imagery), daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logfile.close()