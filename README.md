# Wildfire Multi-Drone Simulation

A simulated multi-drone communication pipeline for wildfire detection, built on [Mininet](http://mininet.org/). Two drones — a thermal sensor and an imagery camera — transmit data streams to a ground fusion controller over an emulated network. The system measures end-to-end detection latency and robustness under varying network conditions (delay, packet loss).

50-seed Monte Carlo experiments have been run across 14 network configurations, producing statistically grounded latency measurements.

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
| **h2** (thermal worker) | Simulates thermal sensor — 2D/1D temperature arrays, distance-based dropout |
| **h3** (imagery worker) | Simulates camera — bounding box detections, distance-based dropout |

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

# Run a quick single-seed test (~15 min)
sudo python3 run_experiments.py --seed 42 --duration 60

# Full 50-seed Monte Carlo sweep (~13 hours)
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
python3 compare_seeds.py    # discovers data/results_seed*/ automatically
# → writes plots to results/
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

### Run a single seed experiment (14 network conditions, ~15 min)

```bash
sudo python3 run_experiments.py --seed 42 --duration 60
```

Results are written to `data/results_seed42/`.

### Run a Monte Carlo sweep (50 seeds, ~13 hours)

```bash
sudo python3 run_experiments.py --seeds "0-49" --duration 60
```

Results are written to `data/results_seed{N}/` for each seed.

### Generate aggregate comparison plots

```bash
python3 compare_seeds.py          # auto-discovers all seeds in data/
# → writes 11 plots to results/
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
| `--plots-only` | — | Skip simulation; regenerate plots from existing logs |

---

## Network experiments (14 configurations)

| Category | Experiments |
|----------|------------|
| **Baseline** | No delay, no loss |
| **Camera delay** | 10 / 50 / 100 / 500 / 1000 ms on imagery link |
| **Thermal delay** | 10 / 50 / 100 / 500 / 1000 ms on thermal link |
| **Delay + loss** | Camera 10ms+1%, Camera 100ms+1%, Camera 100ms+5% |

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
| `thermal_net_ns` / `imagery_net_ns` | Network delay per stream (ns) |
| `thermal_proc_ns` / `imagery_proc_ns` | Worker processing time (ns) |
| `fusion_proc_ns` | Controller fusion processing time (ns) |
| `separation_xy_m` | Horizontal inter-drone distance (m) |
| `separation_dz_m` | Vertical inter-drone separation (m) |
| `fire_window` | Ground-truth: was fire active at this timestamp? |
| `hit_miss` | Detection outcome: `TP` / `FP` / `FN` / `TN` |

---

## Results

After running `compare_seeds.py`, the `results/` directory contains 11 aggregate figures across all 50 seeds:

| File | What it shows |
|------|---------------|
| `1_latency_vs_delay.png` | Mean ± SD latency vs camera/thermal delay |
| `4_cdf_e2e_latency.png` | CDF of E2E latency for key conditions |
| `5_loss_effect.png` | Boxplots — effect of packet loss across seeds |
| `6_latency_breakdown.png` | Stacked bar — which component dominates latency |
| `7_distance_vs_latency.png` | Scatter — inter-drone separation vs E2E latency |
| `8_experiment_comparison.png` | Sorted horizontal bar — all 14 experiments ranked |
| `9_summary_table.png` | Mean ± SD table for all experiments |
| `10_timeseries_baseline.png` | Latency over time — mean ± SD envelope |
| `11_trajectories_xy.png` | Drone position density heatmap (XY plane) |
| `12_trajectories_z.png` | Drone altitude over time — mean ± SD envelope |
| `13_violin_all_experiments.png` | Full latency distribution for every experiment |

---

## Repo structure

```
wildfire_sim/
│
├── controller.py          # Fusion controller — receives streams, fuses, logs latency
├── thermal_worker.py      # Thermal sensor simulator
├── imagery_worker.py      # Camera / imagery simulator
├── mn_topo.py             # Mininet topology (configurable delay/loss/bandwidth)
├── gps_time.py            # UTC/GPS-style nanosecond timestamp helpers
│
├── run_experiments.py     # Automated experiment runner (single seed or Monte Carlo sweep)
├── compare_seeds.py       # Aggregate cross-seed comparison plots → results/
├── analyze_latency.py     # Per-run latency analysis and plots
├── model_latency.py       # Latency distribution modelling utilities
├── investigate_anomaly.py # Helper for identifying anomalous latency events
│
├── tests/                 # Unit + integration tests (run with run_tests.sh)
├── requirements.txt
├── run_tests.sh
│
├── data/                  # Raw experiment outputs (generated — not in .gitignore'd by default)
│   ├── results_seed0/     # One directory per seed
│   │   ├── baseline/
│   │   │   ├── latency_log.jsonl
│   │   │   ├── fusion_log.csv
│   │   │   └── plots/
│   │   ├── cam_delay_100ms/
│   │   └── ...            # 14 experiment dirs per seed
│   └── results_seed49/
│
└── results/               # Aggregate comparison figures (generated by compare_seeds.py)
    ├── 1_latency_vs_delay.png
    └── ...
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
| `--bw` | 100.0 | Link bandwidth (Mbps) |
