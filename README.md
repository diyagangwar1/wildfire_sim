# Wildfire Multi-Drone Simulation (Phases 1–5)

Evaluate multi-drone communication architectures under stress conditions (latency, packet loss, dropouts) to determine which structure minimizes latency and maximizes robustness. Phases 1–5 are implemented; GPS-based synchronization and controlled experiment sweeps are in progress (see [Recent updates & next steps](#recent-updates--next-steps)).

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

## Fire Model & Ground Truth

Both workers use a shared clock-based fire schedule (`is_fire_window()` in `gps_time.py`).

- **Cycle**: 8 s on, 5 s off (3 s active = 37.5 % duty cycle)
- **During fire window**: thermal 85 % → high temp; imagery 80 % → fire label
- **Outside fire window**: both drop to ~5–10 % (occasional false positives)
- **No coordination needed**: both workers call the same deterministic function of wall-clock time

The controller logs `fire_window` (ground truth) and `hit_miss` (`TP`/`FN`/`FP`/`TN`) for every fusion event. `analyze_latency.py` produces a hit/miss table with recall, miss rate, and precision. This lets you see not just "detection rate" but specifically **how many real fires were missed and under what conditions** (delay, loss, distance).

---

## Phase 3 Timing (GPS/UTC)

We log UTC epoch timestamps in nanoseconds (`tx_ns`, `rx_ns`, `fusion_done_ns`). On real drones, replace this with GPS receiver time; format remains the same. **Note:** The controller currently fuses using the *most recent* thermal and imagery messages; GPS-based *matching* (pair by timestamp within a threshold) is planned next so that baseline vs delay results are interpretable (see [TASKS_PROF_MEETING.md](TASKS_PROF_MEETING.md)).

---

## How to Run

### Option A — Run everything automatically (recommended)

`run_experiments.py` runs all 14 experiments back-to-back, generates per-run plots,
and produces all sweep-level plots the prof requested.

```bash
sudo python3 run_experiments.py --seed 42 --duration 60
```

Key flags:

| Flag | Default | Description |
|------|---------|-------------|
| `--seed` | 42 | Same seed for all runs (identical drone trajectories) |
| `--duration` | 60 | Seconds per run (~15 min total for 14 runs) |
| `--outdir` | `results` | Root folder; each run gets a subfolder |
| `--sync-threshold-ms` | 250 | GPS match window (ms); 250 = half send period for unsynchronized workers |
| `--only baseline cam_delay_100ms` | — | Run only specific experiments |
| `--plots-only` | — | Skip runs; just re-generate plots from existing results |

Outputs:
- `results/<name>/` — per-run logs and plots
- `results/sweep_plots/` — detection rate vs delay, latency vs delay, summary table

### Option B — Manual single run

```bash
# Start Mininet topology (delays/loss configurable)
sudo python3 mn_topo.py --thermal-delay 0 --imagery-delay 100

# Inside Mininet CLI:
mininet> h1 python3 controller.py --outdir results/baseline --sync-threshold-ms 250 &
mininet> h2 python3 thermal_worker.py 10.0.0.1 --seed 42 &
mininet> h3 python3 imagery_worker.py 10.0.0.1 --seed 42 &
mininet> h1 tail -f results/baseline/fusion_log.csv
```

Let it run ~60s, then Ctrl+C and `exit` to stop Mininet.

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
| `fire_window` | Boolean — was the shared fire schedule "active" at send time? (ground truth) |
| `hit_miss` | `TP` / `FN` / `FP` / `TN` — detection outcome vs ground truth |

**latency_log.jsonl** (one JSON object per line) includes:

- `thermal_proc_ns`, `imagery_proc_ns` — worker processing time
- `thermal_net_ns`, `imagery_net_ns` — network delay (rx - tx)
- `fusion_proc_ns` — controller fusion time
- `e2e_ns`, `e2e_ms` — end-to-end latency

---

## Latency Analysis

**Per-run plots** (from a single `latency_log.jsonl`):

```bash
python3 analyze_latency.py latency_log.jsonl --outdir plots
```

Generates:

- `plots/latency_contribution_pie.png` — mean latency contribution (% and absolute ms per component)
- `plots/e2e_latency_timeseries.png` — E2E latency over fusion events
- `plots/phase4_rolling_window.png` — raw signal vs rolling decision, confirmations vs threshold
- `plots/phase5_drone_distance.png` — both drones' distance over time
- `plots/phase5_e2e_vs_distance.png` — E2E latency vs drone distance scatter
- `plots/fire_hit_miss_table.png` — TP / FN / FP / TN counts, recall, miss rate, precision

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
├── README.md              # This file
├── TASKS.md               # Granular phase task list (Phases 1–6)
├── TASKS_PROF_MEETING.md  # Tasks from prof + meeting (GPS sync, seed, sweeps, plots)
├── gps_time.py            # UTC/GPS-style timestamp helpers
├── mn_topo.py             # Mininet topology (asymmetric delay/loss)
├── controller.py          # Fusion controller + latency logging
├── thermal_worker.py      # Thermal stream (Phase 1–5)
├── imagery_worker.py      # Imagery stream (Phase 1–5)
├── analyze_latency.py     # Per-run latency analysis & plots
├── compare_runs.py        # Cross-run comparison (E2E, detection rate, summary table)
├── fusion_log.csv         # Generated at runtime
├── latency_log.jsonl      # Generated at runtime
└── results/
    ├── README.md          # Experiment folder naming
    ├── baseline/
    ├── delay_20_80/
    ├── delay_100_100/
    ├── loss_1_1/
    └── comparison/        # Output of compare_runs.py (e2e, detection, summary_table)
```

---

## Phase 4 & 5 (Implemented)

- **Phase 4**: Rolling window thresholding (`FIRE_WINDOW_K=5`, `FIRE_CONFIRM_K=3`) — fire only when ≥3 of last 5 events meet criteria (temporal consistency kept per meeting)
- **Phase 5**: Distance-based drop probability (drone 3D random-walk, `drop_prob = BASE + SLOPE × distance`); distance logged per stream
- **Phase 6 (sync + ground truth)**: GPS clock-snap so workers send at the same instants; GPS-based pair matching in controller; shared fire-event schedule (`is_fire_window()`) so both sensors agree on when fire is active; `fire_window` + `hit_miss` (TP/FN/FP/TN) logged per fusion event

---

## Recent updates & next steps

**Done (last few weeks):**

- Phase 1–5 implementation (realism, robustness, latency logging, rolling window, distance-based drop)
- Four experiment runs: baseline, delay 20/80 ms (asymmetric), 1% loss, delay 100 ms (symmetric)
- Cross-run comparison: `compare_runs.py` → `results/comparison/` (E2E comparison, detection rate, summary table)
- Task list from prof/meeting: [TASKS_PROF_MEETING.md](TASKS_PROF_MEETING.md)

**Planned (from prof + meeting notes):**

1. **GPS-based synchronization** — Match thermal and imagery by timestamp (e.g. nearest within 5 ms), not “most recent”; add small buffer (~3 messages) per stream so pairing is correct when delays differ.
2. **Reproducibility** — Fixed random seed per run so drone trajectories are identical across experiments; document `--seed` or env.
3. **Drone 3D behavior** — Reduce XY spacing to &lt; 50 m; allow height (Z) to contribute more to separation (realistic radio / altitude).
4. **Controlled sweeps** — One variable at a time: detection rate vs camera delay (thermal=0); vs thermal delay (camera=0); vs camera delay + loss; delay range up to &gt;1 s.
5. **Plots** — Detection rate vs delay (camera and thermal); latency vs delay; latency vs distance; latency vs time with drone trajectory overlay; pie chart and reports with **absolute latency (ms)** for comparison with Orin and Alice’s setup.
6. **Optional** — Send-rate sweep; inject ~12 ms inference delay (YOLO/Orin); compare numbers with onboard Orin and Alice’s centralized architecture.

See [TASKS_PROF_MEETING.md](TASKS_PROF_MEETING.md) for the full task list with IDs and status.

---

## Cross-run comparison

For slides and reports, aggregate multiple runs into comparison plots:

```bash
python3 compare_runs.py --outdir results/comparison
```

Produces in `results/comparison/`:

- `e2e_comparison.png` — E2E latency by run
- `detection_rate_comparison.png` — Detection rate by run
- `summary_table.png` — Summary table (e.g. mean E2E, detection rate) for Baseline, Delay 20/80, Loss 1%, Delay 100 ms

Edit `RUNS` in `compare_runs.py` to include or rename runs.

---

## For Professor

- **Phase 1**: Realism — variable shapes (2D + 1D), empty detections, overlapping boxes
- **Phase 2**: Robustness — threading + locks + deques, port robustness, drop monitoring, stop condition; workers: dropouts, reconnect loop
- **Phase 3**: GPS/UTC timestamps, end-to-end latency, component breakdown, `latency_log.jsonl`, asymmetric delay/loss in topology
- **Phase 4**: Rolling-window fire decision (K=5, confirm=3; temporal consistency)
- **Phase 5**: 3D drone random-walk, distance-based drop, distance logged

Next: GPS-based pairing, seed control, XY &lt; 50 m, controlled sweeps, and absolute latency numbers for comparison with Orin and Alice. Full task list: [TASKS_PROF_MEETING.md](TASKS_PROF_MEETING.md).

The parallel architecture baseline is fully instrumented for latency analysis and stress testing.
