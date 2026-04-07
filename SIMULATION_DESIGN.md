# Simulation Design & Research Goals

This document explains the design decisions behind the drone simulation, how the data is made realistic, and what the research is actually trying to answer.

---

## Research Question

> **How does network delay, packet loss, and clock synchronization error between airborne sensors and a ground controller affect the end-to-end latency and reliability of wildfire detection?**

Real wildfire detection systems use multiple UAVs — one carrying a thermal camera, one carrying an RGB/imagery camera — that stream data to a ground station for fusion. Before deploying such a system, you need to understand:

- How much does link delay hurt detection speed?
- Does packet loss matter as much as delay, or less?
- Which sensor link is the bottleneck — thermal or imagery?
- What happens when a drone's GPS clock is miscalibrated or noisy?
- How stable is latency over the course of a mission?

This simulation recreates that architecture in a controlled, reproducible environment using [Mininet](http://mininet.org/) to emulate the network, so these questions can be answered with real latency measurements rather than guesses.

---

## System Overview

```
┌─────────────────────────────────────────────────────┐
│                     Mininet VM                      │
│                                                     │
│  ┌──────────┐  TCP/5001  ┌──────────────────────┐   │
│  │ Thermal  │ ─────────► │                      │   │
│  │  Worker  │            │  Ground Controller   │   │
│  │  (h2)    │            │        (h1)          │   │
│  └──────────┘            │                      │   │
│                          │  - GPS pair matching │   │
│  ┌──────────┐  TCP/5002  │  - Rolling-window    │   │
│  │ Imagery  │ ─────────► │    fire decision     │   │
│  │  Worker  │            │  - Latency logging   │   │
│  │  (h3)    │            │                      │   │
│  └──────────┘            └──────────────────────┘   │
│                                                     │
│  Network: configurable delay + loss per link        │
│  Workers: configurable clock offset + jitter        │
└─────────────────────────────────────────────────────┘
```

Each run:
1. Mininet creates a virtual network with two configurable links
2. Both workers launch from the same position and send sensor data at 2 Hz
3. The controller fuses matched pairs, logs every fusion event with full latency breakdown
4. Everything is written to `latency_log.jsonl` and `fusion_log.csv`

---

## Making the Data Realistic

### 1. Two independent sensor streams with different data formats

**Thermal worker** (`thermal_worker.py`) generates:
- **2D temperature grids** — variable sizes: `2×2`, `3×3`, `4×4`, `2×4`
- **1D arrays** — lengths 2, 4, 8, 15 (30% of frames)
- Background temperature: `N(70°C, 2°C)` — hot ambient, below the fire threshold of 100°C

**Imagery worker** (`imagery_worker.py`) generates:
- Bounding box detections: label, confidence (0.4–0.99), `(x1, y1, x2, y2)`
- 1–3 detections per frame; 20% of frames are empty (no detections)
- Labels: `"fire"`, `"smoke"`, `"tree"`, `"rock"` — controller acts only on `"fire"`
- Overlapping bounding boxes — consecutive detections jitter around a base box, simulating the same fire region seen multiple times

### 2. Shared launch position — drones start together

Both drones start at the same launch pad: `(0, 0, 10m)`. This is realistic — in practice, all drones take off from the same site. They then diverge via independent random walks. The thermal drone takes 5m steps; the imagery drone takes 6m steps, so they separate naturally over time.

Previously, drones started at arbitrary pre-separated positions, which baked in artificial spatial separation from frame one. Now their separation emerges organically from the random walk — different seeds produce different divergence patterns.

### 3. Spatial fire model — drone position affects detection probability

The fire exists at a fixed ground-level zone: `FIRE_ZONE_XY = (20, 20)`. Detection probability scales with horizontal distance from the drone to the fire zone:

```
During a fire window:
  fire_p = max(0.08,  0.90 - 0.008 × dist_xy_metres)

  → Directly above fire (dist=0):   ~90% detection
  → 50m away:                       ~50% detection
  → 100m+ away:                     ~8%  (matches false-alarm floor)

Outside a fire window (regardless of position):
  fire_p = 0.08  (constant false-alarm floor)
```

Both workers use the **same fire zone** so a drone near the fire zone on both sensors will simultaneously see high-confidence signals — which is the physically correct behavior. Previously, detection probability was independent of drone position: even a drone 200m away had the same 85% detection rate as one directly overhead.

### 4. Clean baseline — distance-based drop is opt-in

The previous simulation had a persistent distance-based packet drop (`DIST_DROP_SLOPE = 0.001/m`) active even in the "baseline" (no Mininet loss) experiment. A drone starting at 60m distance would already have ~6% packet loss, corrupting the zero-loss baseline.

This is now fixed:

- **Default**: `DIST_DROP_SLOPE = 0.0` — distance drops are **off**. The baseline is a true zero-loss floor.
- **Opt-in**: Pass `--dist-drop-slope 0.001` to enable the distance model for specific experiments.
- A dedicated `dist_drop_enabled` experiment in the matrix runs the distance model explicitly so its effect is measured in isolation.

### 5. GPS clock synchronisation between workers

Both workers use `sleep_to_next_tick(period=0.5)` to align to the same wall-clock grid. They wake at `t=0.0, 0.5, 1.0...` seconds, so their `tx_ns` values are within a few milliseconds — mimicking GPS PPS-synchronized drones.

### 6. GPS-based pair matching in the controller

The controller maintains a rolling buffer of the last 10 messages from each stream and matches on the smallest `|tx_thermal - tx_imagery|`, only accepting pairs within `SYNC_THRESHOLD_MS = 2000ms`. This handles asymmetric link delays correctly: if imagery has 500ms delay, the controller still pairs the thermally-captured frame with the imagery frame that was captured at the same time.

### 7. Clock synchronization error experiments (new)

Your proposal explicitly called for characterizing synchronization errors:

> *"add a constant offset of time, add an offset sampled from a Gaussian distribution"*

This is now implemented. Each worker accepts two new CLI flags:

- `--clock-offset-ms` — constant bias on `tx_ns` (e.g. the drone's GPS clock runs 200ms fast)
- `--clock-jitter-ms` — std dev of per-message Gaussian noise on `tx_ns`

**Why this matters:** The controller uses `|tx_thermal - tx_imagery|` for pair matching. When the thermal drone has a +500ms clock offset, the controller sees every pair as 500ms apart. Since the fusion raw signal check requires `dt_s <= 0.5s`, an offset at exactly 500ms sits right at the threshold — fire events start getting dropped. At 1000ms offset, pair matching fails completely.

This creates a meaningful new sweep: how sensitive is the fusion system to GPS clock quality?

### 8. Rolling-window fire decision (temporal consistency)

A single fusion event is not enough to raise an alarm:
- **Window K = 5**: sliding window of last 5 fusion events
- **Confirmation = 2**: fire declared only if ≥ 2/5 events pass the raw signal check

Raw signal check per event requires:
1. Thermal max temp > 100°C
2. Imagery has at least one `"fire"` label
3. Matched pair timestamps within 0.5s of each other

### 9. Full latency decomposition

Every fusion event is logged with a complete breakdown:

```
E2E = fusion_done_ns − min(thermal_tx_ns, imagery_tx_ns)

  thermal_proc_ns  : thermal worker data generation time
  thermal_net_ns   : transit time (rx_ns − tx_ns)
  imagery_proc_ns  : imagery worker detection generation time
  imagery_net_ns   : transit time
  fusion_proc_ns   : controller processing time
```

### 10. Detection ground truth (TP/FP/FN/TN)

Every fusion event is classified against the known fire schedule:

| Label | Meaning |
|-------|---------|
| **TP** | Fire active AND detected |
| **FN** | Fire active AND missed |
| **FP** | Fire NOT active AND false alarm |
| **TN** | Fire NOT active AND correctly quiet |

---

## What We're Measuring

### Experiment groups (20 total)

| Group | Experiments | Variable |
|-------|-------------|---------|
| Network — baseline | 1 | True zero-loss floor |
| Network — camera delay | 5 | Imagery link: 10/50/100/500/1000ms |
| Network — thermal delay | 5 | Thermal link: 10/50/100/500/1000ms |
| Network — loss + delay | 3 | Imagery: 10ms+1%, 100ms+1%, 100ms+5% |
| Clock — offset | 3 | Thermal clock: +100/+500/+1000ms constant bias |
| Clock — jitter | 2 | Thermal clock: σ=50ms, σ=200ms Gaussian noise |
| Distance drop | 1 | Enables 0.001/m distance drop model |

### Monte Carlo sweep (50 seeds)

Running 50 independent seeds gives:
- Distribution of E2E latency across different drone trajectory realizations
- Mean ± standard deviation for every experiment
- Confidence that results are not artifacts of one specific flight path

### Research questions being answered

1. **Does delay scale linearly into E2E latency?** Or is there non-linear amplification from the pairing buffer and confirmation window?

2. **Is the system symmetric?** Does camera delay hurt more than thermal delay? The fusion logic depends on both sensors agreeing, so asymmetry is expected.

3. **How much does packet loss matter at 2 Hz?** A dropped packet means the next one arrives 500ms later. Is this worse or better than adding 500ms of delay?

4. **What is the tolerance for GPS clock error?** At what clock offset does the pair-matching system start breaking down? Where does the fusion raw signal check fail?

5. **How does drone proximity to the fire affect detection rates?** With the spatial fire model, seeds where the random walk brings drones near `(20, 20)` should have higher TP rates than seeds where drones stay far away.

6. **Is latency stable over a mission?** The timeseries plots check whether latency drifts upward (buffer buildup) or stays stationary.

---

## Remaining Limitations

| Assumption | Reality | Severity |
|-----------|---------|---------|
| Controller is a fixed ground station at `(0,0,0)` | Proposal envisions a flying controller drone | Medium — drop model is based on wrong reference point |
| Network delay is constant within a run | Real RF links have time-varying multipath fading | Medium |
| Fire ground truth is a square wave at a fixed XY | Real fires ignite, spread, and move | Medium |
| Workers share the same physical clock source (VM) | Real drones each have independent GPS receiver drift | Low — clock error experiments now cover this explicitly |
| Only 2 worker drones | Proposal envisions 100+ | High for scalability questions; fine for protocol analysis |
| No conditional payload (always sends bounding boxes) | Proposal: send raw image only when model confidence is low | Low for latency study; missing for bandwidth/compute tradeoffs |

---

## Code Map

| File | Role |
|------|------|
| `thermal_worker.py` | Thermal sensor — spatial fire model, random walk from shared launch pad, opt-in distance drop, GPS clock error |
| `imagery_worker.py` | Imagery sensor — same spatial + walk + clock model as thermal |
| `gps_time.py` | Shared time utilities — `utc_ns()`, `sleep_to_next_tick()`, `is_fire_window()` |
| `controller.py` | Ground controller — GPS pair matching, rolling-window fusion, full latency logging |
| `mn_topo.py` | Mininet topology — per-link delay/loss + per-worker clock error args |
| `run_experiments.py` | Orchestrator — 20 experiments in 3 groups (network, clock, distance) |
| `compare_seeds.py` | Aggregate analysis — cross-seed comparison plots into `results/` |
| `analyze_latency.py` | Per-run analysis — latency breakdown, timeseries, drone distance, hit/miss table |
| `model_latency.py` | Analytical model — predicted vs. measured E2E latency curves |
| `investigate_anomaly.py` | Deep-dive tool — isolates seed-specific or experiment-specific anomalies |
