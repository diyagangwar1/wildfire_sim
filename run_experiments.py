"""
Automated experiment runner — Wildfire Multi-Drone Simulation.

Runs every controlled sweep Prof Mohanty requested, back-to-back, with no
manual intervention.  After all runs it generates:
  • Per-run plots  (via analyze_latency.py)
  • Sweep-level plots (latency-focused; detection-rate sweeps removed as misleading in sim):
      - Mean / p95 E2E latency vs camera delay (thermal delay = 0)
      - Mean / p95 E2E latency vs thermal delay (camera delay = 0)
      - E2E vs camera delay with packet-loss curves (rolling decision rate removed)
      - Monte Carlo: with multiple --seeds, writes aggregate sweep under
        <outdir>/monte_carlo_aggregate/sweep_plots/

Usage (must be run as root for Mininet):
    sudo python3 run_experiments.py [options]

Parallel runs (many seeds):
    Mininet + OVS on one machine: run experiments sequentially (default). Starting
    multiple Mininet sessions in parallel on the same host usually corrupts state.
    To scale: use one process per machine/VM, a job array (Slurm: --array), or
    cloud workers each with its own --seeds value. Post-processing (analyze_latency,
    compare_seeds) can run in parallel safely.

Key options:
    --seeds STR           Comma-separated seeds or range "0-99" (Monte Carlo).
                          Single seed default: 42.  With multiple seeds, each run
                          goes to <outdir>_seed<k>/ (e.g. results_seed7/baseline).
    --duration SECS       Seconds to run each experiment (default 60)
    --outdir DIR          Root results directory (default results)
    --sync-threshold-ms   GPS pair-matching window in ms (default 2000 = 2 s)
    --only NAME [NAME...] Run only the named experiments (space-separated)
    --plots-only          Skip running experiments; only generate plots from
                          existing results in --outdir
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
# Experiment matrix
# Each tuple: (name, thermal_delay_ms, imagery_delay_ms, thermal_loss_pct, imagery_loss_pct)
# ---------------------------------------------------------------------------
EXPERIMENTS: List[Tuple[str, float, float, float, float]] = [
    # Baseline — no stress
    ("baseline",               0,    0,   0.0, 0.0),

    # Camera (imagery) delay sweep — thermal fixed at 0, no loss
    ("cam_delay_10ms",         0,   10,   0.0, 0.0),
    ("cam_delay_50ms",         0,   50,   0.0, 0.0),
    ("cam_delay_100ms",        0,  100,   0.0, 0.0),
    ("cam_delay_500ms",        0,  500,   0.0, 0.0),
    ("cam_delay_1000ms",       0, 1000,   0.0, 0.0),

    # Thermal delay sweep — camera fixed at 0, no loss
    ("thermal_delay_10ms",    10,    0,   0.0, 0.0),
    ("thermal_delay_50ms",    50,    0,   0.0, 0.0),
    ("thermal_delay_100ms",  100,    0,   0.0, 0.0),
    ("thermal_delay_500ms",  500,    0,   0.0, 0.0),
    ("thermal_delay_1000ms",1000,    0,   0.0, 0.0),

    # Camera delay + imagery packet loss (no thermal delay)
    ("cam_10ms_loss1pct",      0,   10,   0.0, 1.0),
    ("cam_100ms_loss1pct",     0,  100,   0.0, 1.0),
    ("cam_100ms_loss5pct",     0,  100,   0.0, 5.0),
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
def run_one(
    name: str,
    thermal_delay: float,
    imagery_delay: float,
    thermal_loss: float,
    imagery_loss: float,
    outdir: str,
    seed: int,
    duration: int,
    sync_threshold_ms: float,
    worker_base_drop_prob: float,
) -> None:
    """Run a single experiment via mn_topo.py --auto (fully isolated subprocess)."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    exp_dir = os.path.abspath(os.path.join(outdir, name))
    os.makedirs(exp_dir, exist_ok=True)

    print(f"\n{'='*64}")
    print(f"  Experiment : {name}")
    print(f"  thermal delay={thermal_delay}ms   imagery delay={imagery_delay}ms")
    print(f"  thermal loss={thermal_loss}%      imagery loss={imagery_loss}%")
    print(f"  seed={seed}   duration={duration}s   sync_threshold={sync_threshold_ms}ms")
    print(f"  worker_base_drop_prob={worker_base_drop_prob}")
    print(f"  output -> {exp_dir}")
    print(f"{'='*64}")

    # Each experiment runs as its own subprocess so Mininet starts completely
    # fresh — no residual OVS bridge / namespace state from previous runs.
    cmd = [
        sys.executable,
        os.path.join(script_dir, "mn_topo.py"),
        "--thermal-delay", str(thermal_delay),
        "--imagery-delay", str(imagery_delay),
        "--thermal-loss",  str(thermal_loss),
        "--imagery-loss",  str(imagery_loss),
        "--auto",
        "--outdir",            exp_dir,
        "--seed",              str(seed),
        "--duration",          str(duration),
        "--sync-threshold-ms", str(sync_threshold_ms),
        "--base-drop-prob", str(worker_base_drop_prob),
    ]
    subprocess.run(cmd, check=False)

    # Clean residual OVS state before the next run
    subprocess.run(["mn", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)

    # Per-run analysis
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
        print(f"  Check {exp_dir}/controller.log  and  {exp_dir}/thermal.log for details.")


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
    k = max(0, min(k, len(s) - 1))
    return s[k]


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

    e2e     = [float(r["e2e_ms"]) for r in rows if "e2e_ms" in r]
    raw     = [bool(r.get("raw_signal", False))   for r in rows]
    dec     = [bool(r.get("decision", False))      for r in rows]
    t_net   = [r.get("thermal_net_ns", 0) / 1e6   for r in rows]
    i_net   = [r.get("imagery_net_ns", 0) / 1e6   for r in rows]

    return {
        "n":             len(rows),
        "mean_e2e_ms":   _mean(e2e),
        "median_e2e_ms": _median(e2e),
        "p95_e2e_ms":    _pctl(e2e, 95),
        "raw_rate":      sum(raw) / max(1, len(raw)),
        "dec_rate":      sum(dec) / max(1, len(dec)),
        "t_net_mean":    _mean(t_net),
        "i_net_mean":    _mean(i_net),
    }


# ---------------------------------------------------------------------------
# Sweep-level plots
# ---------------------------------------------------------------------------
def merge_metrics_across_seeds(
    outdir: str, seeds: List[int], experiments: List[Tuple]
) -> Dict[str, Dict[str, Any]]:
    """Mean-of-means across Monte Carlo seeds for each experiment name."""
    merged: Dict[str, Dict[str, Any]] = {}
    for name, *_ in experiments:
        series: List[Dict[str, Any]] = []
        for s in seeds:
            root = f"{outdir}_seed{s}"
            m = compute_metrics(os.path.join(root, name, "latency_log.jsonl"))
            if m:
                series.append(m)
        if not series:
            continue
        merged[name] = {
            "n": int(round(statistics.mean([x["n"] for x in series]))),
            "mean_e2e_ms": statistics.mean([x["mean_e2e_ms"] for x in series]),
            "median_e2e_ms": statistics.mean([x["median_e2e_ms"] for x in series]),
            "p95_e2e_ms": statistics.mean([x["p95_e2e_ms"] for x in series]),
            "raw_rate": statistics.mean([x["raw_rate"] for x in series]),
            "dec_rate": statistics.mean([x["dec_rate"] for x in series]),
            "t_net_mean": statistics.mean([x["t_net_mean"] for x in series]),
            "i_net_mean": statistics.mean([x["i_net_mean"] for x in series]),
        }
    return merged


def plot_sweeps(
    outdir: str,
    experiments: List[Tuple],
    metrics: Optional[Dict[str, Dict[str, Any]]] = None,
) -> None:
    """Generate sweep-level comparison plots (latency-focused)."""
    sweep_dir = os.path.join(outdir, "sweep_plots")
    os.makedirs(sweep_dir, exist_ok=True)

    if metrics is None:
        metrics = {}
        for name, *_ in experiments:
            m = compute_metrics(os.path.join(outdir, name, "latency_log.jsonl"))
            if m:
                metrics[name] = m

    if not metrics:
        print("[SWEEPS] No results found — skipping sweep plots.")
        return

    # Helpers to extract sweep series
    def cam_delay_series(loss: float = 0.0):
        """Camera delay sweep: thermal=0, thermal_loss=0, imagery_loss=loss."""
        pts = []
        for name, t_del, i_del, t_loss, i_loss in experiments:
            if t_del == 0 and t_loss == 0.0 and i_loss == loss and name in metrics:
                pts.append((i_del, metrics[name]))
        return sorted(pts, key=lambda x: x[0])

    def thermal_delay_series():
        """Thermal delay sweep: imagery=0, both losses=0."""
        pts = []
        for name, t_del, i_del, t_loss, i_loss in experiments:
            if i_del == 0 and t_loss == 0.0 and i_loss == 0.0 and name in metrics:
                pts.append((t_del, metrics[name]))
        return sorted(pts, key=lambda x: x[0])

    cam_pts = cam_delay_series(loss=0.0)
    th_pts = thermal_delay_series()

    # ── 1. Mean + p95 E2E vs camera delay ────────────────────────────────────
    if cam_pts:
        delays = [p[0] for p in cam_pts]
        e2e_mean = [p[1]["mean_e2e_ms"] for p in cam_pts]
        e2e_p95 = [p[1]["p95_e2e_ms"] for p in cam_pts]
        i_net = [p[1]["i_net_mean"] for p in cam_pts]

        fig, ax = plt.subplots(figsize=(9, 5))
        ax.plot(delays, e2e_mean, "o-", color="#c44e52", linewidth=2, markersize=8, label="Mean E2E")
        ax.plot(delays, e2e_p95, "s--", color="#8c564b", linewidth=2, markersize=7, label="p95 E2E")
        ax.plot(delays, i_net, "^--", color="#f0a500", linewidth=1.5, markersize=7,
                label="Imagery network delay (mean)")
        ax.set_xlabel("Camera (imagery) link delay (ms)", fontsize=12)
        ax.set_ylabel("Latency (ms)", fontsize=12)
        ax.set_title("E2E Latency vs Camera Delay\n(thermal delay = 0)", fontsize=13, fontweight="bold")
        ax.legend(fontsize=11)
        ax.yaxis.grid(True, alpha=0.4)
        ax.set_axisbelow(True)
        plt.tight_layout()
        out = os.path.join(sweep_dir, "latency_vs_camera_delay.png")
        plt.savefig(out, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"  Wrote {out}")

    # ── 2. Mean + p95 E2E vs thermal delay ────────────────────────────────────
    if th_pts:
        delays = [p[0] for p in th_pts]
        e2e_mean = [p[1]["mean_e2e_ms"] for p in th_pts]
        e2e_p95 = [p[1]["p95_e2e_ms"] for p in th_pts]
        t_net = [p[1]["t_net_mean"] for p in th_pts]

        fig, ax = plt.subplots(figsize=(9, 5))
        ax.plot(delays, e2e_mean, "o-", color="#4c72b0", linewidth=2, markersize=8, label="Mean E2E")
        ax.plot(delays, e2e_p95, "s--", color="#17becf", linewidth=2, markersize=7, label="p95 E2E")
        ax.plot(delays, t_net, "^--", color="#f0a500", linewidth=1.5, markersize=7,
                label="Thermal network delay (mean)")
        ax.set_xlabel("Thermal link delay (ms)", fontsize=12)
        ax.set_ylabel("Latency (ms)", fontsize=12)
        ax.set_title("E2E Latency vs Thermal Delay\n(camera delay = 0)", fontsize=13, fontweight="bold")
        ax.legend(fontsize=11)
        ax.yaxis.grid(True, alpha=0.4)
        ax.set_axisbelow(True)
        plt.tight_layout()
        out = os.path.join(sweep_dir, "latency_vs_thermal_delay.png")
        plt.savefig(out, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"  Wrote {out}")

    # ── 3. Mean E2E vs camera delay — multiple loss curves (latency, not detection rate)
    loss_levels = sorted({i_loss for _, _, _, _, i_loss in experiments if i_loss > 0.0})
    fig, ax = plt.subplots(figsize=(9, 5))
    cam_no_loss = cam_delay_series(loss=0.0)
    if cam_no_loss:
        delays_0 = [p[0] for p in cam_no_loss]
        e0 = [p[1]["mean_e2e_ms"] for p in cam_no_loss]
        ax.plot(delays_0, e0, "o-", color=PALETTE[0], linewidth=2,
                markersize=8, label="loss = 0% (mean E2E)")
    for idx, loss in enumerate(loss_levels):
        pts = cam_delay_series(loss=loss)
        pts = [(d, m) for (d, m) in pts
               if any(e[4] == loss and e[2] == d for e in experiments)]
        if pts:
            delays = [p[0] for p in pts]
            e2e_m = [p[1]["mean_e2e_ms"] for p in pts]
            ax.plot(delays, e2e_m, "s--", color=PALETTE[idx + 1], linewidth=2,
                    markersize=8, label=f"loss = {loss:.0f}% (mean E2E)")
    ax.set_xlabel("Camera (imagery) link delay (ms)", fontsize=12)
    ax.set_ylabel("Mean E2E latency (ms)", fontsize=12)
    ax.set_title("Mean E2E Latency vs Camera Delay + Packet Loss", fontsize=13, fontweight="bold")
    ax.legend(fontsize=11)
    ax.yaxis.grid(True, alpha=0.4)
    ax.set_axisbelow(True)
    plt.tight_layout()
    out = os.path.join(sweep_dir, "latency_vs_delay_and_loss.png")
    plt.savefig(out, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"  Wrote {out}")

    # ── 4. Summary table of all runs (latency-first) ─────────────────────────
    names_with_data = [name for name, *_ in experiments if name in metrics]
    if names_with_data:
        fig, ax = plt.subplots(figsize=(14, max(3, 0.45 * len(names_with_data) + 1.5)))
        ax.axis("off")
        col_labels = ["Run", "Events", "Mean E2E", "p95 E2E", "Median E2E",
                      "Thermal net", "Imagery net"]
        rows_data = []
        for name in names_with_data:
            m = metrics[name]
            rows_data.append([
                name,
                str(m["n"]),
                f"{m['mean_e2e_ms']:.1f} ms",
                f"{m['p95_e2e_ms']:.1f} ms",
                f"{m['median_e2e_ms']:.1f} ms",
                f"{m['t_net_mean']:.1f} ms",
                f"{m['i_net_mean']:.1f} ms",
            ])
        tbl = ax.table(cellText=rows_data, colLabels=col_labels,
                       loc="center", cellLoc="center")
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(9)
        tbl.scale(1, 1.8)
        for j in range(len(col_labels)):
            tbl[0, j].set_facecolor("#2c3e50")
            tbl[0, j].set_text_props(color="white", fontweight="bold")
        for i in range(len(rows_data)):
            bg = "#f7f7f7" if i % 2 == 0 else "#ffffff"
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
        help="Single run seed (overrides --seeds if set). For Monte Carlo use --seeds instead.",
    )
    parser.add_argument(
        "--seeds", type=str, default="42",
        help='Comma-separated seeds or range "0-99" (e.g. "0,1,2" or "0-49").',
    )
    parser.add_argument("--duration", type=int, default=60,
                        help="Seconds to run each experiment (default: 60)")
    parser.add_argument("--outdir", default="data",
                        help="Root output directory; each run gets a sub-folder (default: results)")
    parser.add_argument("--sync-threshold-ms", type=float, default=2000.0,
                        help=(
                            "GPS pair-matching window for the controller (ms). "
                            "Default 2000 ms (2 s). Smaller values tighten tx alignment; "
                            "larger values allow more pairing under jitter (may include staler pairs)."
                        ))
    parser.add_argument("--only", nargs="+", metavar="NAME",
                        help="Run only these experiment names (use quotes for spaces)")
    parser.add_argument("--plots-only", action="store_true",
                        help="Skip Mininet runs; just generate sweep plots from existing results")
    parser.add_argument(
        "--worker-base-drop-prob", type=float, default=0.0,
        help=(
            "Worker-side drop probability before distance scaling (0 = nominal). "
            "Use a small positive value (e.g. 0.1) for stress / worst-case drop experiments."
        ),
    )
    args = parser.parse_args()

    exps = EXPERIMENTS
    if args.only:
        exps = [e for e in EXPERIMENTS if e[0] in args.only]
        if not exps:
            valid = [e[0] for e in EXPERIMENTS]
            print(f"[ERROR] No experiments matched {args.only}")
            print(f"  Valid names: {valid}")
            sys.exit(1)

    if args.seed is not None:
        seeds = [args.seed]
    else:
        seeds = parse_seeds(args.seeds)
    multi_seed = len(seeds) > 1

    os.makedirs(args.outdir, exist_ok=True)

    if not args.plots_only:
        # Clean up any stale Mininet/OVS state from previous crashed runs
        print("[RUNNER] Cleaning up stale Mininet state ...")
        subprocess.run(["mn", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)

        print(f"\n[RUNNER] {len(exps)} experiment(s) × {len(seeds)} seed(s)")
        print(f"[RUNNER] seeds={seeds}  duration={args.duration}s  "
              f"outdir={args.outdir}  sync={args.sync_threshold_ms}ms\n")

        total_minutes = len(exps) * len(seeds) * (args.duration + 10) / 60
        print(f"[RUNNER] Estimated total time: ~{total_minutes:.0f} minutes\n")
        print(
            "[RUNNER] Note: Mininet is run sequentially per experiment (one topology at a time). "
            "For 100+ seeds, consider shorter --duration or a job array on multiple hosts.\n"
        )

        for seed in seeds:
            root = result_root_for_seed(args.outdir, seed, multi_seed)
            os.makedirs(root, exist_ok=True)
            print(f"\n[RUNNER] --- Seed {seed} → {root} ---\n")
            for name, t_delay, i_delay, t_loss, i_loss in exps:
                run_one(
                    name=name,
                    thermal_delay=t_delay,
                    imagery_delay=i_delay,
                    thermal_loss=t_loss,
                    imagery_loss=i_loss,
                    outdir=root,
                    seed=seed,
                    duration=args.duration,
                    sync_threshold_ms=args.sync_threshold_ms,
                    worker_base_drop_prob=args.worker_base_drop_prob,
                )

        print("\n[RUNNER] All experiments done.\n")

    # Generate sweep plots from all runs (including any pre-existing ones)
    print("[RUNNER] Generating sweep-level plots ...")
    if multi_seed:
        for seed in seeds:
            root = result_root_for_seed(args.outdir, seed, multi_seed)
            print(f"[RUNNER] Sweeps for seed {seed} → {root}/sweep_plots/")
            plot_sweeps(root, EXPERIMENTS)
        mc_root = os.path.join(args.outdir, "monte_carlo_aggregate")
        os.makedirs(mc_root, exist_ok=True)
        merged = merge_metrics_across_seeds(args.outdir, seeds, EXPERIMENTS)
        if merged:
            print(f"[RUNNER] Monte Carlo aggregate (mean across {len(seeds)} seeds) → {mc_root}/sweep_plots/")
            plot_sweeps(mc_root, EXPERIMENTS, metrics=merged)
        else:
            print("[RUNNER] No merged metrics for Monte Carlo aggregate (missing logs).")
    else:
        root = result_root_for_seed(args.outdir, seeds[0], False)
        plot_sweeps(root, EXPERIMENTS)

    print(f"\n[RUNNER] Complete.  Results under: {os.path.abspath(args.outdir)}/")
    if multi_seed:
        print(f"[RUNNER] Per-seed sweep plots: {args.outdir}_seed<k>/sweep_plots/")
        print(f"[RUNNER] Aggregate sweeps: {args.outdir}/monte_carlo_aggregate/sweep_plots/")
    else:
        print(f"[RUNNER] Sweep plots: {os.path.abspath(root)}/sweep_plots/")


if __name__ == "__main__":
    main()
