# Wildfire Multi-Drone Simulation — Granular Task List

**Objective:** Build a robust, stress-testable parallel architecture baseline, instrumented for latency analysis and architectural comparisons.

---

## Phase 1 — Immediate Code Improvements (Stability + Realism)

**Status:** ✅ Completed + Tested

### 1.1 Thermal Worker — Probabilistic Fire Model

| ID | Task | Status |
|----|------|--------|
| 1.1.1 | Replace binary temperature spike logic with probabilistic model | ✅ |
| 1.1.2 | Draw random probability p ∈ [0,1] per timestep | ✅ |
| 1.1.3 | If p > threshold (e.g., 0.6): inflate temperatures by +10°C | ✅ |
| 1.1.4 | Otherwise: keep baseline Gaussian | ✅ |
| 1.1.5 | Add hotspot at random cell when fire_sim=True | ✅ |

### 1.2 Thermal Worker — Grid Size Reduction

| ID | Task | Status |
|----|------|--------|
| 1.2.1 | Change from 8×8 to smaller grids (2×2, 3×3, 4×4) | ✅ |
| 1.2.2 | Add SHAPES_2D list with variable dimensions | ✅ |
| 1.2.3 | Randomly select shape per timestep | ✅ |

### 1.3 Thermal Worker — Irregular Data Sizes

| ID | Task | Status |
|----|------|--------|
| 1.3.1 | Add 1D list option (8×1, 15×1, 2×1, etc.) | ✅ |
| 1.3.2 | Add LENS_1D list for variable array lengths | ✅ |
| 1.3.3 | Add P_SEND_1D probability for 1D vs 2D choice | ✅ |
| 1.3.4 | Vary array length per timestep | ✅ |

### 1.4 Imagery Worker — Overlapping Bounding Boxes

| ID | Task | Status |
|----|------|--------|
| 1.4.1 | Generate bounding boxes that partially overlap | ✅ |
| 1.4.2 | Keep x fixed, increment y slightly for overlap | ✅ |
| 1.4.3 | Keep y fixed, increment x slightly for overlap | ✅ |
| 1.4.4 | Use _rand_box(base) with jitter for subsequent boxes | ✅ |

### 1.5 Imagery Worker — No Detection Case

| ID | Task | Status |
|----|------|--------|
| 1.5.1 | Add P_EMPTY probability for empty detections | ✅ |
| 1.5.2 | Sometimes send empty list [] | ✅ |
| 1.5.3 | Controller handles empty detection case (imagery_has_fire returns False) | ✅ |

---

## Phase 2 — Controller Robustness

**Status:** ✅ Completed

### 2.1 Port Safety Checks

| ID | Task | Status |
|----|------|--------|
| 2.1.1 | If thermal_port == imagery_port: stop execution, raise warning | ✅ |
| 2.1.2 | If one port bind fails: continue with only thermal | ✅ |
| 2.1.3 | If one port bind fails: continue with only imagery | ✅ |
| 2.1.4 | Don't crash when one server fails to bind | ✅ |

### 2.2 Drop Monitoring

| ID | Task | Status |
|----|------|--------|
| 2.2.1 | Track packets received over time (thermal_arrivals, imagery_arrivals) | ✅ |
| 2.2.2 | Compute packets per second in rolling window | ✅ |
| 2.2.3 | Compute expected vs actual packet count | ✅ |
| 2.2.4 | Add DROP_STOP_THRESHOLD (e.g., 50%) | ✅ |
| 2.2.5 | If drop rate > threshold: stop simulation, raise alert | ✅ |
| 2.2.6 | Print [MONITOR] stats every MONITOR_WINDOW_S seconds | ✅ |

---

## Phase 3 — Timing & Latency

**Status:** ✅ Completed

### 3.1 Artificial Link Delay in Mininet

| ID | Task | Status |
|----|------|--------|
| 3.1.1 | Add --thermal-delay option to mn_topo.py | ✅ |
| 3.1.2 | Add --imagery-delay option to mn_topo.py | ✅ |
| 3.1.3 | Apply asymmetric delays (thermal ≠ imagery) | ✅ |
| 3.1.4 | Add --thermal-loss and --imagery-loss options | ✅ |

### 3.2 End-to-End Execution Time Measurement

| ID | Task | Status |
|----|------|--------|
| 3.2.1 | Use GPS/UTC time (NOT plain Python time) | ✅ |
| 3.2.2 | Record time when thermal link starts (tx_ns) | ✅ |
| 3.2.3 | Record time when imagery link starts (tx_ns) | ✅ |
| 3.2.4 | Record time when controller completes fusion (fusion_done_ns) | ✅ |
| 3.2.5 | Compute total elapsed e2e_ns, e2e_ms | ✅ |
| 3.2.6 | Implement gps_time.py with utc_ns(), utc_iso() | ✅ |

### 3.3 Latency Component Breakdown

| ID | Task | Status |
|----|------|--------|
| 3.3.1 | Measure thermal processing time (proc_ns in worker) | ✅ |
| 3.3.2 | Measure imagery processing time (proc_ns in worker) | ✅ |
| 3.3.3 | Measure network delay (rx_ns - tx_ns per stream) | ✅ |
| 3.3.4 | Measure controller fusion time (fusion_proc_ns) | ✅ |
| 3.3.5 | Log all components to latency_log.jsonl | ✅ |

### 3.4 Latency Contribution Plot

| ID | Task | Status |
|----|------|--------|
| 3.4.1 | Create pie chart of % contribution | ✅ |
| 3.4.2 | Graph latency vs send rate (extend analyze_latency.py) | ⬜ |
| 3.4.3 | Graph latency vs delay (extend analyze_latency.py) | ⬜ |
| 3.4.4 | Graph latency vs loss (extend analyze_latency.py) | ⬜ |

---

## Phase 4 — Robust Fusion Logic

**Status:** ✅ Completed

### 4.1 Rolling Window Thresholding

| ID | Task | Status |
|----|------|--------|
| 4.1.1 | Add FIRE_WINDOW_K constant (sliding window size, e.g., 5) | ✅ |
| 4.1.2 | Add FIRE_CONFIRM_K constant (min confirmations, e.g., 3) | ✅ |
| 4.1.3 | Replace single-timestep check with sliding window | ✅ |
| 4.1.4 | Maintain deque of last K raw fire signals | ✅ |
| 4.1.5 | Raw signal = (temp > 100) AND (imagery fire) AND (dt ≤ 2s) | ✅ |
| 4.1.6 | Decision = True only if ≥ FIRE_CONFIRM_K of last K are positive | ✅ |
| 4.1.7 | Log raw_signal, window_confirmations, window_fill, decision to JSONL | ✅ |
| 4.1.8 | Add Phase 4 columns to fusion_log.csv | ✅ |
| 4.1.9 | Print rolling window stats in [FUSION] log line | ✅ |

### 4.2 (Optional, Later) Smoothing

| ID | Task | Status |
|----|------|--------|
| 4.2.1 | Implement moving average for thermal readings | ⬜ |
| 4.2.2 | Implement low-pass filter | ⬜ |
| 4.2.3 | Implement robust estimator | ⬜ |

---

## Phase 5 — Drop Probability as Distance Function

**Status:** ✅ Completed

### 5.1 Drone Coordinate Simulation

| ID | Task | Status |
|----|------|--------|
| 5.1.1 | Define CONTROLLER_POS (0,0,0) in workers | ✅ |
| 5.1.2 | Define DRONE_START_POS for thermal worker | ✅ |
| 5.1.3 | Define DRONE_START_POS for imagery worker (different from thermal) | ✅ |
| 5.1.4 | Implement _random_walk_3d() for drone movement | ✅ |
| 5.1.5 | Update drone position each timestep | ✅ |
| 5.1.6 | Clamp altitude to DRONE_ALT_MIN_M, DRONE_ALT_MAX_M | ✅ |

### 5.2 Distance-Based Drop Probability

| ID | Task | Status |
|----|------|--------|
| 5.2.1 | Implement _distance_3d(a, b) | ✅ |
| 5.2.2 | Implement _drop_prob_from_distance(dist_m) | ✅ |
| 5.2.3 | Linear model: drop_prob = BASE + SLOPE × distance | ✅ |
| 5.2.4 | Clamp drop_prob to [BASE_DROP_PROB, MAX_DROP_PROB] | ✅ |
| 5.2.5 | Replace hard-coded DROP_PROB with distance-based value in thermal_worker | ✅ |
| 5.2.6 | Replace hard-coded DROP_PROB with distance-based value in imagery_worker | ✅ |
| 5.2.7 | Include drone_x, drone_y, drone_z, distance_m, drop_prob in sent messages | ✅ |

### 5.3 Controller-Side Distance Logging

| ID | Task | Status |
|----|------|--------|
| 5.3.1 | Extract thermal_distance_m from thermal message | ✅ |
| 5.3.2 | Extract imagery_distance_m from imagery message | ✅ |
| 5.3.3 | Log thermal_distance_m, imagery_distance_m to latency_log.jsonl | ✅ |
| 5.3.4 | Add thermal_distance_m, imagery_distance_m to fusion_log.csv | ✅ |

### 5.4 Phase 5 Analysis Plots

| ID | Task | Status |
|----|------|--------|
| 5.4.1 | Create phase5_drone_distance.png (both drones' distance over time) | ✅ |
| 5.4.2 | Create phase5_e2e_vs_distance.png (scatter: E2E latency vs distance) | ✅ |

---

## Phase 6 — Experiments

**Status:** ⬜ Not Started

### 6.1 First Baseline Experiment

| ID | Task | Status |
|----|------|--------|
| 6.1.1 | Run with no delay (mn_topo.py default) | ⬜ |
| 6.1.2 | Run with no loss | ⬜ |
| 6.1.3 | Run with no dropout (BASE_DROP_PROB=0 or distance=0) | ⬜ |
| 6.1.4 | Collect fusion_log.csv and latency_log.jsonl | ⬜ |
| 6.1.5 | Run analyze_latency.py on baseline | ⬜ |
| 6.1.6 | Document baseline metrics (e2e mean/median/p95, detection rate) | ⬜ |

### 6.2 Delay Sweep Experiments

| ID | Task | Status |
|----|------|--------|
| 6.2.1 | Run with thermal-delay=10ms, imagery-delay=10ms | ⬜ |
| 6.2.2 | Run with thermal-delay=20ms, imagery-delay=80ms (asymmetric) | ⬜ |
| 6.2.3 | Run with thermal-delay=50ms, imagery-delay=50ms | ⬜ |
| 6.2.4 | Run with thermal-delay=100ms, imagery-delay=100ms | ⬜ |
| 6.2.5 | Collect latency_log.jsonl for each run | ⬜ |
| 6.2.6 | Graph E2E latency vs delay | ⬜ |

### 6.3 Loss Sweep Experiments

| ID | Task | Status |
|----|------|--------|
| 6.3.1 | Run with thermal-loss=0%, imagery-loss=1% | ⬜ |
| 6.3.2 | Run with thermal-loss=1%, imagery-loss=1% | ⬜ |
| 6.3.3 | Run with thermal-loss=5%, imagery-loss=5% | ⬜ |
| 6.3.4 | Collect drop rate and latency for each run | ⬜ |
| 6.3.5 | Graph E2E latency vs loss | ⬜ |

### 6.4 Send Rate Sweep Experiments

| ID | Task | Status |
|----|------|--------|
| 6.4.1 | Run with SEND_HZ=1 in both workers | ⬜ |
| 6.4.2 | Run with SEND_HZ=2 (default) | ⬜ |
| 6.4.3 | Run with SEND_HZ=5 | ⬜ |
| 6.4.4 | Run with SEND_HZ=10 | ⬜ |
| 6.4.5 | Graph E2E latency vs send rate | ⬜ |

### 6.5 Drop Probability / Distance Experiments

| ID | Task | Status |
|----|------|--------|
| 6.5.1 | Run with DIST_DROP_SLOPE=0 (constant drop) | ⬜ |
| 6.5.2 | Run with DIST_DROP_SLOPE=0.001 (default) | ⬜ |
| 6.5.3 | Run with DIST_DROP_SLOPE=0.002 (steeper) | ⬜ |
| 6.5.4 | Graph E2E latency vs drone distance | ⬜ |
| 6.5.5 | Graph detection rate vs distance | ⬜ |

### 6.6 Summary Plots & Report

| ID | Task | Status |
|----|------|--------|
| 6.6.1 | Create combined plot: E2E latency vs (delay, loss, send rate, drop) | ⬜ |
| 6.6.2 | Create detection rate vs stress parameters | ⬜ |
| 6.6.3 | Document failure cases (when simulation stopped) | ⬜ |
| 6.6.4 | Write experiment summary to results/README.md | ⬜ |

---

## Phase 4 Analysis Plots (analyze_latency.py)

| ID | Task | Status |
|----|------|--------|
| 4.A.1 | Create phase4_rolling_window.png | ✅ |
| 4.A.2 | Overlay raw_signal vs rolling decision in plot | ✅ |
| 4.A.3 | Bar chart of confirmations per event vs threshold | ✅ |

---

## Future Work (Not Now)

- Hierarchical architecture variant
- Add ground station hop
- Convert to C++ (later)
- Hardware migration

---

## Legend

| Symbol | Meaning |
|--------|---------|
| ✅ | Done |
| 🔄 | In progress / implemented by Claude, needs verification |
| ⬜ | Not started |
