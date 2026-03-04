"""
Automated experiment runner — Wildfire Multi-Drone Simulation.

Runs every controlled sweep Prof Mohanty requested, back-to-back, with no
manual intervention.  After all runs it generates:
  • Per-run plots  (via analyze_latency.py)
  • Sweep-level plots:
      - Detection rate vs camera delay  (thermal delay = 0)
      - Detection rate vs thermal delay (camera delay = 0)
      - Mean E2E latency vs camera delay
      - Mean E2E latency vs thermal delay
      - Detection rate vs camera delay + loss (multiple curves)
      - Latency over time with drone trajectory overlay (per run)

Usage (must be run as root for Mininet):
    sudo python3 run_experiments.py [options]

Key options:
    --seed INT            Same seed for all runs (default 42)
    --duration SECS       Seconds to run each experiment (default 60)
    --outdir DIR          Root results directory (default results)
    --sync-threshold-ms   GPS sync window for controller (default 250)
    --only NAME [NAME...] Run only the named experiments (space-separated)
    --plots-only          Skip running experiments; only generate plots from
                          existing results in --outdir
"""

from __future__ import annotations

import argparse
import json
import math
import os
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
        "--base-drop-prob",    "0",
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
        "raw_rate":      sum(raw) / max(1, len(raw)),
        "dec_rate":      sum(dec) / max(1, len(dec)),
        "t_net_mean":    _mean(t_net),
        "i_net_mean":    _mean(i_net),
    }


# ---------------------------------------------------------------------------
# Sweep-level plots
# ---------------------------------------------------------------------------
def plot_sweeps(outdir: str, experiments: List[Tuple]) -> None:
    """Generate the sweep-level comparison plots the professor requested."""
    sweep_dir = os.path.join(outdir, "sweep_plots")
    os.makedirs(sweep_dir, exist_ok=True)

    # Load available metrics
    metrics: Dict[str, Dict] = {}
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

    # ── 1. Detection rate vs camera delay ────────────────────────────────────
    cam_pts = cam_delay_series(loss=0.0)
    if cam_pts:
        delays = [p[0] for p in cam_pts]
        raw_rates = [100 * p[1]["raw_rate"]  for p in cam_pts]
        dec_rates = [100 * p[1]["dec_rate"]  for p in cam_pts]

        fig, ax = plt.subplots(figsize=(9, 5))
        ax.plot(delays, raw_rates, "o-", color="#dd8452", linewidth=2,
                markersize=8, label="Raw signal rate")
        ax.plot(delays, dec_rates, "s-", color="#c44e52", linewidth=2,
                markersize=8, label="Rolling decision rate")
        ax.set_xlabel("Camera (imagery) link delay (ms)", fontsize=12)
        ax.set_ylabel("Detection rate (%)", fontsize=12)
        ax.set_title("Detection Rate vs Camera Delay\n(thermal delay = 0)", fontsize=13, fontweight="bold")
        ax.legend(fontsize=11)
        ax.yaxis.grid(True, alpha=0.4)
        ax.set_axisbelow(True)
        for x, r, d in zip(delays, raw_rates, dec_rates):
            ax.annotate(f"{r:.1f}%", (x, r), textcoords="offset points", xytext=(0, 8),
                        ha="center", fontsize=8, color="#dd8452")
            ax.annotate(f"{d:.1f}%", (x, d), textcoords="offset points", xytext=(0, -14),
                        ha="center", fontsize=8, color="#c44e52")
        plt.tight_layout()
        out = os.path.join(sweep_dir, "detection_rate_vs_camera_delay.png")
        plt.savefig(out, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"  Wrote {out}")

    # ── 2. Detection rate vs thermal delay ───────────────────────────────────
    th_pts = thermal_delay_series()
    if th_pts:
        delays = [p[0] for p in th_pts]
        raw_rates = [100 * p[1]["raw_rate"] for p in th_pts]
        dec_rates = [100 * p[1]["dec_rate"] for p in th_pts]

        fig, ax = plt.subplots(figsize=(9, 5))
        ax.plot(delays, raw_rates, "o-", color="#4c72b0", linewidth=2,
                markersize=8, label="Raw signal rate")
        ax.plot(delays, dec_rates, "s-", color="#8172b2", linewidth=2,
                markersize=8, label="Rolling decision rate")
        ax.set_xlabel("Thermal link delay (ms)", fontsize=12)
        ax.set_ylabel("Detection rate (%)", fontsize=12)
        ax.set_title("Detection Rate vs Thermal Delay\n(camera delay = 0)", fontsize=13, fontweight="bold")
        ax.legend(fontsize=11)
        ax.yaxis.grid(True, alpha=0.4)
        ax.set_axisbelow(True)
        plt.tight_layout()
        out = os.path.join(sweep_dir, "detection_rate_vs_thermal_delay.png")
        plt.savefig(out, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"  Wrote {out}")

    # ── 3. Mean E2E latency vs camera delay ──────────────────────────────────
    if cam_pts:
        delays = [p[0] for p in cam_pts]
        e2e = [p[1]["mean_e2e_ms"] for p in cam_pts]
        i_net = [p[1]["i_net_mean"] for p in cam_pts]

        fig, ax = plt.subplots(figsize=(9, 5))
        ax.plot(delays, e2e, "o-", color="#c44e52", linewidth=2, markersize=8, label="Mean E2E")
        ax.plot(delays, i_net, "^--", color="#f0a500", linewidth=1.5, markersize=7,
                label="Imagery network delay")
        ax.set_xlabel("Camera (imagery) link delay (ms)", fontsize=12)
        ax.set_ylabel("Latency (ms)", fontsize=12)
        ax.set_title("Mean E2E Latency vs Camera Delay\n(thermal delay = 0)", fontsize=13, fontweight="bold")
        ax.legend(fontsize=11)
        ax.yaxis.grid(True, alpha=0.4)
        ax.set_axisbelow(True)
        for x, y in zip(delays, e2e):
            ax.annotate(f"{y:.0f}ms", (x, y), textcoords="offset points",
                        xytext=(0, 8), ha="center", fontsize=8)
        plt.tight_layout()
        out = os.path.join(sweep_dir, "latency_vs_camera_delay.png")
        plt.savefig(out, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"  Wrote {out}")

    # ── 4. Mean E2E latency vs thermal delay ─────────────────────────────────
    if th_pts:
        delays = [p[0] for p in th_pts]
        e2e = [p[1]["mean_e2e_ms"] for p in th_pts]
        t_net = [p[1]["t_net_mean"] for p in th_pts]

        fig, ax = plt.subplots(figsize=(9, 5))
        ax.plot(delays, e2e, "o-", color="#4c72b0", linewidth=2, markersize=8, label="Mean E2E")
        ax.plot(delays, t_net, "^--", color="#f0a500", linewidth=1.5, markersize=7,
                label="Thermal network delay")
        ax.set_xlabel("Thermal link delay (ms)", fontsize=12)
        ax.set_ylabel("Latency (ms)", fontsize=12)
        ax.set_title("Mean E2E Latency vs Thermal Delay\n(camera delay = 0)", fontsize=13, fontweight="bold")
        ax.legend(fontsize=11)
        ax.yaxis.grid(True, alpha=0.4)
        ax.set_axisbelow(True)
        plt.tight_layout()
        out = os.path.join(sweep_dir, "latency_vs_thermal_delay.png")
        plt.savefig(out, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"  Wrote {out}")

    # ── 5. Detection rate vs camera delay — multiple loss curves ─────────────
    loss_levels = sorted({i_loss for _, _, _, _, i_loss in experiments if i_loss > 0.0})
    fig, ax = plt.subplots(figsize=(9, 5))
    # No-loss baseline curve
    cam_no_loss = cam_delay_series(loss=0.0)
    if cam_no_loss:
        delays_0 = [p[0] for p in cam_no_loss]
        dec_0 = [100 * p[1]["dec_rate"] for p in cam_no_loss]
        ax.plot(delays_0, dec_0, "o-", color=PALETTE[0], linewidth=2,
                markersize=8, label="loss = 0%")
    for idx, loss in enumerate(loss_levels):
        pts = cam_delay_series(loss=loss)
        # Only keep experiments that had this loss value
        pts = [(d, m) for (d, m) in pts
               if any(e[4] == loss and e[2] == d for e in experiments)]
        if pts:
            delays = [p[0] for p in pts]
            dec = [100 * p[1]["dec_rate"] for p in pts]
            ax.plot(delays, dec, "s--", color=PALETTE[idx + 1], linewidth=2,
                    markersize=8, label=f"loss = {loss:.0f}%")
    ax.set_xlabel("Camera (imagery) link delay (ms)", fontsize=12)
    ax.set_ylabel("Rolling decision rate (%)", fontsize=12)
    ax.set_title("Detection Rate vs Camera Delay + Packet Loss", fontsize=13, fontweight="bold")
    ax.legend(fontsize=11)
    ax.yaxis.grid(True, alpha=0.4)
    ax.set_axisbelow(True)
    plt.tight_layout()
    out = os.path.join(sweep_dir, "detection_rate_vs_delay_and_loss.png")
    plt.savefig(out, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"  Wrote {out}")

    # ── 6. Summary table of all runs ─────────────────────────────────────────
    names_with_data = [name for name, *_ in experiments if name in metrics]
    if names_with_data:
        fig, ax = plt.subplots(figsize=(14, max(3, 0.45 * len(names_with_data) + 1.5)))
        ax.axis("off")
        col_labels = ["Run", "Events", "Mean E2E", "Median E2E",
                      "Raw rate", "Decision rate", "Thermal net", "Imagery net"]
        rows_data = []
        for name in names_with_data:
            m = metrics[name]
            rows_data.append([
                name,
                str(m["n"]),
                f"{m['mean_e2e_ms']:.1f} ms",
                f"{m['median_e2e_ms']:.1f} ms",
                f"{100*m['raw_rate']:.1f}%",
                f"{100*m['dec_rate']:.1f}%",
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
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for workers — same across all runs (default: 42)")
    parser.add_argument("--duration", type=int, default=60,
                        help="Seconds to run each experiment (default: 60)")
    parser.add_argument("--outdir", default="results",
                        help="Root output directory; each run gets a sub-folder (default: results)")
    parser.add_argument("--sync-threshold-ms", type=float, default=250.0,
                        help=(
                            "GPS sync threshold for the controller (default: 250ms). "
                            "With independent workers, 250ms ≈ half the 500ms send period "
                            "and gives reliable pairing.  Use 5ms once workers are "
                            "GPS-synchronized."
                        ))
    parser.add_argument("--only", nargs="+", metavar="NAME",
                        help="Run only these experiment names (use quotes for spaces)")
    parser.add_argument("--plots-only", action="store_true",
                        help="Skip Mininet runs; just generate sweep plots from existing results")
    args = parser.parse_args()

    exps = EXPERIMENTS
    if args.only:
        exps = [e for e in EXPERIMENTS if e[0] in args.only]
        if not exps:
            valid = [e[0] for e in EXPERIMENTS]
            print(f"[ERROR] No experiments matched {args.only}")
            print(f"  Valid names: {valid}")
            sys.exit(1)

    os.makedirs(args.outdir, exist_ok=True)

    if not args.plots_only:
        # Clean up any stale Mininet/OVS state from previous crashed runs
        print("[RUNNER] Cleaning up stale Mininet state ...")
        subprocess.run(["mn", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)

        print(f"\n[RUNNER] {len(exps)} experiment(s) to run")
        print(f"[RUNNER] seed={args.seed}  duration={args.duration}s  "
              f"outdir={args.outdir}  sync={args.sync_threshold_ms}ms\n")

        total_minutes = len(exps) * (args.duration + 10) / 60
        print(f"[RUNNER] Estimated total time: ~{total_minutes:.0f} minutes\n")

        for name, t_delay, i_delay, t_loss, i_loss in exps:
            run_one(
                name=name,
                thermal_delay=t_delay,
                imagery_delay=i_delay,
                thermal_loss=t_loss,
                imagery_loss=i_loss,
                outdir=args.outdir,
                seed=args.seed,
                duration=args.duration,
                sync_threshold_ms=args.sync_threshold_ms,
            )

        print("\n[RUNNER] All experiments done.\n")

    # Generate sweep plots from all runs (including any pre-existing ones)
    print("[RUNNER] Generating sweep-level plots ...")
    plot_sweeps(args.outdir, EXPERIMENTS)

    print(f"\n[RUNNER] Complete.  Results in: {os.path.abspath(args.outdir)}/")
    print(f"[RUNNER] Sweep plots: {os.path.abspath(args.outdir)}/sweep_plots/")


if __name__ == "__main__":
    main()
