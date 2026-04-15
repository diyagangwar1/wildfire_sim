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

### 2. Coordinated lawnmower survey pattern

Both drones fly the same systematic **lawnmower survey** over a ±40m area — the standard flight plan for drone-based area surveillance. They sweep back and forth in X, advancing by 12m in Y after each strip, covering the entire area on every pass.

```
Survey bounds: X ∈ [-40, +40],  Y ∈ [-40, +40]  (80m × 80m)
Strip width:   12m  →  ~7 strips per pass
Speed:         4m/step × 2Hz = 8 m/s  (realistic surveillance UAV)
Full pass:     ~70 seconds (slightly more than one experiment duration)

Thermal drone:  altitude 40m  (lower = better thermal resolution)
Imagery drone:  altitude 80m  (higher = wider visual field)

Both drones cover the same XY area simultaneously.
```

This guarantees:
- The fire zone at `(20, 20)` is **always covered** on every pass — both sensors have the same opportunity to detect it
- Inter-drone XY separation is **~0** (they're directly above/below each other) — the vertical separation of 40m is the primary geometric difference
- All 50 Monte Carlo seeds fly the **same deterministic route** — only the GPS noise differs, isolating the experiment variable cleanly

The `--seed` argument controls **GPS noise only** (`N(0, 0.5m)` per axis, per step) — realistic GPS accuracy. Previously the seed controlled an entire random walk trajectory, which meant seeds could accidentally avoid the fire zone entirely, making detection rates noisy for the wrong reason.

### 3. Cellular automaton fire propagation model

The fire is no longer a binary on/off square wave. `fire_model.py` implements a full spreading fire on a **120×120 metre grid** (1m cells):

```
States: UNBURNED → BURNING → BURNED

Each 0.5s step:
  • Each BURNING cell tries to ignite its 8 neighbours with probability:
        p = BASE_SPREAD_PROB + WIND_BOOST × max(0, dot(direction, wind))
        BASE_SPREAD_PROB = 0.12,  WIND_BOOST = 0.20  (NE wind by default)
  • Each BURNING cell has a 4% chance of burning out → BURNED

Fire starts at ignition point (20, 20) — one cell. Over a 60s run:
  t=0s  → 1 burning cell
  t=15s → ~15 cells
  t=30s → ~80 cells
  t=60s → ~300+ cells (large, well-detectable fire)
```

**Detection probability** is position-dependent and grows as the fire spreads:

```
visible_count = burning cells within 40m horizontal radius of drone

detect_p = FALSE_ALARM_PROB + (DETECT_MAX_P - FALSE_ALARM_P) × √(visible_count / 20)
         = 0.06 + 0.86 × √(count / 20)

→ 0 visible cells  →  6%  (false alarm only)
→ 5 cells          → ~40%
→ 20 cells         → ~92% (saturates near maximum)
```

**How both workers stay in sync without communication:** Both workers call `fire_grid.advance_to_wall_clock_step()` each loop, which computes `target = int(time.time() / 0.5)` and advances the grid to that step. Since both use the same `fire_seed` (default 0), their isolated `random.Random` instances make identical RNG calls in identical order, producing byte-for-byte the same grid at every wall-clock moment. No message passing required.

The `fire_seed` is completely independent of the trajectory seed (`--seed`). Changing the trajectory seed changes where the drones fly but not how the fire spreads — and vice versa. This clean separation is what makes the 50-seed Monte Carlo meaningful: the 50 seeds vary drone trajectories against the same fire event.

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

5. **How does the fire's growth affect detection rates over time?** Early in a run the fire is small — even a nearby drone may miss it. As it spreads to 50+ cells, detection becomes reliable. Does high network delay cause the system to miss the early, critical detection window?

6. **Is latency stable over a mission?** The timeseries plots check whether latency drifts upward (buffer buildup) or stays stationary.

---

## Remaining Limitations

| Assumption | Reality | Severity |
|-----------|---------|---------|
| Controller is a fixed ground station at `(0,0,0)` | Proposal envisions a flying controller drone | Medium — drop model is based on wrong reference point |
| Network delay is constant within a run | Real RF links have time-varying multipath fading | Medium |
| Fire ignition point is fixed; wind direction is constant | Real fires chase wind, terrain, and fuel gradients | Low — growth and spread now modelled; dynamic wind is not |
| Workers share the same physical clock source (VM) | Real drones each have independent GPS receiver drift | Low — clock error experiments now cover this explicitly |
| Only 2 worker drones | Proposal envisions 100+ | High for scalability questions; fine for protocol analysis |
| No conditional payload (always sends bounding boxes) | Proposal: send raw image only when model confidence is low | Low for latency study; missing for bandwidth/compute tradeoffs |

---

## Code Map

| File | Role |
|------|------|
| `fire_model.py` | Cellular automaton fire spread — 120×120m grid, wind-biased propagation, wall-clock-based sync between workers |
| `thermal_worker.py` | Thermal sensor — FireGrid detection, deterministic lawnmower trajectory from shared launch pad, opt-in distance drop, clock error |
| `imagery_worker.py` | Imagery sensor — same FireGrid + lawnmower + clock model as thermal |
| `gps_time.py` | Shared time utilities — `utc_ns()`, `sleep_to_next_tick()` |
| `controller.py` | Ground controller — GPS pair matching, rolling-window fusion, full latency logging |
| `mn_topo.py` | Mininet topology — per-link delay/loss + per-worker clock error args |
| `run_experiments.py` | Orchestrator — 20 experiments in 3 groups (network, clock, distance) |
| `compare_seeds.py` | Aggregate analysis — cross-seed comparison plots into `results/results_combined/` |
| `analyze_latency.py` | Per-run analysis — latency breakdown, timeseries, drone distance, hit/miss table |
| `model_latency.py` | Analytical model — predicted vs. measured E2E latency curves |
| `investigate_anomaly.py` | Deep-dive tool — isolates seed-specific or experiment-specific anomalies |

---

## Canonical Repository Data Layout (Apr 15 update)

To avoid stale-result confusion, the repository now uses one canonical dataset layout:

- Raw Monte Carlo run data: `data/results_mc50_seed0` ... `data/results_mc50_seed49`
- Combined aggregate outputs: `results/results_combined/`
- Report snapshot for the latest run: `results/results_combined/APR15_RESULTS_ANALYSIS.md`

This separation ensures the analysis scripts only consume the intended 50-seed dataset and prevents mixing with legacy pre-Apr-15 outputs.
