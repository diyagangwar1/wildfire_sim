# Wildfire Multi-Drone Simulation

Evaluate multi-drone communication architectures under stress conditions (latency, packet loss, dropouts) to determine which structure minimizes latency and maximizes robustness.

---

## Architecture

```
                    ┌─────────────┐
                    │     s1      │
                    │  (switch)   │
                    └─────┬─────--┘
            ┌─────────────┼─────────────┐
            │             │             │
       ┌────┴─── ─┐   ┌────┴────┐   ┌────┴────┐
       │   h1     │   │   h2    │   │   h3    │
       │controller│   │ thermal │   │ imagery │
       │          │   │ worker  │   │ worker  │
       └──────── ─┘   └─────────┘   └─────────┘
```

- **h1**: Fusion controller — receives thermal + imagery streams, fuses with temporal alignment
- **h2**: Thermal worker — simulates thermal sensor (variable 1D/2D data)
- **h3**: Imagery worker — simulates camera detections (bounding boxes, empty frames)

---

## Requirements

- Python 3.6+
- Mininet (`sudo apt install mininet` or equivalent)
- No external Python packages beyond stdlib

---

## How to Run

### 1. Start Mininet topology

```bash
cd wildfire_sim
sudo python3 mn_topo.py
```

### 2. In Mininet CLI, start the controller on h1

```bash
mininet> h1 python3 controller.py &
```

### 3. Start thermal worker on h2

```bash
mininet> h2 python3 thermal_worker.py 10.0.0.1 &
```

### 4. Start imagery worker on h3

```bash
mininet> h3 python3 imagery_worker.py 10.0.0.1 &
```

### 5. Monitor output

```bash
mininet> h1 tail -f fusion_log.csv
```

---

## Output Files

| File | Description |
|------|-------------|
| `fusion_log.csv` | Main fusion results |

**CSV columns:**

| Column | Description |
|--------|-------------|
| `recv_time` | Controller reception timestamp |
| `thermal_ts` | Thermal worker timestamp |
| `imagery_ts` | Imagery worker timestamp |
| `delta_t` | Time difference between streams |
| `max_temp` | Maximum temperature reading (°C) |
| `fire_detected` | Boolean — any "fire" label in imagery |
| `num_detections` | Count of bounding boxes |
| `thermal_shape` | Data dimensions (e.g. `4x4`, `8x1`) |
| `decision` | Final fusion decision (fire iff thermal + imagery + sync) |

---

## Phase 1 Improvements (Complete)

### Thermal Worker

- **Probabilistic fire model**: `p > 0.6` triggers temperature boost (not binary)
- **Variable grid sizes**: 2×2, 3×3, 4×4, 2×4
- **1D arrays**: 30% of packets are 1D (lengths 2, 4, 8, 15)

### Imagery Worker

- **Overlapping bounding boxes**: Horizontal/vertical overlap patterns
- **Empty detections**: 20% of frames send `[]` — controller handles gracefully

### Controller

- **Variable-shaped thermal**: Handles both 1D and 2D data
- **Try/catch**: Graceful handling of malformed/empty data
- **Port safety**: Validates thermal ≠ imagery port
- **Packet rate monitoring**: Prints thermal/imagery counts every 10s

---

## Configuration

### thermal_worker.py

| Parameter | Default | Description |
|-----------|---------|-------------|
| `DROP_PROB` | 0.1 | Packet dropout probability |
| `SEND_HZ` | 2 | Send rate (Hz) |
| `FIRE_P_THRESHOLD` | 0.6 | Probability threshold for fire-like frame |
| `FIRE_INFLATION` | 10.0 | °C added in fire mode |
| `P_SEND_1D` | 0.3 | Fraction of 1D vs 2D packets |

### imagery_worker.py

| Parameter | Default | Description |
|-----------|---------|-------------|
| `DROP_PROB` | 0.1 | Packet dropout probability |
| `SEND_HZ` | 2 | Send rate (Hz) |
| `P_EMPTY` | 0.2 | Probability of no detections |

### controller.py

| Parameter | Default | Description |
|-----------|---------|-------------|
| `TEMP_THRESHOLD` | 100.0 | °C for fire decision |
| `TIME_WINDOW` | 2.0 | Max skew (s) between thermal/imagery |
| `THERMAL_PORT` | 5001 | Thermal stream port |
| `IMAGERY_PORT` | 5002 | Imagery stream port |

### mn_topo.py

| Link | Default | Notes |
|------|---------|------|
| h2–s1 | delay=0ms, loss=0 | Set for Phase 3 latency experiments |
| h3–s1 | delay=0ms, loss=0 | Asymmetric delays possible |

---

## Quick Tests

```bash
# Variable temperatures (not just 70 vs 130)
mininet> h1 tail -f fusion_log.csv | grep max_temp

# Variable shapes (2x2, 4x4, 8x1, etc.)
mininet> h1 tail -f fusion_log.csv | grep thermal_shape

# Empty detections (~20% with num_detections=0)
mininet> h1 tail -f fusion_log.csv | grep num_detections
```

---

## Next Steps (Phase 2–5)

- **Phase 2**: Port failure recovery, drop rate monitoring, auto-shutdown if drop > 50%
- **Phase 3** (highest priority): GPS/UTC timestamps, end-to-end latency, latency breakdown, visualization
- **Phase 4**: Rolling window thresholding, smoothing/filtering
- **Phase 5**: Distance-based drop probability

---

## File Structure

```
wildfire_sim/
├── README.md           # This file
├── mn_topo.py          # Mininet topology (h1, h2, h3, s1)
├── controller.py       # Fusion controller
├── thermal_worker.py   # Thermal sensor simulator
├── imagery_worker.py   # Imagery detector simulator
├── fusion_log.csv      # Generated at runtime
└── results/
    ├── README.md       # Experiment documentation
    ├── baseline/       # No delay, no loss
    ├── delay_tests/    # Link delay experiments
    └── dropout_tests/  # Packet loss experiments
```

---

## For Professor

Phase 1 focused on **realism without changing architecture**:

- Sensor outputs match real-world variability (variable shapes, empty detections)
- System is robust to edge cases (try/catch, graceful degradation)
- Foundation is ready for latency analysis (Phase 3)

The parallel architecture baseline is production-ready for stress testing.
