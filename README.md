# Wildfire Multi-Drone Simulation (Phases 1–3 Integrated)

Evaluate multi-drone communication architectures under stress conditions (latency, packet loss, dropouts) to determine which structure minimizes latency and maximizes robustness.

---

## Architecture

```
                    ┌─────────────┐
                    │     s1      │
                    │  (switch)   │
                    └─────┬───────┘
            ┌─────────────┼─────────────┐
            │             │             │
       ┌────┴────┐   ┌────┴────┐   ┌────┴────┐
       │   h1    │   │   h2    │   │   h3    │
       │controller│   │ thermal │   │ imagery │
       │         │   │ worker  │   │ worker  │
       └─────────┘   └─────────┘   └─────────┘
```

- **h1**: Fusion controller — receives thermal + imagery streams, fuses with temporal alignment
- **h2**: Thermal worker — simulates thermal sensor (variable 1D/2D data)
- **h3**: Imagery worker — simulates camera detections (bounding boxes, empty frames)

---

## Requirements

- Python 3.6+
- Mininet (`sudo apt install mininet` or equivalent)
- **matplotlib** (for `analyze_latency.py`): `pip install matplotlib`

---

## Ports

| Port | Stream |
|------|--------|
| TCP 5001 | Thermal worker → controller |
| TCP 5002 | Imagery worker → controller |

---

## Fusion Rule (Professor spec)

1. Thermal max temperature > 100°C
2. Imagery has at least one detection with label `"fire"`
3. Thermal and imagery timestamps within 2 seconds

---

## Phase 3 Timing (GPS/UTC)

We log UTC epoch timestamps in nanoseconds (`tx_ns`, `rx_ns`, `fusion_done_ns`). On real drones, replace this with GPS receiver time; format remains the same.

---

## How to Run

### 1. Start Mininet with asymmetric delays (Phase 3)

```bash
cd wildfire_sim
sudo python3 mn_topo.py --thermal-delay 20 --imagery-delay 80 --thermal-loss 0 --imagery-loss 1
```

Or baseline (no delay):

```bash
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

Let it run ~60s, then Ctrl+C processes as needed.

---

## Output Files

| File | Description |
|------|-------------|
| `fusion_log.csv` | Fusion decisions (Phase 1/2 style) |
| `latency_log.jsonl` | Full Phase 3 timing breakdown per fusion event |

**fusion_log.csv columns:**

| Column | Description |
|--------|-------------|
| `fusion_id` | Fusion event index |
| `utc_iso` | ISO-8601 UTC timestamp |
| `dt_s` | Time difference between streams |
| `max_temp` | Maximum temperature (°C) |
| `imagery_fire` | Boolean — any "fire" label |
| `raw_signal` | Phase 4: single-timestep fire check |
| `window_confirmations` | Phase 4: count of positive signals in window |
| `window_fill` | Phase 4: current window size |
| `decision` | Final fusion decision (rolling-window) |
| `thermal_shape` | Data dimensions (e.g. `4x4`, `1d:8`) |
| `num_detections` | Count of bounding boxes |
| `thermal_distance_m` | Phase 5: thermal drone distance (m) |
| `imagery_distance_m` | Phase 5: imagery drone distance (m) |

**latency_log.jsonl** (one JSON object per line) includes:

- `thermal_proc_ns`, `imagery_proc_ns` — worker processing time
- `thermal_net_ns`, `imagery_net_ns` — network delay (rx - tx)
- `fusion_proc_ns` — controller fusion time
- `e2e_ns`, `e2e_ms` — end-to-end latency

---

## Latency Analysis

```bash
python3 analyze_latency.py latency_log.jsonl --outdir plots
```

Generates:

- `plots/latency_contribution_pie.png` — mean % contribution (thermal proc, thermal net, imagery proc, imagery net, fusion proc)
- `plots/e2e_latency_timeseries.png` — E2E latency over fusion events
- `plots/phase4_rolling_window.png` — raw signal vs rolling decision, confirmations vs threshold
- `plots/phase5_drone_distance.png` — both drones' distance over time
- `plots/phase5_e2e_vs_distance.png` — E2E latency vs drone distance scatter

---

## Phase Summary

| Phase | Features |
|-------|----------|
| **Phase 1** | Probabilistic fire, variable shapes (2D + 1D), empty detections, overlapping boxes |
| **Phase 2** | **Controller**: Port robustness (continue if one bind fails), rolling-window drop monitoring, stop if drop > 50%. **Workers**: Dropout simulation, reconnect loop |
| **Phase 3** | GPS/UTC timestamps, latency breakdown, `latency_log.jsonl`, asymmetric delay/loss in topology |
| **Phase 4** | Rolling window thresholding — fire decision requires K confirmations in last K events |
| **Phase 5** | Distance-based drop probability — drones random-walk in 3D, drop_prob ∝ distance |

### Phase 2 Controller Features (restored)

- **Threading**: Separate threads for thermal server, imagery server, per-connection handlers, and monitoring
- **Locks**: `threading.Lock()` protects shared state (`last_thermal`, `last_imagery`, `thermal_arrivals`, `imagery_arrivals`)
- **Deques**: `thermal_arrivals` and `imagery_arrivals` store packet timestamps for rolling-window drop calculation
- **Port robustness**: If thermal or imagery bind fails, controller continues with the other stream
- **Drop monitoring**: Rolling 10s window; tracks received vs expected packets per stream; prints `[MONITOR]` every 10s
- **Stop condition**: If drop rate > 50% for either stream, prints `[STOP]` and exits with code 2
- **Testing**: Set `DROP_PROB = 0.9` in a worker to trigger stop within ~10s

---

## mn_topo.py Options

```bash
sudo python3 mn_topo.py --help
```

| Option | Default | Description |
|--------|---------|-------------|
| `--thermal-delay` | 0 | Delay (ms) on thermal link |
| `--imagery-delay` | 0 | Delay (ms) on imagery link |
| `--thermal-loss` | 0.0 | Loss (%) on thermal link |
| `--imagery-loss` | 0.0 | Loss (%) on imagery link |
| `--bw` | 100.0 | Bandwidth (Mbps) for all links |

**Example:** Asymmetric delay + imagery loss:

```bash
sudo python3 mn_topo.py --thermal-delay 20 --imagery-delay 80 --thermal-loss 0 --imagery-loss 1
```

---

## File Structure

```
wildfire_sim/
├── README.md           # This file
├── gps_time.py        # UTC/GPS-style timestamp helpers
├── mn_topo.py         # Mininet topology (asymmetric delay/loss)
├── controller.py      # Fusion controller + latency logging
├── thermal_worker.py  # Thermal stream (Phase 1–3)
├── imagery_worker.py  # Imagery stream (Phase 1–3)
├── analyze_latency.py # Latency analysis & plots
├── fusion_log.csv    # Generated at runtime
├── latency_log.jsonl # Generated at runtime
└── results/
    ├── README.md
    ├── baseline/
    ├── delay_tests/
    └── dropout_tests/
```

---

## Phase 4 & 5 (Implemented)

- **Phase 4**: Rolling window thresholding (`FIRE_WINDOW_K=5`, `FIRE_CONFIRM_K=3`)
- **Phase 5**: Distance-based drop probability (drone 3D random-walk, `drop_prob = BASE + SLOPE × distance`)

**Phase 6 experiments to run:**

- Sweep asymmetric delay values and collect multiple runs
- Sweep send rate (`SEND_HZ` in workers) and Mininet loss
- Extend `analyze_latency.py` to plot latency vs (delay, send rate, loss) across runs

---

## For Professor

- **Phase 1**: Realism — variable shapes, empty detections, overlapping boxes
- **Phase 2**: Robustness — threading + locks + deques, port robustness, drop monitoring, stop condition; workers: dropouts, reconnect loop
- **Phase 3**: GPS/UTC timestamps, end-to-end latency, component breakdown, `latency_log.jsonl`, asymmetric delay/loss in topology

The parallel architecture baseline is fully instrumented for latency analysis and stress testing.
