# Wildfire Sim — Tasks from Prof Mohanty, Meeting Notes & ChatGPT

**Sources:** Week 6 prof notes, meeting summary (detection logic, sync, experiments), Zoom transcript (Mar 2, 2026).  
**Goal:** Fix synchronization, get reproducible experiments, and produce the requested plots and numbers.

---

## 1. GPS-based synchronization (priority 1)

**Why:** Controller was using "most recent" thermal + imagery instead of matching by timestamp. With added delay, imagery consistently arrived second → artificially better detection. Now pairs by GPS tx_ns so results are interpretable.

| ID | Task | Status | Notes |
|----|------|--------|--------|
| 1.1 | Implement **GPS-based matching** in controller (pair thermal + imagery by timestamp, not "latest of each") | ✅ | `try_evaluate()` in controller.py finds nearest pair by tx_ns |
| 1.2 | Add **time-proximity threshold** (configurable): match "nearest timestamp within X ms" — **start with 5 ms** | ✅ | `SYNC_THRESHOLD_MS=5.0`; CLI `--sync-threshold-ms` |
| 1.3 | Add **interpolation buffer**: keep last N messages per stream so a new imagery msg can match a slightly older thermal (and vice versa) | ✅ | `SYNC_BUFFER_SIZE=10`; `thermal_buffer` / `imagery_buffer` deques |
| 1.4 | Avoid pairing new imagery with stale thermal; only consider messages within threshold | ✅ | If `best_dt_ns > threshold_ns`, evaluation is skipped |
| 1.5 | Make threshold a **configurable parameter** (`--sync-threshold-ms 5`) | ✅ | `controller.py --sync-threshold-ms <float>` |

> **Tip:** With unsynchronized workers (independent `time.sleep` loops), tx_ns values rarely fall within 5 ms of each other. If you see no fusions, increase the threshold (e.g. `--sync-threshold-ms 250`) or start both workers simultaneously.

---

## 2. Reproducibility: random seed

**Why:** Drone trajectories differ between runs → can't isolate effect of delay/loss. Fixed seed makes runs comparable.

| ID | Task | Status | Notes |
|----|------|--------|--------|
| 2.1 | **Reset seed** at the start of each experiment run (thermal + imagery workers) | ✅ | `random.seed(args.seed)` at startup in both workers |
| 2.2 | Use a **fixed seed per run** (e.g. from CLI) so the same config gives same drone paths every time | ✅ | `--seed 42` flag added to both workers |
| 2.3 | Document how to set seed in README / run scripts | ✅ | README "How to Run" updated; `run_experiments.py` always passes `--seed` |

---

## 3. Drone distance / 3D behavior

**Why:** Realistic 3D separation: smaller XY, larger Z (height). Radio range >100 m is unrealistic in XY; Z can dominate the 3D distance.

| ID | Task | Status | Notes |
|----|------|--------|--------|
| 3.1 | **Seed / constrain drone distance / separation** (3D) for reproducibility | ✅ | `--seed` controls random walk; `XY_MAX_M=45.0` clamps radius |
| 3.2 | **Reduce X–Y spacing** to < 50 m (cap XY random walk) | ✅ | `XY_MAX_M=45.0`; walk projected back onto 45 m circle when exceeded |
| 3.3 | Allow **height (Z) difference to be much larger** than XY | ✅ | `DRONE_ALT_MAX_M=200`; thermal starts at Z=60 m, imagery at Z=80 m |
| 3.4 | Note: 100 m total range may be optimistic for radios; Z dominates | ✅ | Comment added in workers |

---

## 4. Detection logic (rolling window)

**Decision from meeting:** Keep temporal consistency (consecutive / pattern-based), not "any 3 in 5."

| ID | Task | Status | Notes |
|----|------|--------|--------|
| 4.1 | **Keep** rolling window with temporal logic — do *not* switch to "any 3 in 5" | ✅ | Unchanged; `FIRE_CONFIRM_K=3` of last `FIRE_WINDOW_K=5` |
| 4.2 | After GPS sync, re-run baseline to check detection rates make sense | ⬜ | Run with `--sync-threshold-ms 250` initially to get fusions |

---

## 5. Time delay range

| ID | Task | Status | Notes |
|----|------|--------|--------|
| 5.1 | Support time delay **> 1 s** (e.g. 1000 ms, 2000 ms) | ✅ | `--thermal-delay` / `--imagery-delay` now `type=float` in mn_topo.py |

---

## 6. Experiments: controlled sweeps (one variable at a time)

| ID | Task | Status | Notes |
|----|------|--------|--------|
| 6.1 | **Detection rate vs camera delay** (thermal delay = 0): sweep 0 → 10 → 50 → 100 ms → 1 s | ⬜ | |
| 6.2 | **Detection rate vs thermal delay** (camera delay = 0): same increments | ⬜ | |
| 6.3 | **Detection rate vs camera delay + loss**: sweep delay and add loss rates (0%, 1%, 5%) | ⬜ | |
| 6.4 | **Separate** delay and loss experiments in reporting | ⬜ | |
| 6.5 | Use **same seed** for all sweep runs | ⬜ | e.g. `--seed 42` in both workers |

---

## 7. Plots to generate

### 7.1 Detection rate

| ID | Task | Status | Notes |
|----|------|--------|--------|
| 7.1.1 | **Detection rate vs added delay (camera)** — thermal delay = 0 | ⬜ | Add to `compare_runs.py` or new script |
| 7.1.2 | **Detection rate vs added delay (thermal)** — camera delay = 0 | ⬜ | |
| 7.1.3 | **Detection rate vs camera delay + loss** — multiple curves for different loss rates | ⬜ | |

### 7.2 Latency

| ID | Task | Status | Notes |
|----|------|--------|--------|
| 7.2.1 | **Latency as a function of distance** between drones / drone–controller | ⬜ | Existing `phase5_e2e_vs_distance.png`; re-run with fixed seed |
| 7.2.2 | **Latency vs added delay**: mean/median E2E vs configured delay | ⬜ | Extend `compare_runs.py` |
| 7.2.3 | **Latency vs drone distance**: scatter, fixed seed | ⬜ | |
| 7.2.4 | **Latency plotted as time** + **superimpose drone trajectories** on same/dual axis | ⬜ | New plot in `analyze_latency.py` |
| 7.2.5 | **Scatter**: latency vs delay; latency vs distance | ⬜ | |

### 7.3 Reporting numbers

| ID | Task | Status | Notes |
|----|------|--------|--------|
| 7.3.1 | **Report absolute numbers (ms)** alongside % — for comparison with Orin and Alice | ✅ | Pie chart now shows `XX.X ms` per slice |
| 7.3.2 | Pie chart title shows mean E2E ms and total component sum | ✅ | Done in `analyze_latency.py` |

---

## 8. Optional / later

| ID | Task | Status | Notes |
|----|------|--------|--------|
| 8.1 | **Send rate sweep**: vary `SEND_HZ` in workers | ⬜ | |
| 8.2 | **Inject ~12 ms inference delay** in imagery path (YOLO/Orin benchmark) | ⬜ | Add `time.sleep(0.012)` in imagery proc |
| 8.3 | **Keep thermal-first hierarchy** (if thermal false, no image compute flagged) | ✅ | Current controller behavior; no change needed |
| 8.4 | **Compare** latency numbers with Orin and Alice's centralized architecture | ⬜ | |
| 8.5 | **Repository**: move to main GitHub repo | ⬜ | |

---

## 9. Summary checklist (order of operations)

1. ✅ Implement GPS sync (buffer 10, threshold 5 ms, match by tx_ns, skip re-fusion).
2. ✅ Add seed control (`--seed`) to both workers.
3. ✅ Adjust 3D: XY < 50 m (radius clamped to 45 m), Z up to 200 m.
4. ✅ Float delay in mn_topo.py (supports > 1 s).
5. ✅ Absolute ms in pie chart.
6. ✅ GPS clock-snap (`sleep_to_next_tick`) so both workers send at the same instants — makes 5 ms threshold meaningful.
7. ✅ Shared fire schedule (`is_fire_window()`) — both sensors correlated via clock; fixes independent-fire problem.
8. ✅ `--base-drop-prob 0` in `run_experiments.py` — clean baseline, only Mininet loss applies.
9. ✅ `fire_window` + `hit_miss` (TP/FN/FP/TN) logged per event in CSV and JSONL.
10. ✅ `fire_hit_miss_table.png` generated by `analyze_latency.py` — recall, miss rate, precision per run.
11. ✅ Fused-pairs deque (maxlen=20) prevents re-fusion edge case.
12. ✅ `TIME_WINDOW_S` tightened from 2 s → 0.5 s (half send period).
13. ✅ Update README "How to Run" with `--seed`, `--sync-threshold-ms`, fire model docs.
14. ⬜ **Run experiments** (`sudo python3 run_experiments.py --seed 42 --duration 60`).
15. ⬜ Verify results are physically sensible (detection rate flat/declining with delay, latency increasing).
16. ⬜ Generate final sweep plots and hit/miss tables for write-up.

---

## 10. Source quote summary

- **Prof (Week 6):** Sync GPS time; 5 ms time-proximity threshold; delay up to >1 s; seed/constrain 3D drone separation; XY < 50 m, height can be much more; detection rate vs delay (camera only, thermal only, camera+loss); latency vs distance; latency vs time with trajectory overlay; report absolute numbers.
- **Meeting notes:** GPS-based matching + buffer ~3 + 5 ms threshold; reset seed per experiment; isolated sweeps (delay vs loss); keep consecutive rolling-window logic; plots: detection vs delay, latency vs delay, vs distance, vs time; pie chart with absolute values.
- **Zoom transcript:** Same sync/buffer/seed/plot points; 12 ms YOLO reference; XY < 50 m, Z larger; repo restructure; compare with Orin and Alice.
