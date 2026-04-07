"""
Automated experiment runner — Wildfire Multi-Drone Simulation.

Runs every controlled sweep back-to-back with no manual intervention.
After all runs it generates:
  • Per-run plots  (via analyze_latency.py)
  • Sweep-level comparison plots under <outdir>/sweep_plots/
  • Monte Carlo aggregate plots when multiple seeds are used

Experiment groups
─────────────────
  Network delay & loss (14 runs)   — standard Mininet link conditioning
  Clock synchronization (4 runs)   — GPS clock offset / jitter on the thermal drone
  Distance-based drop (1 run)      — enables linear drop-vs-distance model

Usage (must be run as root for Mininet):
    sudo python3 run_experiments.py [options]

Key options:
    --seeds STR           Comma-separated seeds or range "0-99" (Monte Carlo).
                          Default: 42.
    --duration SECS       Seconds per experiment (default 60)
    --outdir DIR          Root results directory (default data)
    --sync-threshold-ms   GPS pair-matching window in ms (default 2000)
    --only NAME [NAME...] Run only the named experiments
    --plots-only          Skip running experiments; regenerate plots only
    --skip-groups GROUP   Skip experiment groups: network, clock, distance
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np


# ---------------------------------------------------------------------------
# Experiment matrix — each entry is a plain dict with defaults for all fields.
# ---------------------------------------------------------------------------

def _exp(
    name: str,
    thermal_delay: float = 0,
    imagery_delay: float = 0,
    thermal_loss: float = 0.0,
    imagery_loss: float = 0.0,
    thermal_clock_offset_ms: float = 0.0,
    thermal_clock_jitter_ms: float = 0.0,
    imagery_clock_offset_ms: float = 0.0,
    imagery_clock_jitter_ms: float = 0.0,
    dist_drop_slope: float = 0.0,
    fire_seed: int = 0,
    group: str = "network",
) -> Dict[str, Any]:
    return dict(
        name=name,
        thermal_delay=thermal_delay,
        imagery_delay=imagery_delay,
        thermal_loss=thermal_loss,
        imagery_loss=imagery_loss,
        thermal_clock_offset_ms=thermal_clock_offset_ms,
        thermal_clock_jitter_ms=thermal_clock_jitter_ms,
        imagery_clock_offset_ms=imagery_clock_offset_ms,
        imagery_clock_jitter_ms=imagery_clock_jitter_ms,
        dist_drop_slope=dist_drop_slope,
        fire_seed=fire_seed,
        group=group,
    )


EXPERIMENTS: List[Dict[str, Any]] = [
    # ── Network: baseline ─────────────────────────────────────────────────
    # No delay, no loss, no distance drop, perfect clocks.
    # This is the true floor — no hidden packet loss from the distance model.
    _exp("baseline"),

    # ── Network: camera (imagery) delay sweep — thermal link clean ────────
    _exp("cam_delay_10ms",    imagery_delay=10),
    _exp("cam_delay_50ms",    imagery_delay=50),
    _exp("cam_delay_100ms",   imagery_delay=100),
    _exp("cam_delay_500ms",   imagery_delay=500),
    _exp("cam_delay_1000ms",  imagery_delay=1000),

    # ── Network: thermal delay sweep — imagery link clean ─────────────────
    _exp("thermal_delay_10ms",   thermal_delay=10),
    _exp("thermal_delay_50ms",   thermal_delay=50),
    _exp("thermal_delay_100ms",  thermal_delay=100),
    _exp("thermal_delay_500ms",  thermal_delay=500),
    _exp("thermal_delay_1000ms", thermal_delay=1000),

    # ── Network: camera delay + imagery packet loss ───────────────────────
    _exp("cam_10ms_loss1pct",   imagery_delay=10,  imagery_loss=1.0),
    _exp("cam_100ms_loss1pct",  imagery_delay=100, imagery_loss=1.0),
    _exp("cam_100ms_loss5pct",  imagery_delay=100, imagery_loss=5.0),

    # ── Clock sync: constant offset on thermal GPS clock ──────────────────
    # Simulates a miscalibrated GPS receiver on the thermal drone.
    # The controller's pair-matching window (SYNC_THRESHOLD_MS=2000ms) still
    # accepts these pairs, but dt_s grows → raw_signal fails at offset > 500ms.
    _exp("clock_offset_100ms",  thermal_clock_offset_ms=100.0,  group="clock"),
    _exp("clock_offset_500ms",  thermal_clock_offset_ms=500.0,  group="clock"),
    _exp("clock_offset_1000ms", thermal_clock_offset_ms=1000.0, group="clock"),

    # ── Clock sync: per-message Gaussian jitter on thermal GPS clock ──────
    # Simulates GPS timing noise (e.g., multipath, weak signal).
    _exp("clock_jitter_50ms",  thermal_clock_jitter_ms=50.0,  group="clock"),
    _exp("clock_jitter_200ms", thermal_clock_jitter_ms=200.0, group="clock"),

    # ── Distance-based drop: enables linear drop-vs-distance model ────────
    # Unlike the baseline (no distance drops), this run activates the 0.001/m
    # slope so packet loss grows as each drone drifts further from the controller.
    # Both drones start together at (0,0,10) and diverge — distance increases
    # naturally over the run.
    _exp("dist_drop_enabled", dist_drop_slope=0.001, group="distance"),
]

PALETTE = ["#4c72b0", "#dd8452", "#55a868", "#c44e52", "#8172b2",
           "#937860", "#da8bc3", "#8c8c8c", "#ccb974", "#64b5cd"]


def parse_seeds(spec: str) -> List[int]:
    """Parse '42', '0,1,2', or '0-99' into a list of seeds."""
    spec = spec.strip()
    if not spec:
        return [42]
    if "-" in spec and "," not in spec:
        parts = spec.replace(" ", "").split("-", 1)
        if len(parts) == 2:
            return list(range(int(parts[0]), int(parts[1]) + 1))
    return [int(x.strip()) for x in spec.split(",") if x.strip()]


def result_root_for_seed(outdir: str, seed: int, multi_seed: bool) -> str:
    if multi_seed:
        return f"{outdir}_seed{seed}"
    return outdir


# ---------------------------------------------------------------------------
# Mininet runner
# ---------------------------------------------------------------------------
def run_one(exp: Dict[str, Any], outdir: str, seed: int, duration: int,
            sync_threshold_ms: float) -> None:
    """Run a single experiment via mn_topo.py --auto (fully isolated subprocess)."""
    name = exp["name"]
    script_dir = os.path.dirname(os.path.abspath(__file__))
    exp_dir = os.path.abspath(os.path.join(outdir, name))
    os.makedirs(exp_dir, exist_ok=True)

    print(f"\n{'='*64}")
    print(f"  Experiment : {name}  [group={exp['group']}]")
    print(f"  thermal delay={exp['thermal_delay']}ms   imagery delay={exp['imagery_delay']}ms")
    print(f"  thermal loss={exp['thermal_loss']}%      imagery loss={exp['imagery_loss']}%")
    print(f"  thermal clock: offset={exp['thermal_clock_offset_ms']}ms  "
          f"jitter={exp['thermal_clock_jitter_ms']}ms")
    print(f"  imagery clock: offset={exp['imagery_clock_offset_ms']}ms  "
          f"jitter={exp['imagery_clock_jitter_ms']}ms")
    print(f"  dist_drop_slope={exp['dist_drop_slope']}  fire_seed={exp['fire_seed']}")
    print(f"  seed={seed}   duration={duration}s   sync_threshold={sync_threshold_ms}ms")
    print(f"  output -> {exp_dir}")
    print(f"{'='*64}")

    cmd = [
        sys.executable,
        os.path.join(script_dir, "mn_topo.py"),
        "--thermal-delay",             str(exp["thermal_delay"]),
        "--imagery-delay",             str(exp["imagery_delay"]),
        "--thermal-loss",              str(exp["thermal_loss"]),
        "--imagery-loss",              str(exp["imagery_loss"]),
        "--thermal-clock-offset-ms",   str(exp["thermal_clock_offset_ms"]),
        "--thermal-clock-jitter-ms",   str(exp["thermal_clock_jitter_ms"]),
        "--imagery-clock-offset-ms",   str(exp["imagery_clock_offset_ms"]),
        "--imagery-clock-jitter-ms",   str(exp["imagery_clock_jitter_ms"]),
        "--dist-drop-slope",           str(exp["dist_drop_slope"]),
        "--fire-seed",                 str(exp["fire_seed"]),
        "--auto",
        "--outdir",            exp_dir,
        "--seed",              str(seed),
        "--duration",          str(duration),
        "--sync-threshold-ms", str(sync_threshold_ms),
    ]
    subprocess.run(cmd, check=False)

    subprocess.run(["mn", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)

    latency_log = os.path.join(exp_dir, "latency_log.jsonl")
    if os.path.exists(latency_log) and os.path.getsize(latency_log) > 0:
        plots_dir = os.path.join(exp_dir, "plots")
        subprocess.run(
            [sys.executable,
             os.path.join(script_dir, "analyze_latency.py"),
             latency_log, "--outdir", plots_dir, "--label", name],
            check=False,
        )
        print(f"  Per-run plots -> {plots_dir}")
    else:
        print(f"  WARNING: {latency_log} is empty — no fusions recorded.")
        print(f"  Check {exp_dir}/controller.log  and  {exp_dir}/thermal.log")


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------
def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def _median(xs: List[float]) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[len(s) // 2]


def _pctl(xs: List[float], p: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    k = int(round((p / 100.0) * (len(s) - 1)))
    return s[max(0, min(k, len(s) - 1))]


def compute_metrics(jsonl_path: str) -> Optional[Dict[str, Any]]:
    """Load a latency_log.jsonl and return a dict of summary metrics."""
    if not os.path.exists(jsonl_path) or os.path.getsize(jsonl_path) == 0:
        return None
    rows = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    if not rows:
        return None

    e2e   = [float(r["e2e_ms"]) for r in rows if "e2e_ms" in r]
    raw   = [bool(r.get("raw_signal", False)) for r in rows]
    dec   = [bool(r.get("decision", False))   for r in rows]
    t_net = [r.get("thermal_net_ns", 0) / 1e6 for r in rows]
    i_net = [r.get("imagery_net_ns", 0) / 1e6 for r in rows]
    dt_s  = [float(r.get("dt_s", 0))          for r in rows]

    return {
        "n":             len(rows),
        "mean_e2e_ms":   _mean(e2e),
        "median_e2e_ms": _median(e2e),
        "p95_e2e_ms":    _pctl(e2e, 95),
        "raw_rate":      sum(raw) / max(1, len(raw)),
        "dec_rate":      sum(dec) / max(1, len(dec)),
        "t_net_mean":    _mean(t_net),
        "i_net_mean":    _mean(i_net),
        "mean_dt_s":     _mean(dt_s),
    }


# ---------------------------------------------------------------------------
# Sweep-level plots
# ---------------------------------------------------------------------------
def merge_metrics_across_seeds(
    outdir: str, seeds: List[int], experiments: List[Dict]
) -> Dict[str, Dict[str, Any]]:
    """Mean-of-means across Monte Carlo seeds for each experiment name."""
    merged: Dict[str, Dict[str, Any]] = {}
    for exp in experiments:
        name = exp["name"]
        series: List[Dict[str, Any]] = []
        for s in seeds:
            root = f"{outdir}_seed{s}"
            m = compute_metrics(os.path.join(root, name, "latency_log.jsonl"))
            if m:
                series.append(m)
        if not series:
            continue
        merged[name] = {
            "n":             int(round(statistics.mean([x["n"]             for x in series]))),
            "mean_e2e_ms":   statistics.mean([x["mean_e2e_ms"]             for x in series]),
            "median_e2e_ms": statistics.mean([x["median_e2e_ms"]           for x in series]),
            "p95_e2e_ms":    statistics.mean([x["p95_e2e_ms"]              for x in series]),
            "raw_rate":      statistics.mean([x["raw_rate"]                for x in series]),
            "dec_rate":      statistics.mean([x["dec_rate"]                for x in series]),
            "t_net_mean":    statistics.mean([x["t_net_mean"]              for x in series]),
            "i_net_mean":    statistics.mean([x["i_net_mean"]              for x in series]),
            "mean_dt_s":     statistics.mean([x.get("mean_dt_s", 0)        for x in series]),
        }
    return merged


def plot_sweeps(
    outdir: str,
    experiments: List[Dict],
    metrics: Optional[Dict[str, Dict[str, Any]]] = None,
) -> None:
    """Generate sweep-level comparison plots."""
    sweep_dir = os.path.join(outdir, "sweep_plots")
    os.makedirs(sweep_dir, exist_ok=True)

    if metrics is None:
        metrics = {}
        for exp in experiments:
            m = compute_metrics(os.path.join(outdir, exp["name"], "latency_log.jsonl"))
            if m:
                metrics[exp["name"]] = m

    if not metrics:
        print("[SWEEPS] No results found — skipping sweep plots.")
        return

    # Only include network-group, no-clock-offset, no-distance-drop experiments
    # in the delay sweep plots so the axes are clean.
    def _is_clean_network(e: Dict) -> bool:
        return (
            e["group"] == "network"
            and e.get("thermal_clock_offset_ms", 0.0) == 0.0
            and e.get("thermal_clock_jitter_ms", 0.0) == 0.0
            and e.get("imagery_clock_offset_ms", 0.0) == 0.0
            and e.get("imagery_clock_jitter_ms", 0.0) == 0.0
            and e.get("dist_drop_slope", 0.0) == 0.0
        )

    def cam_delay_series(loss: float = 0.0) -> List[Tuple[float, Dict]]:
        pts = []
        for e in experiments:
            if not _is_clean_network(e):
                continue
            if (e["thermal_delay"] == 0 and e["thermal_loss"] == 0.0
                    and e["imagery_loss"] == loss and e["name"] in metrics):
                pts.append((e["imagery_delay"], metrics[e["name"]]))
        return sorted(pts, key=lambda x: x[0])

    def thermal_delay_series() -> List[Tuple[float, Dict]]:
        pts = []
        for e in experiments:
            if not _is_clean_network(e):
                continue
            if (e["imagery_delay"] == 0 and e["thermal_loss"] == 0.0
                    and e["imagery_loss"] == 0.0 and e["name"] in metrics):
                pts.append((e["thermal_delay"], metrics[e["name"]]))
        return sorted(pts, key=lambda x: x[0])

    cam_pts = cam_delay_series(loss=0.0)
    th_pts = thermal_delay_series()

    # ── 1. Mean + p95 E2E vs camera delay ────────────────────────────────
    if cam_pts:
        delays = [p[0] for p in cam_pts]
        e2e_mean = [p[1]["mean_e2e_ms"] for p in cam_pts]
        e2e_p95  = [p[1]["p95_e2e_ms"]  for p in cam_pts]
        i_net    = [p[1]["i_net_mean"]   for p in cam_pts]
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.plot(delays, e2e_mean, "o-", color="#c44e52", lw=2, ms=8, label="Mean E2E")
        ax.plot(delays, e2e_p95,  "s--", color="#8c564b", lw=2, ms=7, label="p95 E2E")
        ax.plot(delays, i_net,    "^--", color="#f0a500", lw=1.5, ms=7,
                label="Imagery network delay (mean)")
        ax.set_xlabel("Camera (imagery) link delay (ms)", fontsize=12)
        ax.set_ylabel("Latency (ms)", fontsize=12)
        ax.set_title("E2E Latency vs Camera Delay\n(thermal delay = 0)",
                     fontsize=13, fontweight="bold")
        ax.legend(fontsize=11)
        ax.yaxis.grid(True, alpha=0.4)
        ax.set_axisbelow(True)
        plt.tight_layout()
        out = os.path.join(sweep_dir, "latency_vs_camera_delay.png")
        plt.savefig(out, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"  Wrote {out}")

    # ── 2. Mean + p95 E2E vs thermal delay ───────────────────────────────
    if th_pts:
        delays = [p[0] for p in th_pts]
        e2e_mean = [p[1]["mean_e2e_ms"] for p in th_pts]
        e2e_p95  = [p[1]["p95_e2e_ms"]  for p in th_pts]
        t_net    = [p[1]["t_net_mean"]   for p in th_pts]
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.plot(delays, e2e_mean, "o-", color="#4c72b0", lw=2, ms=8, label="Mean E2E")
        ax.plot(delays, e2e_p95,  "s--", color="#17becf", lw=2, ms=7, label="p95 E2E")
        ax.plot(delays, t_net,    "^--", color="#f0a500", lw=1.5, ms=7,
                label="Thermal network delay (mean)")
        ax.set_xlabel("Thermal link delay (ms)", fontsize=12)
        ax.set_ylabel("Latency (ms)", fontsize=12)
        ax.set_title("E2E Latency vs Thermal Delay\n(camera delay = 0)",
                     fontsize=13, fontweight="bold")
        ax.legend(fontsize=11)
        ax.yaxis.grid(True, alpha=0.4)
        ax.set_axisbelow(True)
        plt.tight_layout()
        out = os.path.join(sweep_dir, "latency_vs_thermal_delay.png")
        plt.savefig(out, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"  Wrote {out}")

    # ── 3. Mean E2E vs camera delay — multiple loss curves ───────────────
    loss_levels = sorted({e["imagery_loss"] for e in experiments
                          if _is_clean_network(e) and e["imagery_loss"] > 0.0})
    fig, ax = plt.subplots(figsize=(9, 5))
    cam_no_loss = cam_delay_series(loss=0.0)
    if cam_no_loss:
        delays_0 = [p[0] for p in cam_no_loss]
        ax.plot(delays_0, [p[1]["mean_e2e_ms"] for p in cam_no_loss],
                "o-", color=PALETTE[0], lw=2, ms=8, label="loss = 0%")
    for idx, loss in enumerate(loss_levels):
        pts = cam_delay_series(loss=loss)
        pts = [(d, m) for (d, m) in pts
               if any(e["imagery_loss"] == loss and e["imagery_delay"] == d
                      for e in experiments)]
        if pts:
            ax.plot([p[0] for p in pts], [p[1]["mean_e2e_ms"] for p in pts],
                    "s--", color=PALETTE[idx + 1], lw=2, ms=8,
                    label=f"loss = {loss:.0f}%")
    ax.set_xlabel("Camera (imagery) link delay (ms)", fontsize=12)
    ax.set_ylabel("Mean E2E latency (ms)", fontsize=12)
    ax.set_title("Mean E2E Latency vs Camera Delay + Packet Loss",
                 fontsize=13, fontweight="bold")
    ax.legend(fontsize=11)
    ax.yaxis.grid(True, alpha=0.4)
    ax.set_axisbelow(True)
    plt.tight_layout()
    out = os.path.join(sweep_dir, "latency_vs_delay_and_loss.png")
    plt.savefig(out, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"  Wrote {out}")

    # ── 4. Clock sync: mean E2E + mean dt_s vs thermal clock offset ──────
    clock_offset_exps = sorted(
        [e for e in experiments
         if e["group"] == "clock"
         and e["thermal_clock_offset_ms"] > 0.0
         and e["thermal_clock_jitter_ms"] == 0.0
         and e["name"] in metrics],
        key=lambda e: e["thermal_clock_offset_ms"],
    )
    if clock_offset_exps:
        offsets  = [e["thermal_clock_offset_ms"] for e in clock_offset_exps]
        e2e_vals = [metrics[e["name"]]["mean_e2e_ms"]  for e in clock_offset_exps]
        dt_vals  = [metrics[e["name"]]["mean_dt_s"] * 1000 for e in clock_offset_exps]

        # Also append baseline as offset=0
        if "baseline" in metrics:
            offsets  = [0.0] + offsets
            e2e_vals = [metrics["baseline"]["mean_e2e_ms"]]  + e2e_vals
            dt_vals  = [metrics["baseline"]["mean_dt_s"] * 1000] + dt_vals

        fig, ax1 = plt.subplots(figsize=(9, 5))
        ax2 = ax1.twinx()
        ax1.plot(offsets, e2e_vals, "o-", color="#c44e52", lw=2, ms=8, label="Mean E2E (ms)")
        ax2.plot(offsets, dt_vals,  "s--", color="#4c72b0", lw=2, ms=7, label="Mean pair Δt (ms)")
        ax1.axvline(x=500, color="gray", linestyle=":", lw=1.5,
                    label="TIME_WINDOW_S = 500ms")
        ax1.set_xlabel("Thermal clock offset (ms)", fontsize=12)
        ax1.set_ylabel("Mean E2E latency (ms)", fontsize=12, color="#c44e52")
        ax2.set_ylabel("Mean fusion pair Δt (ms)", fontsize=12, color="#4c72b0")
        ax1.set_title("Effect of Thermal Clock Offset on Latency & Pair Matching",
                      fontsize=13, fontweight="bold")
        lines1, labs1 = ax1.get_legend_handles_labels()
        lines2, labs2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labs1 + labs2, fontsize=10)
        ax1.yaxis.grid(True, alpha=0.3)
        ax1.set_axisbelow(True)
        plt.tight_layout()
        out = os.path.join(sweep_dir, "clock_offset_effect.png")
        plt.savefig(out, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"  Wrote {out}")

    # ── 5. Summary table of all runs ────────────────────────────────────
    names_with_data = [e["name"] for e in experiments if e["name"] in metrics]
    if names_with_data:
        fig, ax = plt.subplots(figsize=(16, max(3, 0.40 * len(names_with_data) + 1.5)))
        ax.axis("off")
        col_labels = ["Run", "Group", "Events", "Mean E2E", "p95 E2E",
                      "Thermal net", "Imagery net", "Mean Δt"]
        rows_data = []
        for name in names_with_data:
            m = metrics[name]
            exp = next(e for e in experiments if e["name"] == name)
            rows_data.append([
                name,
                exp["group"],
                str(m["n"]),
                f"{m['mean_e2e_ms']:.1f} ms",
                f"{m['p95_e2e_ms']:.1f} ms",
                f"{m['t_net_mean']:.1f} ms",
                f"{m['i_net_mean']:.1f} ms",
                f"{m.get('mean_dt_s', 0)*1000:.1f} ms",
            ])
        tbl = ax.table(cellText=rows_data, colLabels=col_labels,
                       loc="center", cellLoc="center")
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(9)
        tbl.scale(1, 1.7)
        group_colors = {"network": "#eaf2fb", "clock": "#fef9e7", "distance": "#eafaf1"}
        for j in range(len(col_labels)):
            tbl[0, j].set_facecolor("#2c3e50")
            tbl[0, j].set_text_props(color="white", fontweight="bold")
        for i, name in enumerate(names_with_data):
            exp = next(e for e in experiments if e["name"] == name)
            bg = group_colors.get(exp["group"], "#f7f7f7")
            for j in range(len(col_labels)):
                tbl[i + 1, j].set_facecolor(bg)
        plt.title("Sweep Experiment Summary", fontsize=13, fontweight="bold", pad=12)
        plt.tight_layout()
        out = os.path.join(sweep_dir, "sweep_summary_table.png")
        plt.savefig(out, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"  Wrote {out}")

    print(f"\n  All sweep plots -> {sweep_dir}/")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run all wildfire sim experiments and generate sweep plots.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Single run seed (overrides --seeds). For Monte Carlo use --seeds.",
    )
    parser.add_argument(
        "--seeds", type=str, default="42",
        help='Comma-separated seeds or range "0-99".',
    )
    parser.add_argument("--duration", type=int, default=60,
                        help="Seconds per experiment (default: 60)")
    parser.add_argument("--outdir", default="data",
                        help="Root output directory (default: data)")
    parser.add_argument("--sync-threshold-ms", type=float, default=2000.0,
                        help="GPS pair-matching window for the controller (ms). Default 2000.")
    parser.add_argument("--only", nargs="+", metavar="NAME",
                        help="Run only these experiment names")
    parser.add_argument("--skip-groups", nargs="+", metavar="GROUP",
                        help="Skip experiments in these groups (network, clock, distance)")
    parser.add_argument("--plots-only", action="store_true",
                        help="Skip Mininet runs; regenerate sweep plots from existing results")
    args = parser.parse_args()

    exps = EXPERIMENTS
    if args.only:
        exps = [e for e in EXPERIMENTS if e["name"] in args.only]
        if not exps:
            print(f"[ERROR] No experiments matched {args.only}")
            print(f"  Valid names: {[e['name'] for e in EXPERIMENTS]}")
            sys.exit(1)
    if args.skip_groups:
        exps = [e for e in exps if e["group"] not in args.skip_groups]

    if args.seed is not None:
        seeds = [args.seed]
    else:
        seeds = parse_seeds(args.seeds)
    multi_seed = len(seeds) > 1

    os.makedirs(args.outdir, exist_ok=True)

    if not args.plots_only:
        print("[RUNNER] Cleaning up stale Mininet state ...")
        subprocess.run(["mn", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)

        print(f"\n[RUNNER] {len(exps)} experiment(s) × {len(seeds)} seed(s)")
        print(f"[RUNNER] seeds={seeds}  duration={args.duration}s  "
              f"outdir={args.outdir}  sync={args.sync_threshold_ms}ms\n")

        total_minutes = len(exps) * len(seeds) * (args.duration + 10) / 60
        print(f"[RUNNER] Estimated total time: ~{total_minutes:.0f} minutes\n")

        groups_running = sorted({e["group"] for e in exps})
        print(f"[RUNNER] Experiment groups: {groups_running}\n")

        for seed in seeds:
            root = result_root_for_seed(args.outdir, seed, multi_seed)
            os.makedirs(root, exist_ok=True)
            print(f"\n[RUNNER] --- Seed {seed} → {root} ---\n")
            for exp in exps:
                run_one(
                    exp=exp,
                    outdir=root,
                    seed=seed,
                    duration=args.duration,
                    sync_threshold_ms=args.sync_threshold_ms,
                )

        print("\n[RUNNER] All experiments done.\n")

    print("[RUNNER] Generating sweep-level plots ...")
    if multi_seed:
        for seed in seeds:
            root = result_root_for_seed(args.outdir, seed, multi_seed)
            plot_sweeps(root, exps)
        mc_root = os.path.join(args.outdir, "monte_carlo_aggregate")
        os.makedirs(mc_root, exist_ok=True)
        merged = merge_metrics_across_seeds(args.outdir, seeds, exps)
        if merged:
            print(f"[RUNNER] Monte Carlo aggregate → {mc_root}/sweep_plots/")
            plot_sweeps(mc_root, exps, metrics=merged)
    else:
        root = result_root_for_seed(args.outdir, seeds[0], False)
        plot_sweeps(root, exps)

    print(f"\n[RUNNER] Complete.  Results under: {os.path.abspath(args.outdir)}/")


if __name__ == "__main__":
    main()
