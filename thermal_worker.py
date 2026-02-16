"""
Wildfire Multi-Drone Simulation - Thermal Worker

Simulates thermal sensor output with probabilistic fire generation.
Sends variable-shaped data (2D grids or 1D arrays) to the fusion controller.
"""

import socket, json, time, random, sys

# --- Network ---
HOST = sys.argv[1]   # Controller IP (e.g. 10.0.0.1)
PORT = 5001

# --- Send behavior ---
DROP_PROB = 0.1      # Packet dropout probability (simulates link loss)
SEND_HZ = 2          # Send rate in Hz

# --- Temperature model (Gaussian baseline) ---
BASE_MEAN = 70.0     # °C - normal ambient
BASE_STD = 2.0       # Variance

# --- Probabilistic fire model (p > threshold => fire-like frame) ---
FIRE_P_THRESHOLD = 0.6   # If random() > 0.6, inflate temps
FIRE_INFLATION = 10.0    # °C added to all cells in fire mode
HOTSPOT_MEAN = 130.0     # °C - localized hotspots (exceeds controller threshold)
HOTSPOT_STD = 3.0

# --- Variable data sizes (simulates real sensor output) ---
SHAPES_2D = [(2, 2), (4, 4), (3, 3), (2, 4)]   # 2D grid dimensions
LENS_1D = [2, 4, 8, 15]                         # 1D array lengths
P_SEND_1D = 0.3                                 # 30% of packets are 1D


def gen_thermal():
    """
    Generate one thermal frame. Probabilistic fire model: p > 0.6 => fire-like.
    Returns variable-shaped data: 30% 1D arrays, 70% 2D grids.
    """
    p = random.random()
    fire_like = p > FIRE_P_THRESHOLD

    # 1D output sometimes (irregular sizes - simulates real sensor variability)
    if random.random() < P_SEND_1D:
        n = random.choice(LENS_1D)
        arr = [random.gauss(BASE_MEAN, BASE_STD) for _ in range(n)]

        if fire_like:
            arr = [x + FIRE_INFLATION for x in arr]
            k = min(2, n)
            start = random.randint(0, n - k)
            for i in range(start, start + k):
                arr[i] = random.gauss(HOTSPOT_MEAN, HOTSPOT_STD)

        return {"timestamp": time.time(), "shape": [n, 1], "grid": arr}

    # 2D grid output otherwise
    r, c = random.choice(SHAPES_2D)
    grid = [[random.gauss(BASE_MEAN, BASE_STD) for _ in range(c)] for _ in range(r)]

    if fire_like:
        # Global inflation + localized hotspots
        for i in range(r):
            for j in range(c):
                grid[i][j] += FIRE_INFLATION
        pr = 1 if r == 1 else min(2, r)
        pc = 1 if c == 1 else min(2, c)
        r0 = random.randint(0, r - pr)
        c0 = random.randint(0, c - pc)
        for i in range(r0, r0 + pr):
            for j in range(c0, c0 + pc):
                grid[i][j] = random.gauss(HOTSPOT_MEAN, HOTSPOT_STD)

    return {"timestamp": time.time(), "shape": [r, c], "grid": grid}


def main():
    """Connect to controller and stream thermal data at SEND_HZ with DROP_PROB."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((HOST, PORT))

    period = 1.0 / SEND_HZ
    while True:
        if random.random() >= DROP_PROB:
            msg = gen_thermal()
            s.sendall((json.dumps(msg) + "\n").encode())
        time.sleep(period)


if __name__ == "__main__":
    main()