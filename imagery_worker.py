"""
Wildfire Multi-Drone Simulation - Imagery Worker

Simulates camera/object-detection output with overlapping bounding boxes.
Sends variable detections (1-3 boxes) or empty list [] to simulate no-detection frames.
"""

import socket, json, time, random, sys

# --- Network ---
HOST = sys.argv[1]   # Controller IP (e.g. 10.0.0.1)
PORT = 5002

# --- Send behavior ---
DROP_PROB = 0.1      # Packet dropout probability
SEND_HZ = 2          # Send rate in Hz

# --- Detection variability ---
P_EMPTY = 0.2        # 20% of frames have no detections (realistic camera behavior)


def gen_detections():
    """
    Generate bounding box detections. 20% empty; otherwise 1-3 overlapping boxes.
    Overlap pattern: either x-varying (horizontal) or y-varying (vertical).
    """
    if random.random() < P_EMPTY:
        return []   # No detection - controller must handle gracefully

    n = random.choices([1, 2, 3], weights=[0.6, 0.3, 0.1])[0]

    base_x = random.randint(50, 250)
    base_y = random.randint(50, 250)

    dets = []
    for i in range(n):
        if i == 0:
            x, y = base_x, base_y
        else:
            # Overlapping boxes: slight offset from base
            if random.random() < 0.5:  # Overlap mostly in x (horizontal spread)
                x = base_x + random.randint(-30, 30)
                y = base_y + random.randint(-10, 10)
            else:  # Overlap mostly in y (vertical spread)
                x = base_x + random.randint(-10, 10)
                y = base_y + random.randint(-30, 30)

        w = random.randint(40, 80)
        h = random.randint(40, 80)
        label = "fire" if random.random() < 0.7 else random.choice(["smoke", "heat"])

        dets.append({
            "label": label,
            "confidence": round(random.uniform(0.6, 0.95), 3),
            "bbox": {"x": x, "y": y, "width": w, "height": h},
        })

    return dets


def main():
    """Connect to controller and stream imagery detections at SEND_HZ with DROP_PROB."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((HOST, PORT))

    period = 1.0 / SEND_HZ
    while True:
        if random.random() >= DROP_PROB:
            msg = {"timestamp": time.time(), "detections": gen_detections()}
            s.sendall((json.dumps(msg) + "\n").encode())
        time.sleep(period)


if __name__ == "__main__":
    main()