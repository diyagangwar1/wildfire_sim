# Wildfire Multi-Drone Simulation

A simulated multi-drone communication pipeline for wildfire detection, built on [Mininet](http://mininet.org/). Two drones — a thermal sensor and an imagery camera — transmit data streams to a ground fusion controller over an emulated network. The system measures end-to-end detection latency and robustness under varying network conditions (delay, packet loss, and GPS clock error).

Fire realism is provided by a **cellular automaton spread model** (`fire_model.py`): fire ignites at a fixed point and propagates across a 120×120m grid with wind-biased spread. Detection probability for each drone scales with how many burning cells it can see from its current position — making early-run detection harder (small fire) and late-run detection easier (large fire).

50-seed Monte Carlo experiments have been run across 20 configurations (14 network + 5 clock-sync + 1 distance-drop), producing statistically grounded latency and detection-reliability measurements.

---

## Architecture

```
              ┌──────────────┐
              │  s1 (switch) │
              └──────┬───────┘
         ┌───────────┼───────────┐
         │           │           │
   ┌─────┴─────┐ ┌───┴────┐ ┌───┴─────┐
   │    h1     │ │   h2   │ │   h3    │
   │controller │ │thermal │ │ imagery │
   └───────────┘ └────────┘ └─────────┘
       TCP 5001 ←──────┘         │
       TCP 5002 ←────────────────┘
```

| Node | Role |
|------|------|
| **h1** (controller) | Receives thermal + imagery streams, applies rolling-window fusion, logs latency |
| **h2** (thermal worker) | Thermal sensor — lawnmower survey at 40m altitude, fire detection via `FireGrid` |
| **h3** (imagery worker) | Camera sensor — same lawnmower at 80m altitude, fire detection via `FireGrid` |

### Fusion rule
A fire is declared when **all three** conditions hold within a 2-second sync window:
1. Thermal max temperature > 100 °C
2. Imagery has at least one `"fire"` label
3. ≥ 3 of the last 5 fusion events confirm fire (rolling-window threshold)

---

## Requirements

Mininet requires a Linux kernel — it cannot run on macOS directly. If you're on a Mac, follow the VM setup guide below. Analysis and plotting scripts (`compare_seeds.py`, `analyze_latency.py`) work natively on macOS without a VM.

---

## VM Setup (macOS → UTM → Ubuntu)

> Skip this if you're already on Linux.

### 1. Install UTM

Download UTM from [mac.getutm.app](https://mac.getutm.app) (free) and install it.

### 2. Create an Ubuntu VM

1. Download **Ubuntu 22.04 LTS Server** ISO from [ubuntu.com/download/server](https://ubuntu.com/download/server)
2. Open UTM → **Create a New Virtual Machine** → **Virtualize**
3. Select **Linux** → choose the Ubuntu ISO
4. Recommended settings:
   - **RAM**: 4 GB minimum (8 GB if available)
   - **CPU cores**: 2+
   - **Storage**: 20 GB
5. Complete the Ubuntu installer — set a username and password you'll remember
6. After installation, eject the ISO: VM settings → Drives → remove the CD-ROM drive

### 3. Enable SSH (optional but recommended)

Inside the VM:

```bash
sudo apt update && sudo apt install -y openssh-server
ip a   # note the VM's IP address (e.g. 192.168.64.X)
```

From your Mac terminal you can then SSH in instead of using the UTM window:

```bash
ssh <your-username>@<vm-ip>
```

### 4. Install dependencies

```bash
sudo apt update
sudo apt install -y mininet python3-pip python3-matplotlib python3-numpy git
sudo pip3 install scipy pandas
```

Verify Mininet works:

```bash
sudo mn --test pingall
# Should print "Results: 0% dropped" then clean up
```

### 5. Clone the repo and run

```bash
git clone https://github.com/diyagangwar1/wildfire_sim.git
cd wildfire_sim

# Quick test — 14 network-only experiments, single seed (~15 min)
sudo python3 run_experiments.py --seed 42 --duration 60 --skip-groups clock distance

# All 20 experiments, single seed (~25 min)
sudo python3 run_experiments.py --seed 42 --duration 60

# Full 50-seed Monte Carlo sweep, all experiments (~21 hours)
sudo python3 run_experiments.py --seeds "0-49" --duration 60
```

### 6. Copy results back to your Mac

Once experiments finish, copy the `data/` folder from the VM to your Mac for analysis:

```bash
# Run this on your Mac (replace with your VM's IP and username)
scp -r <username>@<vm-ip>:~/wildfire_sim/data ./
```

Then on your Mac, generate the comparison plots:

```bash
python3 compare_seeds.py    # discovers data/results_mc50_seed*/ automatically
# → writes plots to results/results_combined/
```

### Troubleshooting

| Problem | Fix |
|---------|-----|
| `sudo mn` hangs or errors | Run `sudo mn -c` to clean up stale Mininet state, then retry |
| `ModuleNotFoundError: matplotlib` | `sudo apt install python3-matplotlib` |
| `ModuleNotFoundError: scipy` | `sudo pip3 install scipy` |
| SSH connection refused | `sudo systemctl start ssh` inside the VM |
| VM very slow | In UTM settings, enable **Hardware OpenGL acceleration** and increase CPU cores |

---

## Quick Start

### Run a single seed — all 20 experiments (~25 min)

```bash
sudo python3 run_experiments.py --seed 42 --duration 60
```

Results are written to `data/results_mc50_seed42/`.

### Run only the original 14 network experiments (~15 min per seed)

```bash
sudo python3 run_experiments.py --seed 42 --duration 60 --skip-groups clock distance
```

### Run the full Monte Carlo sweep — all 20 experiments × 50 seeds (~21 hours)

```bash
sudo python3 run_experiments.py --seeds "0-49" --duration 60
```

### Run only the clock sync experiments (5 experiments)

```bash
sudo python3 run_experiments.py --seed 42 --duration 60 --only \
  clock_offset_100ms clock_offset_500ms clock_offset_1000ms \
  clock_jitter_50ms clock_jitter_200ms
```

Results are written to `data/results_mc50_seed{N}/` for each seed.

### Generate aggregate comparison plots

```bash
python3 compare_seeds.py          # auto-discovers all seeds in data/
# → writes 18 plots to results/results_combined/
```

### Analyse a single run

```bash
python3 analyze_latency.py data/results_seed42/baseline/latency_log.jsonl --outdir plots/
```

---

## `run_experiments.py` flags

| Flag | Default | Description |
|------|---------|-------------|
| `--seed N` | 42 | Single seed (fixed drone trajectories) |
| `--seeds "0-49"` | — | Range of seeds for Monte Carlo sweep |
| `--duration S` | 60 | Seconds per experiment run |
| `--outdir DIR` | `data` | Root output directory |
| `--only exp1 exp2` | — | Run only the named experiments |
| `--skip-groups GROUP` | — | Skip groups: `network`, `clock`, `distance` |
| `--plots-only` | — | Skip simulation; regenerate plots from existing logs |

---

## Experiments (20 configurations across 3 groups)

| Group | Category | Experiments |
|-------|----------|------------|
| `network` | Baseline | No delay, no loss, no clock error — true zero-loss floor |
| `network` | Camera delay | 10 / 50 / 100 / 500 / 1000 ms on imagery link |
| `network` | Thermal delay | 10 / 50 / 100 / 500 / 1000 ms on thermal link |
| `network` | Delay + loss | Camera 10ms+1%, Camera 100ms+1%, Camera 100ms+5% |
| `clock` | Clock offset | Thermal GPS clock: +100ms / +500ms / +1000ms constant bias |
| `clock` | Clock jitter | Thermal GPS jitter: σ=50ms / σ=200ms per-message noise |
| `distance` | Distance drop | Enables 0.001/m distance-based packet drop model |

---

## Output files

Each experiment run produces a directory with:

| File | Contents |
|------|----------|
| `latency_log.jsonl` | One JSON record per fusion event — timestamps, per-component latency, drone positions, inter-drone separation, fire ground truth (TP/FP/FN/TN) |
| `fusion_log.csv` | Per-event fusion decisions, rolling-window state, detection outcome |
| `controller.log` | Raw controller stdout |
| `thermal.log` / `imagery.log` | Raw worker stdout |
| `plots/` | Per-run visualisations (timeseries, breakdown pie, drone distance) |

### Key `latency_log.jsonl` fields

| Field | Description |
|-------|-------------|
| `e2e_ms` | End-to-end latency (ms) — fire event to fusion decision |
| `thermal_net_ns` / `imagery_net_ns` | Network transit time per stream (ns) |
| `thermal_proc_ns` / `imagery_proc_ns` | Worker data generation time (ns) |
| `fusion_proc_ns` | Controller fusion processing time (ns) |
| `separation_xy_m` | Horizontal inter-drone distance (m) |
| `separation_dz_m` | Vertical inter-drone separation (m) |
| `fire_window` | Ground-truth: are any cells currently BURNING in the fire model? |
| `fire_cell_count` | Total burning cells across the entire 120×120m grid |
| `fire_visible_count` | Burning cells within 40m of this drone's XY position |
| `hit_miss` | Detection outcome: `TP` / `FP` / `FN` / `TN` |
| `clock_offset_ns` | Clock bias applied to this worker's `tx_ns` (0 in standard experiments) |

---

## Results

After running `compare_seeds.py`, `results/results_combined/` contains aggregate figures across all 50 seeds (currently 18 plots):

| File | What it shows |
|------|---------------|
| `1_latency_vs_delay.png` | Mean ± SD latency vs camera/thermal delay |
| `4_cdf_e2e_latency.png` | CDF of E2E latency for key conditions |
| `5_loss_effect.png` | Boxplots — effect of packet loss across seeds |
| `6_latency_breakdown.png` | Stacked bar — which component dominates latency |
| `7_distance_vs_latency.png` | Scatter — inter-drone separation vs E2E latency |
| `8_experiment_comparison.png` | Sorted horizontal bar — all 20 experiments ranked |
| `9_summary_table.png` | Mean ± SD table for all experiments |
| `10_timeseries_baseline.png` | Latency over time — mean ± SD envelope |
| `11_trajectories_xy.png` | Drone 2D occupancy map + representative trajectories (XY plane) |
| `12_trajectories_z.png` | Drone altitude over time — mean ± SD envelope |
| `13_violin_all_experiments.png` | Full latency distribution for every experiment |
| `14_detection_performance.png` | Precision / Recall / F1 by experiment |
| `15_queuing_wait.png` | Queuing-wait decomposition of slow vs fast latency paths |
| `16_clock_sync_effect.png` | Clock offset/jitter impact on latency, F1, and throughput |
| `17_fire_growth.png` | Fire growth and visible-fire dynamics over time |
| `18_confusion_balance.png` | TP vs FN composition by experiment |
| `19_queue_vs_visibility.png` | Queuing wait vs visible fire correlation |
| `20_stability_map.png` | Experiment stability map (mean vs SD across seeds) |

---

## Repo structure

```
wildfire_sim/
│
├── controller.py          # Fusion controller — receives streams, fuses, logs latency
├── thermal_worker.py      # Thermal sensor simulator
├── imagery_worker.py      # Camera / imagery simulator
├── fire_model.py          # Cellular automaton fire spread (120×120m, wind-biased)
├── mn_topo.py             # Mininet topology (delay/loss/clock-error/fire-seed)
├── gps_time.py            # UTC/GPS-style nanosecond timestamp helpers
│
├── run_experiments.py     # Automated experiment runner (single seed or Monte Carlo sweep)
├── compare_seeds.py       # Aggregate cross-seed comparison plots → results/results_combined/
├── analyze_latency.py     # Per-run latency analysis and plots
├── model_latency.py       # Latency distribution modelling utilities
├── investigate_anomaly.py # Helper for identifying anomalous latency events
│
├── tests/                 # Unit + integration tests (run with run_tests.sh)
├── requirements.txt
├── run_tests.sh
│
├── data/                  # Raw experiment outputs
│   ├── results_mc50_seed0/ # One directory per seed
│   │   ├── baseline/
│   │   │   ├── latency_log.jsonl
│   │   │   ├── fusion_log.csv
│   │   │   └── plots/
│   │   ├── cam_delay_100ms/
│   │   └── ...            # 20 experiment dirs per seed
│   └── results_mc50_seed49/
│
└── results/
    └── results_combined/  # Aggregate comparison figures (generated by compare_seeds.py)
        ├── 1_latency_vs_delay.png
        ├── ...
        └── APR15_RESULTS_ANALYSIS.md
```

---

## Running tests

```bash
bash run_tests.sh
# or
python3 -m pytest tests/ -v
```

---

## Manual single-run (advanced)

```bash
# 1. Start the Mininet topology with desired network conditions
sudo python3 mn_topo.py --imagery-delay 100 --imagery-loss 1

# 2. Inside the Mininet CLI:
mininet> h1 python3 controller.py --outdir data/my_run --sync-threshold-ms 2000 &
mininet> h2 python3 thermal_worker.py 10.0.0.1 --seed 42 &
mininet> h3 python3 imagery_worker.py 10.0.0.1 --seed 42 &

# 3. Wait ~60s, then:
mininet> exit

# 4. Analyse the run:
python3 analyze_latency.py data/my_run/latency_log.jsonl --outdir data/my_run/plots
```

### `mn_topo.py` options

| Option | Default | Description |
|--------|---------|-------------|
| `--thermal-delay` | 0 | Delay (ms) on thermal link |
| `--imagery-delay` | 0 | Delay (ms) on imagery link |
| `--thermal-loss` | 0.0 | Packet loss (%) on thermal link |
| `--imagery-loss` | 0.0 | Packet loss (%) on imagery link |
| `--bw` | 1.0 | Link bandwidth (Mbps) |
| `--fire-seed` | 0 | Fire propagation seed (same for both workers) |
| `--dist-drop-slope` | 0.0 | Drop probability per metre of distance (0 = disabled) |
| `--thermal-clock-offset-ms` | 0.0 | Constant GPS clock bias on thermal drone (ms) |
| `--thermal-clock-jitter-ms` | 0.0 | Per-message GPS jitter std dev on thermal drone (ms) |
| `--imagery-clock-offset-ms` | 0.0 | Constant GPS clock bias on imagery drone (ms) |
| `--imagery-clock-jitter-ms` | 0.0 | Per-message GPS jitter std dev on imagery drone (ms) |
