#!/usr/bin/env python3
"""
compare_seeds.py  —  cross-seed aggregate comparison visuals

Discovers results_mc50_seed*/ directories (canonical Apr-15 dataset, 50 seeds,
20 experiments each including clock-sync and distance-drop experiments).
Writes results/results_combined/ with all comparison figures.
"""

import argparse
import collections
import glob
import json
import os
import pathlib
import warnings
from typing import Dict, List, Tuple
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
from scipy import stats

warnings.filterwarnings("ignore")

# ── paths (OUT_DIR may be overridden by main()) ────────────────────────────
OUT_DIR = pathlib.Path("results/results_combined")


def _discover_seeds() -> Tuple[Dict[int, pathlib.Path], List[int]]:
    dirs: dict[int, pathlib.Path] = {}
    # Canonical layout: data/results_mc50_seedN/
    search_patterns = ["data/results_mc50_seed*", "results_mc50_seed*", "data/results_seed*", "results_seed*"]
    for pattern in search_patterns:
        for p in sorted(glob.glob(pattern)):
            if not os.path.isdir(p):
                continue
            base = os.path.basename(p)
            for prefix in ("results_mc50_seed", "results_seed"):
                if base.startswith(prefix):
                    try:
                        s = int(base[len(prefix):])
                        dirs[s] = pathlib.Path(p)
                    except ValueError:
                        pass
        if dirs:
            break
    seeds = sorted(dirs.keys())
    return dirs, seeds


SEED_DIRS, SEEDS = _discover_seeds()

# ── experiment ordering ────────────────────────────────────────────────────
CAM_EXPS = [
    ("baseline",        0,    0),
    ("cam_delay_10ms",  10,   0),
    ("cam_delay_50ms",  50,   0),
    ("cam_delay_100ms", 100,  0),
    ("cam_delay_500ms", 500,  0),
    ("cam_delay_1000ms",1000, 0),
]
TH_EXPS = [
    ("baseline",          0,    0),
    ("thermal_delay_10ms",  10,  0),
    ("thermal_delay_50ms",  50,  0),
    ("thermal_delay_100ms", 100, 0),
    ("thermal_delay_500ms", 500, 0),
    ("thermal_delay_1000ms",1000,0),
]
LOSS_EXPS = [
    ("cam_delay_10ms",       10,  0.0),
    ("cam_10ms_loss1pct",    10,  1.0),
    ("cam_delay_100ms",     100,  0.0),
    ("cam_100ms_loss1pct",  100,  1.0),
    ("cam_100ms_loss5pct",  100,  5.0),
]
CLOCK_EXPS = [
    ("baseline",          "Baseline",          0),
    ("clock_offset_100ms","Offset ±100ms",   100),
    ("clock_offset_500ms","Offset ±500ms",   500),
    ("clock_offset_1000ms","Offset ±1000ms",1000),
    ("clock_jitter_50ms", "Jitter 50ms",      50),
    ("clock_jitter_200ms","Jitter 200ms",    200),
]

TAB10 = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
           "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]


def _palette(seeds: list[int]) -> dict[int, str]:
    return {s: TAB10[i % len(TAB10)] for i, s in enumerate(seeds)}


PALETTE = _palette(SEEDS)
SEED_LABELS = {s: f"seed {s}" for s in SEEDS}

# ── helpers ────────────────────────────────────────────────────────────────
def load(seed, exp_name):
    p = SEED_DIRS[seed] / exp_name / "latency_log.jsonl"
    if not p.exists():
        return pd.DataFrame()
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    return pd.DataFrame(rows)

def e2e_stats(df):
    if df.empty:
        return dict(mean=np.nan, median=np.nan, p95=np.nan, p99=np.nan)
    v = df["e2e_ms"].values
    return dict(
        mean=float(np.mean(v)),
        median=float(np.median(v)),
        p95=float(np.percentile(v, 95)),
        p99=float(np.percentile(v, 99)),
    )

def agg(exps, metric_fn):
    """Return {exp_name: {seed: metric_dict}} for all seeds."""
    out = {}
    for name, *_ in exps:
        out[name] = {}
        for s in SEEDS:
            df = load(s, name)
            out[name][s] = metric_fn(df)
    return out

def mean_err(values):
    """Return mean ± 1-SD across seeds, filtering NaN."""
    v = [x for x in values if not np.isnan(x)]
    if not v:
        return np.nan, 0
    return np.mean(v), np.std(v, ddof=0) if len(v) > 1 else 0

# ─────────────────────────────────────────────────────────────────────────
# 1.  E2E Latency vs Camera / Thermal Delay  (mean ± SD across seeds)
# ─────────────────────────────────────────────────────────────────────────
def plot_latency_sweep():
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    fig.suptitle(
        f"End-to-End Latency vs Network Delay\n(mean ± 1 SD across {len(SEEDS)} seeds)",
        fontsize=13, fontweight="bold",
    )

    for ax, exps, xlabel, title in [
        (axes[0], CAM_EXPS,  "Camera (imagery) link delay (ms)", "Camera Link Delay Sweep"),
        (axes[1], TH_EXPS,   "Thermal link delay (ms)",          "Thermal Link Delay Sweep"),
    ]:
        agg_data = agg(exps, e2e_stats)
        delays = [d for _, d, _ in exps]

        for metric, marker, lw, label in [
            ("mean",   "o", 2.5, "mean"),
            ("median", "s", 1.5, "median"),
            ("p95",    "^", 1.0, "p95"),
        ]:
            ys, errs = [], []
            for name, *_ in exps:
                vals = [agg_data[name][s][metric] for s in SEEDS]
                m, e = mean_err(vals)
                ys.append(m); errs.append(e)

            ax.errorbar(delays, ys, yerr=errs, marker=marker, linewidth=lw,
                        capsize=4, label=label)

        ax.set_xlabel(xlabel, fontsize=11)
        ax.set_ylabel("E2E latency (ms)", fontsize=11)
        ax.set_title(title, fontsize=11)
        ax.legend(fontsize=9)
        ax.set_xticks(delays)
        ax.grid(True, alpha=0.3)
        ax.set_ylim(bottom=0)

    plt.tight_layout()
    out = OUT_DIR / "1_latency_vs_delay.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")

# ─────────────────────────────────────────────────────────────────────────
# 2.  CDF of E2E latency — smooth KDE version + annotated percentiles
# ─────────────────────────────────────────────────────────────────────────
def plot_cdf():
    from scipy.stats import gaussian_kde

    conditions = [
        ("baseline",            "Baseline (0 ms delay)",    "#2196F3", "-",  2.5),
        ("cam_delay_100ms",     "Camera 100 ms delay",      "#FF9800", "--", 2.0),
        ("cam_delay_500ms",     "Camera 500 ms delay",      "#F44336", "-.", 2.0),
        ("cam_delay_1000ms",    "Camera 1000 ms delay",     "#9C27B0", ":",  2.5),
        ("thermal_delay_500ms", "Thermal 500 ms delay",     "#009688", "--", 1.5),
        ("thermal_delay_1000ms","Thermal 1000 ms delay",    "#795548", ":",  1.5),
    ]

    # Collect pooled values for each condition
    pooled = {}
    for exp, label, color, ls, lw in conditions:
        vals = []
        for s in SEEDS:
            df = load(s, exp)
            if not df.empty:
                vals.extend(df["e2e_ms"].tolist())
        pooled[exp] = np.array(vals)

    fig, axes = plt.subplots(1, 2, figsize=(15, 6))
    fig.suptitle(
        "Cumulative Distribution of End-to-End Fusion Latency\n"
        f"(all {len(SEEDS)} seeds pooled — smooth KDE curve)",
        fontsize=13, fontweight="bold",
    )

    panel_cfg = [
        (axes[0], 3000,  "Zoom: 0 – 3 seconds"),
        (axes[1], 6000,  "Full range: 0 – 6 seconds"),
    ]

    for ax, xlim, title in panel_cfg:
        # x grid for smooth curve
        xs = np.linspace(0, xlim, 800)

        for exp, label, color, ls, lw in conditions:
            v = pooled[exp]
            if len(v) < 5:
                continue
            n = len(v)

            # ── smooth CDF via KDE integration ──────────────────────────
            kde = gaussian_kde(v, bw_method=0.15)
            pdf = kde(xs)
            cdf = np.cumsum(pdf) * (xs[1] - xs[0])
            cdf = cdf / cdf[-1] * 100          # normalise to 0–100 %

            ax.plot(xs, cdf, linewidth=lw, color=color,
                    linestyle=ls, label=f"{label}  (n={n})")

            # annotate p50 and p95
            for pct, sym in [(50, "●"), (95, "▲")]:
                idx = np.searchsorted(cdf, pct)
                if idx < len(xs):
                    ax.annotate(
                        f"{xs[idx]:.0f}",
                        xy=(xs[idx], pct),
                        xytext=(xs[idx] + xlim * 0.02, pct - 3),
                        fontsize=6.5, color=color, alpha=0.85,
                    )

        # reference lines
        for pct, lbl in [(50, "p50 (median)"), (95, "p95")]:
            ax.axhline(pct, color="gray", linewidth=0.8, linestyle=":",
                       label=lbl if ax is axes[0] else None)

        ax.set_xlabel("End-to-end latency  (ms)", fontsize=11)
        ax.set_ylabel("% of fusions completed within this latency", fontsize=11)
        ax.set_title(title, fontsize=11)
        ax.set_xlim(0, xlim)
        ax.set_ylim(0, 103)
        ax.legend(fontsize=8.5, loc="lower right")
        ax.grid(True, alpha=0.25)

        # shade the "sub-500ms fast zone"
        ax.axvspan(0, 500, alpha=0.05, color="green", label="_fast zone")
        ax.text(250, 5, "< 500ms\n(fast zone)", ha="center",
                fontsize=7, color="green", alpha=0.7)

    plt.tight_layout()
    out = OUT_DIR / "4_cdf_e2e_latency.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")

# ─────────────────────────────────────────────────────────────────────────
# 5.  Packet loss effect — boxplots showing distribution across seeds
# ─────────────────────────────────────────────────────────────────────────
def plot_loss_effect():
    labels = ["10ms\n0% loss", "10ms\n1% loss", "100ms\n0% loss",
              "100ms\n1% loss", "100ms\n5% loss"]
    colors = ["#2196F3", "#FF9800", "#4CAF50", "#F44336", "#9C27B0"]

    # Collect per-seed mean and p95 for each condition
    mean_data, p95_data = [], []
    for name, *_ in LOSS_EXPS:
        seed_means, seed_p95s = [], []
        for s in SEEDS:
            df = load(s, name)
            if not df.empty and "e2e_ms" in df.columns:
                v = df["e2e_ms"].dropna().values
                seed_means.append(float(np.mean(v)))
                seed_p95s.append(float(np.percentile(v, 95)))
        mean_data.append(seed_means)
        p95_data.append(seed_p95s)

    fig, axes = plt.subplots(1, 2, figsize=(13, 6))
    fig.suptitle(
        f"Effect of Packet Loss on Latency\n"
        f"(distribution across {len(SEEDS)} seeds — box = IQR, whiskers = 5th–95th pct)",
        fontsize=13, fontweight="bold",
    )

    for ax, data, ylabel, title in [
        (axes[0], mean_data, "Mean E2E latency (ms)", "Mean E2E Latency"),
        (axes[1], p95_data,  "p95 E2E latency (ms)",  "p95 E2E Latency"),
    ]:
        bp = ax.boxplot(
            data, patch_artist=True, notch=False,
            whis=[5, 95], showfliers=False,
            medianprops=dict(color="black", linewidth=2),
        )
        for patch, color in zip(bp["boxes"], colors):
            patch.set_facecolor(color)
            patch.set_alpha(0.75)
        for cap in bp["caps"]:
            cap.set_linewidth(1.5)
        for whisker in bp["whiskers"]:
            whisker.set_linestyle("--")
            whisker.set_alpha(0.7)

        # Overlay individual seed dots (jittered)
        rng = np.random.default_rng(0)
        for i, vals in enumerate(data, start=1):
            jitter = rng.uniform(-0.18, 0.18, len(vals))
            ax.scatter(
                np.full(len(vals), i) + jitter, vals,
                s=22, alpha=0.45, color=colors[i - 1], zorder=3,
            )

        ax.set_xticks(range(1, len(labels) + 1))
        ax.set_xticklabels(labels, fontsize=10)
        ax.set_ylabel(ylabel, fontsize=11)
        ax.set_title(title, fontsize=11)
        ax.grid(True, alpha=0.3, axis="y")
        ax.set_ylim(bottom=0)

    plt.tight_layout()
    out = OUT_DIR / "5_loss_effect.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")

# ─────────────────────────────────────────────────────────────────────────
# 6.  Latency breakdown by component — baseline vs cam_1000ms vs th_1000ms
# ─────────────────────────────────────────────────────────────────────────
def plot_latency_breakdown():
    conditions = [
        ("baseline",            "Baseline"),
        ("cam_delay_100ms",     "Cam 100ms"),
        ("cam_delay_500ms",     "Cam 500ms"),
        ("cam_delay_1000ms",    "Cam 1000ms"),
        ("thermal_delay_100ms", "Thermal 100ms"),
        ("thermal_delay_500ms", "Thermal 500ms"),
        ("thermal_delay_1000ms","Thermal 1000ms"),
    ]
    components = [
        ("thermal_net_ns",  "Thermal net",  "#2196F3"),
        ("imagery_net_ns",  "Imagery net",  "#FF9800"),
        ("thermal_proc_ns", "Thermal proc", "#4CAF50"),
        ("imagery_proc_ns", "Imagery proc", "#8BC34A"),
        ("fusion_proc_ns",  "Fusion proc",  "#9E9E9E"),
    ]

    fig, ax = plt.subplots(figsize=(13, 6))
    fig.suptitle(
        f"Mean Latency Breakdown by Component\n(pooled across {len(SEEDS)} seeds)",
        fontsize=13, fontweight="bold",
    )

    x = np.arange(len(conditions))
    bar_w = 0.6
    bottoms = np.zeros(len(conditions))

    for col, label, color in components:
        means = []
        for exp, _ in conditions:
            vals = []
            for s in SEEDS:
                df = load(s, exp)
                if not df.empty and col in df.columns:
                    vals.append(df[col].mean() / 1e6)   # ns → ms
            means.append(np.mean(vals) if vals else 0)
        ax.bar(x, means, bar_w, bottom=bottoms, label=label, color=color, alpha=0.9)
        bottoms += np.array(means)

    ax.set_xticks(x)
    ax.set_xticklabels([lbl for _, lbl in conditions], fontsize=10)
    ax.set_ylabel("Mean latency (ms)", fontsize=11)
    ax.set_title("Latency Breakdown", fontsize=11)
    ax.legend(fontsize=9, loc="upper left")
    ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    out = OUT_DIR / "6_latency_breakdown.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")

# ─────────────────────────────────────────────────────────────────────────
# 7.  Scatter: E2E latency vs drone distance (pooled all seeds, baseline)
# ─────────────────────────────────────────────────────────────────────────
def plot_distance_vs_latency():
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    fig.suptitle(
        "E2E Latency vs Inter-Drone Separation\n(baseline, all seeds pooled)",
        fontsize=13, fontweight="bold",
    )

    for ax, col, xlabel in [
        (axes[0], "separation_xy_m", "Horizontal separation √(dx²+dy²) (m)"),
        (axes[1], "separation_dz_m", "|ΔZ| between drones (m)"),
    ]:
        all_dist, all_lat = [], []
        for s in SEEDS:
            df = load(s, "baseline")
            if df.empty or col not in df.columns:
                continue
            sub = df.dropna(subset=[col, "e2e_ms"])
            if sub.empty:
                continue
            all_dist.extend(sub[col].astype(float).tolist())
            all_lat.extend(sub["e2e_ms"].astype(float).tolist())

        if all_dist:
            ax.scatter(all_dist, all_lat, alpha=0.35, s=18, color="#2196F3")
            slope, intercept, r, p, _ = stats.linregress(all_dist, all_lat)
            xs = np.linspace(min(all_dist), max(all_dist), 100)
            ax.plot(xs, slope * xs + intercept, color="red", linewidth=2,
                    label=f"r={r:.2f}, p={p:.3f}")
            ax.legend(fontsize=10)
        else:
            ax.text(0.5, 0.5, "No inter-drone columns in logs\n(re-run with current controller)",
                    ha="center", va="center", transform=ax.transAxes, fontsize=10)

        ax.set_xlabel(xlabel, fontsize=11)
        ax.set_ylabel("E2E latency (ms)", fontsize=11)
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    out = OUT_DIR / "7_distance_vs_latency.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")

# ─────────────────────────────────────────────────────────────────────────
# 8.  Horizontal bar chart — mean ± SD per experiment, sorted, color-coded
# ─────────────────────────────────────────────────────────────────────────
def plot_experiment_comparison():
    ALL_EXPS = [
        ("baseline",             "Baseline",              "baseline"),
        ("cam_delay_10ms",       "Cam delay 10 ms",       "cam"),
        ("cam_delay_50ms",       "Cam delay 50 ms",       "cam"),
        ("cam_delay_100ms",      "Cam delay 100 ms",      "cam"),
        ("cam_delay_500ms",      "Cam delay 500 ms",      "cam"),
        ("cam_delay_1000ms",     "Cam delay 1000 ms",     "cam"),
        ("thermal_delay_10ms",   "Thermal delay 10 ms",   "thermal"),
        ("thermal_delay_50ms",   "Thermal delay 50 ms",   "thermal"),
        ("thermal_delay_100ms",  "Thermal delay 100 ms",  "thermal"),
        ("thermal_delay_500ms",  "Thermal delay 500 ms",  "thermal"),
        ("thermal_delay_1000ms", "Thermal delay 1000 ms", "thermal"),
        ("cam_10ms_loss1pct",    "Cam 10ms + 1% loss",    "loss"),
        ("cam_100ms_loss1pct",   "Cam 100ms + 1% loss",   "loss"),
        ("cam_100ms_loss5pct",   "Cam 100ms + 5% loss",   "loss"),
        ("clock_offset_100ms",   "Clock offset 100 ms",   "clock"),
        ("clock_offset_500ms",   "Clock offset 500 ms",   "clock"),
        ("clock_offset_1000ms",  "Clock offset 1000 ms",  "clock"),
        ("clock_jitter_50ms",    "Clock jitter 50 ms",    "clock"),
        ("clock_jitter_200ms",   "Clock jitter 200 ms",   "clock"),
        ("dist_drop_enabled",    "Dist-based drop",       "loss"),
    ]
    TYPE_COLOR = {
        "baseline": "#4CAF50",
        "cam":      "#2196F3",
        "thermal":  "#FF9800",
        "loss":     "#E91E63",
        "clock":    "#9C27B0",
    }

    rows = []
    for exp, label, etype in ALL_EXPS:
        vals = [e2e_stats(load(s, exp))["mean"] for s in SEEDS]
        vals = [v for v in vals if not np.isnan(v)]
        if not vals:
            continue
        m, sd = float(np.mean(vals)), float(np.std(vals, ddof=0))
        rows.append((label, etype, m, sd))

    # Sort by mean latency ascending
    rows.sort(key=lambda r: r[2])
    labels_s = [r[0] for r in rows]
    colors_s = [TYPE_COLOR[r[1]] for r in rows]
    means_s  = [r[2] for r in rows]
    sds_s    = [r[3] for r in rows]

    fig, ax = plt.subplots(figsize=(11, 7))
    fig.suptitle(
        f"Mean E2E Latency per Experiment  (mean ± 1 SD, {len(SEEDS)} seeds)",
        fontsize=13, fontweight="bold",
    )

    y = np.arange(len(rows))
    bars = ax.barh(y, means_s, xerr=sds_s, color=colors_s, alpha=0.85,
                   error_kw=dict(elinewidth=1.5, capsize=4, ecolor="#333333"),
                   height=0.65)

    # Annotate bars with mean value
    for i, (m, sd) in enumerate(zip(means_s, sds_s)):
        ax.text(m + sd + 30, i, f"{m:.0f}", va="center", fontsize=8.5, color="#222222")

    ax.set_yticks(y)
    ax.set_yticklabels(labels_s, fontsize=10)
    ax.set_xlabel("Mean E2E Latency (ms)", fontsize=11)
    ax.grid(True, alpha=0.25, axis="x")
    ax.set_xlim(left=0)

    # Legend for experiment type
    legend_patches = [
        mpatches.Patch(color=TYPE_COLOR["baseline"], label="Baseline"),
        mpatches.Patch(color=TYPE_COLOR["cam"],      label="Camera delay"),
        mpatches.Patch(color=TYPE_COLOR["thermal"],  label="Thermal delay"),
        mpatches.Patch(color=TYPE_COLOR["loss"],     label="Delay + packet loss"),
    ]
    ax.legend(handles=legend_patches, loc="lower right", fontsize=10)

    plt.tight_layout()
    out = OUT_DIR / "8_experiment_comparison.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")

# ─────────────────────────────────────────────────────────────────────────
# 9.  Summary table (mean ± SD across seeds for all key metrics)
# ─────────────────────────────────────────────────────────────────────────
def plot_summary_table():
    ALL_EXPS = [
        ("baseline",            "Baseline",               "—",    "—",   "—"),
        ("cam_delay_10ms",      "Cam 10 ms",              "10",   "—",   "—"),
        ("cam_delay_50ms",      "Cam 50 ms",              "50",   "—",   "—"),
        ("cam_delay_100ms",     "Cam 100 ms",             "100",  "—",   "—"),
        ("cam_delay_500ms",     "Cam 500 ms",             "500",  "—",   "—"),
        ("cam_delay_1000ms",    "Cam 1000 ms",            "1000", "—",   "—"),
        ("thermal_delay_10ms",  "Thermal 10 ms",          "—",    "10",  "—"),
        ("thermal_delay_50ms",  "Thermal 50 ms",          "—",    "50",  "—"),
        ("thermal_delay_100ms", "Thermal 100 ms",         "—",    "100", "—"),
        ("thermal_delay_500ms", "Thermal 500 ms",         "—",    "500", "—"),
        ("thermal_delay_1000ms","Thermal 1000 ms",        "—",    "1000","—"),
        ("cam_10ms_loss1pct",   "Cam 10ms+1% loss",       "10",   "—",   "1%"),
        ("cam_100ms_loss1pct",  "Cam 100ms+1% loss",      "100",  "—",   "1%"),
        ("cam_100ms_loss5pct",  "Cam 100ms+5% loss",      "100",  "—",   "5%"),
        ("clock_offset_100ms",  "Clock offset 100ms",     "—",    "—",   "off+100ms"),
        ("clock_offset_500ms",  "Clock offset 500ms",     "—",    "—",   "off+500ms"),
        ("clock_offset_1000ms", "Clock offset 1000ms",    "—",    "—",   "off+1s"),
        ("clock_jitter_50ms",   "Clock jitter 50ms",      "—",    "—",   "jit±50ms"),
        ("clock_jitter_200ms",  "Clock jitter 200ms",     "—",    "—",   "jit±200ms"),
        ("dist_drop_enabled",   "Dist-based drop",        "—",    "—",   "dist"),
    ]

    rows = []
    for exp, label, cam_d, th_d, extra in ALL_EXPS:
        e2e_vals = [e2e_stats(load(s, exp))["mean"] for s in SEEDS]
        p95_vals = [e2e_stats(load(s, exp))["p95"] for s in SEEDS]
        e_m, e_s = mean_err(e2e_vals)
        p_m, p_s = mean_err(p95_vals)

        # Detection F1
        seed_f1 = []
        for s in SEEDS:
            df = load(s, exp)
            if df.empty or "hit_miss" not in df.columns:
                continue
            hm = collections.Counter(df["hit_miss"].tolist())
            tp = hm.get("TP", 0); fp = hm.get("FP", 0); fn = hm.get("FN", 0)
            denom = 2 * tp + fp + fn
            seed_f1.append(2 * tp / denom if denom > 0 else np.nan)
        ff = [x for x in seed_f1 if not np.isnan(x)]
        f1_str = f"{np.mean(ff):.2f} ± {np.std(ff):.2f}" if ff else "—"

        rows.append([label, cam_d, th_d, extra,
                     f"{e_m:.0f} ± {e_s:.0f}",
                     f"{p_m:.0f} ± {p_s:.0f}",
                     f1_str])

    col_headers = ["Experiment", "Cam\ndelay", "Thermal\ndelay", "Other",
                   "E2E mean (ms)\n± SD", "E2E p95 (ms)\n± SD", "F1\n± SD"]
    col_widths = [0.22, 0.07, 0.08, 0.10, 0.19, 0.19, 0.13]

    fig, ax = plt.subplots(figsize=(17, 10))
    ax.axis("off")
    fig.suptitle(
        f"Summary: All Experiments — Mean ± SD Across {len(SEEDS)} Seeds (Apr-15 Monte Carlo)",
        fontsize=13, fontweight="bold", y=0.98,
    )

    tbl = ax.table(cellText=rows, colLabels=col_headers,
                   colWidths=col_widths,
                   cellLoc="center", loc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8.5)
    tbl.scale(1, 1.4)

    # colour header row
    for j in range(len(col_headers)):
        tbl[0, j].set_facecolor("#37474F")
        tbl[0, j].set_text_props(color="white", fontweight="bold")

    for i in range(1, len(rows) + 1):
        color = "#F5F5F5" if i % 2 == 0 else "white"
        for j in range(len(col_headers)):
            tbl[i, j].set_facecolor(color)

    plt.tight_layout()
    out = OUT_DIR / "9_summary_table.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")

# ─────────────────────────────────────────────────────────────────────────
# 10.  E2E latency timeseries — mean ± SD envelope + a few faint traces
# ─────────────────────────────────────────────────────────────────────────
def plot_timeseries_baseline():
    # Collect (t_seconds, e2e_ms) per seed, align to t=0
    seed_series = []
    for s in SEEDS:
        df = load(s, "baseline")
        if df.empty or "fusion_done_ns" not in df.columns:
            continue
        t0 = float(df["fusion_done_ns"].iloc[0])
        t  = (df["fusion_done_ns"].astype(float) - t0) / 1e9
        seed_series.append((t.values, df["e2e_ms"].values, s))

    if not seed_series:
        return

    # Build a common time grid (0 → 95th percentile of max times, 100 bins)
    max_ts = np.percentile([s[0][-1] for s in seed_series], 95)
    t_grid = np.linspace(0, max_ts, 80)

    # Interpolate each seed onto the grid
    grid_vals = []
    for t, v, _ in seed_series:
        interp = np.interp(t_grid, t, v, left=np.nan, right=np.nan)
        grid_vals.append(interp)
    grid_vals = np.array(grid_vals)  # (n_seeds, n_bins)

    mean_v = np.nanmean(grid_vals, axis=0)
    sd_v   = np.nanstd(grid_vals,  axis=0)
    p25_v  = np.nanpercentile(grid_vals, 25, axis=0)
    p75_v  = np.nanpercentile(grid_vals, 75, axis=0)

    fig, ax = plt.subplots(figsize=(12, 5))
    fig.suptitle(
        f"E2E Latency Over Time — Baseline  "
        f"(mean ± 1 SD, {len(seed_series)} seeds)",
        fontsize=13, fontweight="bold",
    )

    # Faint individual lines (5 random seeds for texture)
    rng = np.random.default_rng(7)
    sample_idx = rng.choice(len(seed_series), size=min(6, len(seed_series)), replace=False)
    for idx in sample_idx:
        t, v, s = seed_series[idx]
        mask = t <= max_ts
        ax.plot(t[mask], v[mask], color="#aaaaaa", linewidth=0.7, alpha=0.5, zorder=1)

    # IQR band
    ax.fill_between(t_grid, p25_v, p75_v, alpha=0.25, color="#2196F3", label="IQR (25–75%)", zorder=2)
    # SD band
    ax.fill_between(t_grid, mean_v - sd_v, mean_v + sd_v,
                    alpha=0.18, color="#FF9800", label="Mean ± 1 SD", zorder=3)
    # Mean line
    ax.plot(t_grid, mean_v, color="#1565C0", linewidth=2.5, label="Mean", zorder=4)

    ax.set_xlabel("Time since start (s)", fontsize=11)
    ax.set_ylabel("E2E latency (ms)", fontsize=11)
    ax.set_xlim(0, max_ts)
    ax.set_ylim(bottom=0)
    ax.legend(fontsize=10, loc="upper right")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    out = OUT_DIR / "10_timeseries_baseline.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────
# 11.  Drone XY 2D map — occupancy raster + representative tracks
# ─────────────────────────────────────────────────────────────────────────
def plot_trajectory_overlay():
    th_x, th_y, im_x, im_y = [], [], [], []
    th_tracks, im_tracks = [], []
    for s in SEEDS:
        df = load(s, "baseline")
        if df.empty:
            continue
        if "thermal_x" in df.columns and "thermal_y" in df.columns:
            sub = df.dropna(subset=["thermal_x", "thermal_y"])
            th_x.extend(sub["thermal_x"].tolist())
            th_y.extend(sub["thermal_y"].tolist())
            th_tracks.append((sub["thermal_x"].to_numpy(), sub["thermal_y"].to_numpy()))
        if "imagery_x" in df.columns and "imagery_y" in df.columns:
            sub = df.dropna(subset=["imagery_x", "imagery_y"])
            im_x.extend(sub["imagery_x"].tolist())
            im_y.extend(sub["imagery_y"].tolist())
            im_tracks.append((sub["imagery_x"].to_numpy(), sub["imagery_y"].to_numpy()))

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle(
        f"Drone Survey 2D Map — Baseline ({len(SEEDS)} seeds pooled)\n"
        "Raster = occupancy count per map cell | thin lines = representative paths",
        fontsize=13, fontweight="bold",
    )

    for ax, xs, ys, tracks, title, cmap, line_color in [
        (axes[0], th_x, th_y, th_tracks, "Thermal drone", "Blues", "#0D47A1"),
        (axes[1], im_x, im_y, im_tracks, "Imagery drone", "Oranges", "#E65100"),
    ]:
        if xs:
            # Build a true 2D occupancy raster map
            xbins = np.linspace(-45, 45, 37)
            ybins = np.linspace(-40, 35, 31)
            h, xedges, yedges = np.histogram2d(xs, ys, bins=[xbins, ybins])
            h_plot = np.ma.masked_where(h.T == 0, h.T)
            im = ax.imshow(
                h_plot,
                extent=[xedges[0], xedges[-1], yedges[0], yedges[-1]],
                origin="lower",
                cmap=cmap,
                aspect="equal",
                interpolation="nearest",
                alpha=0.9,
            )
            plt.colorbar(im, ax=ax, label="Occupancy count")

            # Overlay a few representative trajectories
            rng = np.random.default_rng(123)
            if tracks:
                sample_idx = rng.choice(len(tracks), size=min(6, len(tracks)), replace=False)
                for idx in sample_idx:
                    tx, ty = tracks[idx]
                    ax.plot(tx, ty, color=line_color, linewidth=0.8, alpha=0.35, zorder=3)
                    ax.scatter(tx[0], ty[0], s=12, color="black", alpha=0.55, zorder=4)
                    ax.scatter(tx[-1], ty[-1], s=12, color="white", edgecolor="black", alpha=0.8, zorder=4)
        else:
            ax.text(0.5, 0.5, "No position data",
                    ha="center", va="center", transform=ax.transAxes, fontsize=11)
        ax.set_title(title, fontsize=12, fontweight="bold")
        ax.set_xlabel("x (m)", fontsize=11)
        ax.set_ylabel("y (m)", fontsize=11)
        ax.set_aspect("equal")
        ax.grid(True, alpha=0.2)

    plt.tight_layout()
    out = OUT_DIR / "11_trajectories_xy.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────
# 12.  Drone altitude (Z) — mean ± SD band per drone type, trimmed x-axis
# ─────────────────────────────────────────────────────────────────────────
def plot_trajectory_z_time():
    th_series, im_series = [], []
    for s in SEEDS:
        df = load(s, "baseline")
        if df.empty or "fusion_done_ns" not in df.columns:
            continue
        t0 = float(df["fusion_done_ns"].iloc[0])
        t  = (df["fusion_done_ns"].astype(float) - t0) / 1e9
        if "thermal_z" in df.columns and df["thermal_z"].notna().any():
            th_series.append((t.values, df["thermal_z"].values))
        if "imagery_z" in df.columns and df["imagery_z"].notna().any():
            im_series.append((t.values, df["imagery_z"].values))

    def _build_envelope(series):
        if not series:
            return None, None, None, None
        max_t = np.percentile([s[0][-1] for s in series], 95)
        t_grid = np.linspace(0, max_t, 80)
        grid = np.array([np.interp(t_grid, t, z, left=np.nan, right=np.nan)
                         for t, z in series])
        return t_grid, np.nanmean(grid, axis=0), np.nanstd(grid, axis=0), max_t

    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=False)
    fig.suptitle(
        f"Drone Altitude (Z) vs Time — Baseline  "
        f"(mean ± 1 SD, {len(SEEDS)} seeds)",
        fontsize=13, fontweight="bold",
    )

    for ax, series, ylabel, color, label_seed_lines in [
        (axes[0], th_series, "Thermal drone altitude (m)", "#1565C0", True),
        (axes[1], im_series, "Imagery drone altitude (m)", "#E65100", False),
    ]:
        t_grid, mean_z, sd_z, max_t = _build_envelope(series)
        if t_grid is None:
            ax.text(0.5, 0.5, "No Z data", ha="center", va="center",
                    transform=ax.transAxes, fontsize=11)
            continue

        # Faint individual seeds
        rng = np.random.default_rng(3)
        sample = rng.choice(len(series), size=min(5, len(series)), replace=False)
        for idx in sample:
            t, z = series[idx]
            mask = t <= max_t
            ax.plot(t[mask], z[mask], color="#cccccc", linewidth=0.8, alpha=0.6, zorder=1)

        ax.fill_between(t_grid, mean_z - sd_z, mean_z + sd_z,
                        alpha=0.3, color=color, label="Mean ± 1 SD", zorder=2)
        ax.plot(t_grid, mean_z, color=color, linewidth=2.5, label="Mean", zorder=3)

        ax.set_ylabel(ylabel, fontsize=11)
        ax.set_xlabel("Time since start (s)", fontsize=11)
        ax.set_xlim(0, max_t)
        ax.legend(fontsize=10)
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    out = OUT_DIR / "12_trajectories_z.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────
# 13.  Violin plot — full latency distribution for every experiment
# ─────────────────────────────────────────────────────────────────────────
def plot_violin_all_experiments():
    ALL_EXPS = [
        ("baseline",             "Baseline",              "baseline"),
        ("cam_delay_10ms",       "Cam 10ms",              "cam"),
        ("cam_delay_50ms",       "Cam 50ms",              "cam"),
        ("cam_delay_100ms",      "Cam 100ms",             "cam"),
        ("cam_delay_500ms",      "Cam 500ms",             "cam"),
        ("cam_delay_1000ms",     "Cam 1000ms",            "cam"),
        ("thermal_delay_10ms",   "Therm 10ms",            "thermal"),
        ("thermal_delay_50ms",   "Therm 50ms",            "thermal"),
        ("thermal_delay_100ms",  "Therm 100ms",           "thermal"),
        ("thermal_delay_500ms",  "Therm 500ms",           "thermal"),
        ("thermal_delay_1000ms", "Therm 1000ms",          "thermal"),
        ("cam_10ms_loss1pct",    "Cam 10ms\n+1% loss",    "loss"),
        ("cam_100ms_loss1pct",   "Cam 100ms\n+1% loss",   "loss"),
        ("cam_100ms_loss5pct",   "Cam 100ms\n+5% loss",   "loss"),
        ("clock_offset_100ms",   "Offset\n100ms",         "clock"),
        ("clock_offset_500ms",   "Offset\n500ms",         "clock"),
        ("clock_offset_1000ms",  "Offset\n1000ms",        "clock"),
        ("clock_jitter_50ms",    "Jitter\n50ms",          "clock"),
        ("clock_jitter_200ms",   "Jitter\n200ms",         "clock"),
        ("dist_drop_enabled",    "Dist\ndrop",            "loss"),
    ]
    TYPE_COLOR = {
        "baseline": "#4CAF50",
        "cam":      "#2196F3",
        "thermal":  "#FF9800",
        "loss":     "#E91E63",
        "clock":    "#9C27B0",
    }

    # Pool all e2e_ms values across seeds for each experiment
    data, tick_labels, colors = [], [], []
    for exp, label, etype in ALL_EXPS:
        vals = []
        for s in SEEDS:
            df = load(s, exp)
            if not df.empty and "e2e_ms" in df.columns:
                vals.extend(df["e2e_ms"].dropna().tolist())
        if len(vals) < 5:
            continue
        # Clip extreme outliers (above 99.5th pct) so violins are readable
        clip = np.percentile(vals, 99.5)
        data.append([v for v in vals if v <= clip])
        tick_labels.append(label)
        colors.append(TYPE_COLOR[etype])

    fig, ax = plt.subplots(figsize=(16, 6))
    fig.suptitle(
        f"E2E Latency Distribution per Experiment  "
        f"(all {len(SEEDS)} seeds pooled — values clipped at 99.5th pct)",
        fontsize=13, fontweight="bold",
    )

    parts = ax.violinplot(data, positions=range(len(data)),
                          showmedians=True, showextrema=False,
                          widths=0.75)

    for i, (pc, color) in enumerate(zip(parts["bodies"], colors)):
        pc.set_facecolor(color)
        pc.set_alpha(0.7)
        pc.set_edgecolor("white")
        pc.set_linewidth(0.5)
    parts["cmedians"].set_color("black")
    parts["cmedians"].set_linewidth(2)

    # Overlay IQR markers
    for i, vals in enumerate(data):
        q1, med, q3 = np.percentile(vals, [25, 50, 75])
        ax.vlines(i, q1, q3, color="black", linewidth=4, alpha=0.5, zorder=3)

    ax.set_xticks(range(len(tick_labels)))
    ax.set_xticklabels(tick_labels, fontsize=9.5, rotation=15, ha="right")
    ax.set_ylabel("E2E latency (ms)", fontsize=11)
    ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.25, axis="y")

    legend_patches = [
        mpatches.Patch(color=TYPE_COLOR["baseline"], label="Baseline"),
        mpatches.Patch(color=TYPE_COLOR["cam"],      label="Camera delay"),
        mpatches.Patch(color=TYPE_COLOR["thermal"],  label="Thermal delay"),
        mpatches.Patch(color=TYPE_COLOR["loss"],     label="Delay + packet loss"),
    ]
    ax.legend(handles=legend_patches, loc="upper left", fontsize=10)

    plt.tight_layout()
    out = OUT_DIR / "13_violin_all_experiments.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")

# ─────────────────────────────────────────────────────────────────────────
# 14.  Detection Performance — Precision / Recall / F1 per experiment
# ─────────────────────────────────────────────────────────────────────────
def plot_detection_performance():
    import collections

    ALL_EXPS = [
        ("baseline",             "Baseline",               "baseline"),
        ("cam_delay_10ms",       "Cam 10ms",               "cam"),
        ("cam_delay_50ms",       "Cam 50ms",               "cam"),
        ("cam_delay_100ms",      "Cam 100ms",              "cam"),
        ("cam_delay_500ms",      "Cam 500ms",              "cam"),
        ("cam_delay_1000ms",     "Cam 1000ms",             "cam"),
        ("thermal_delay_10ms",   "Therm 10ms",             "thermal"),
        ("thermal_delay_50ms",   "Therm 50ms",             "thermal"),
        ("thermal_delay_100ms",  "Therm 100ms",            "thermal"),
        ("thermal_delay_500ms",  "Therm 500ms",            "thermal"),
        ("thermal_delay_1000ms", "Therm 1000ms",           "thermal"),
        ("cam_10ms_loss1pct",    "Cam 10ms+1%loss",        "loss"),
        ("cam_100ms_loss1pct",   "Cam 100ms+1%loss",       "loss"),
        ("cam_100ms_loss5pct",   "Cam 100ms+5%loss",       "loss"),
        ("clock_offset_100ms",   "Offset 100ms",           "clock"),
        ("clock_offset_500ms",   "Offset 500ms",           "clock"),
        ("clock_offset_1000ms",  "Offset 1000ms",          "clock"),
        ("clock_jitter_50ms",    "Jitter 50ms",            "clock"),
        ("clock_jitter_200ms",   "Jitter 200ms",           "clock"),
        ("dist_drop_enabled",    "Dist drop",              "loss"),
    ]
    TYPE_COLOR = {
        "baseline": "#4CAF50",
        "cam":      "#2196F3",
        "thermal":  "#FF9800",
        "loss":     "#E91E63",
        "clock":    "#9C27B0",
    }

    labels, colors, prec_m, prec_e, rec_m, rec_e, f1_m, f1_e = [], [], [], [], [], [], [], []

    for exp, label, etype in ALL_EXPS:
        seed_prec, seed_rec, seed_f1 = [], [], []
        for s in SEEDS:
            df = load(s, exp)
            if df.empty or "hit_miss" not in df.columns:
                continue
            hm = collections.Counter(df["hit_miss"].tolist())
            tp = hm.get("TP", 0); fp = hm.get("FP", 0); fn = hm.get("FN", 0)
            p = tp / (tp + fp) if (tp + fp) > 0 else np.nan
            r = tp / (tp + fn) if (tp + fn) > 0 else np.nan
            f = 2 * p * r / (p + r) if (not np.isnan(p) and not np.isnan(r) and (p + r) > 0) else np.nan
            seed_prec.append(p); seed_rec.append(r); seed_f1.append(f)

        pp = [x for x in seed_prec if not np.isnan(x)]
        rr = [x for x in seed_rec  if not np.isnan(x)]
        ff = [x for x in seed_f1   if not np.isnan(x)]
        if not pp:
            continue
        labels.append(label)
        colors.append(TYPE_COLOR[etype])
        prec_m.append(np.mean(pp)); prec_e.append(np.std(pp, ddof=0))
        rec_m.append(np.mean(rr));  rec_e.append(np.std(rr,  ddof=0))
        f1_m.append(np.mean(ff));   f1_e.append(np.std(ff,   ddof=0))

    x = np.arange(len(labels))
    w = 0.26
    fig, ax = plt.subplots(figsize=(16, 6))
    fig.suptitle(
        f"Detection Performance per Experiment  (mean ± 1 SD, {len(SEEDS)} seeds)\n"
        "Precision = TP/(TP+FP)  |  Recall = TP/(TP+FN)  |  F1 = harmonic mean",
        fontsize=12, fontweight="bold",
    )

    ax.bar(x - w, prec_m, w, yerr=prec_e, label="Precision", color="#1565C0",
           alpha=0.85, error_kw=dict(elinewidth=1.2, capsize=3, ecolor="#333"))
    ax.bar(x,     rec_m,  w, yerr=rec_e,  label="Recall",    color="#E65100",
           alpha=0.85, error_kw=dict(elinewidth=1.2, capsize=3, ecolor="#333"))
    ax.bar(x + w, f1_m,   w, yerr=f1_e,   label="F1",        color="#2E7D32",
           alpha=0.85, error_kw=dict(elinewidth=1.2, capsize=3, ecolor="#333"))

    # Annotate F1 values above each group
    for i, (fm, fe) in enumerate(zip(f1_m, f1_e)):
        ax.text(x[i] + w, fm + fe + 0.015, f"{fm:.2f}", ha="center",
                fontsize=7.5, color="#2E7D32", fontweight="bold")

    # Colour X-tick labels by experiment type
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=9)
    for tick, color in zip(ax.get_xticklabels(), colors):
        tick.set_color(color)

    ax.set_ylabel("Score (0 – 1)", fontsize=11)
    ax.set_ylim(0, 1.05)
    ax.axhline(0.5, color="gray", linestyle=":", linewidth=0.9, alpha=0.6, label="0.5 reference")
    ax.legend(fontsize=10, loc="lower right")
    ax.grid(True, alpha=0.25, axis="y")

    import matplotlib.patches as mpatches
    legend_patches = [
        mpatches.Patch(color=TYPE_COLOR["baseline"], label="Baseline"),
        mpatches.Patch(color=TYPE_COLOR["cam"],      label="Camera delay"),
        mpatches.Patch(color=TYPE_COLOR["thermal"],  label="Thermal delay"),
        mpatches.Patch(color=TYPE_COLOR["loss"],     label="Delay + packet loss"),
    ]
    ax.legend(handles=legend_patches + [
        mpatches.Patch(color="#1565C0", label="Precision"),
        mpatches.Patch(color="#E65100", label="Recall"),
        mpatches.Patch(color="#2E7D32", label="F1"),
        plt.Line2D([0], [0], color="gray", linestyle=":", linewidth=1, label="0.5 ref"),
    ], fontsize=8.5, loc="lower right", ncol=2)

    plt.tight_layout()
    out = OUT_DIR / "14_detection_performance.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────
# 15.  Latency decomposition: network+proc vs queuing wait (baseline)
# ─────────────────────────────────────────────────────────────────────────
def plot_queuing_wait():
    """
    The controller buffers a frame from one drone until a matching frame
    from the other drone arrives.  Reported e2e_ms includes this waiting time,
    but the component columns (thermal_net, imagery_net, *_proc) only sum to
    actual processing time (typically < 5 ms).  The gap is the queuing wait.
    """
    qwait, netproc = [], []
    for s in SEEDS:
        df = load(s, "baseline")
        if df.empty:
            continue
        comp_cols = ["thermal_net_ns", "imagery_net_ns",
                     "thermal_proc_ns", "imagery_proc_ns", "fusion_proc_ns"]
        missing = [c for c in comp_cols if c not in df.columns]
        if missing:
            continue
        total_comp_ms = df[comp_cols].sum(axis=1) / 1e6
        queue_ms = df["e2e_ms"] - total_comp_ms
        qwait.extend(queue_ms.clip(lower=0).tolist())
        netproc.extend(total_comp_ms.tolist())

    if not qwait:
        return

    qwait  = np.array(qwait)
    netproc = np.array(netproc)

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle(
        "Latency Decomposition — Baseline (all 50 seeds pooled)\n"
        "Queuing wait = time a frame sits in buffer before its pair arrives",
        fontsize=13, fontweight="bold",
    )

    # Left: histogram of queuing wait
    ax = axes[0]
    clip = np.percentile(qwait, 99)
    ax.hist(qwait[qwait <= clip], bins=60, color="#E65100", alpha=0.8, edgecolor="white")
    ax.axvline(np.median(qwait), color="black", linewidth=1.8, linestyle="--",
               label=f"median = {np.median(qwait):.0f} ms")
    ax.axvline(np.mean(qwait),   color="red",   linewidth=1.8, linestyle="-",
               label=f"mean   = {np.mean(qwait):.0f} ms")
    ax.set_xlabel("Queuing wait (ms)", fontsize=11)
    ax.set_ylabel("Count", fontsize=11)
    ax.set_title("Queuing Wait Distribution (clipped at p99)", fontsize=11)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)

    # Right: stacked bar showing fraction fast vs slow
    ax2 = axes[1]
    thresh_ms = [0, 10, 100, 500, 1000, 2000]
    fast_pct = [float((qwait < t).mean() * 100) for t in thresh_ms[1:]]
    slow_pct = [100 - p for p in fast_pct]
    bar_labels = [f"< {t} ms" for t in thresh_ms[1:]]
    y = np.arange(len(bar_labels))
    ax2.barh(y, fast_pct, color="#4CAF50", alpha=0.85, label="Fast (queuing < threshold)")
    ax2.barh(y, slow_pct, left=fast_pct, color="#F44336", alpha=0.85, label="Slow (queuing ≥ threshold)")
    ax2.set_yticks(y)
    ax2.set_yticklabels(bar_labels, fontsize=10)
    ax2.set_xlabel("% of fusions", fontsize=11)
    ax2.set_title("Fast vs Slow Fusions by Queuing Threshold", fontsize=11)
    ax2.set_xlim(0, 100)
    ax2.legend(fontsize=10)
    ax2.grid(True, alpha=0.3, axis="x")
    for i, (fp, sp) in enumerate(zip(fast_pct, slow_pct)):
        ax2.text(fp / 2, i, f"{fp:.0f}%", ha="center", va="center",
                 fontsize=9, color="white", fontweight="bold")

    plt.tight_layout()
    out = OUT_DIR / "15_queuing_wait.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ──────────────────────────────────────────────────────────────────────────
def plot_clock_sync_effect():
    """
    Plot 16 — how clock offset and jitter affect E2E latency and F1.
    Two panels: (left) median E2E vs clock error magnitude; (right) F1 vs clock error.
    """
    configs = [
        ("baseline",           "Baseline",      "none",   0),
        ("clock_offset_100ms", "Offset 100ms",  "offset", 100),
        ("clock_offset_500ms", "Offset 500ms",  "offset", 500),
        ("clock_offset_1000ms","Offset 1000ms", "offset", 1000),
        ("clock_jitter_50ms",  "Jitter 50ms",   "jitter", 50),
        ("clock_jitter_200ms", "Jitter 200ms",  "jitter", 200),
    ]
    COLOR = {"none": "#4CAF50", "offset": "#9C27B0", "jitter": "#FF5722"}

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    fig.suptitle(
        f"Clock Synchronisation Error Effects  (mean ± 1 SD, {len(SEEDS)} seeds)\n"
        "Offset = constant clock skew between drones  |  Jitter = random per-frame timing noise",
        fontsize=12, fontweight="bold",
    )

    # Per-config stats
    labels, types, magnitudes = [], [], []
    med_means, med_sds = [], []
    f1_means, f1_sds = [], []
    n_fusions = []

    for exp, label, ctype, mag in configs:
        seed_meds, seed_f1s, seed_n = [], [], []
        for s in SEEDS:
            df = load(s, exp)
            if df.empty:
                continue
            seed_meds.append(float(df["e2e_ms"].median()))
            hm = collections.Counter(df.get("hit_miss", pd.Series()).tolist())
            tp = hm.get("TP", 0); fp = hm.get("FP", 0); fn = hm.get("FN", 0)
            denom = 2 * tp + fp + fn
            seed_f1s.append(2 * tp / denom if denom > 0 else np.nan)
            seed_n.append(len(df))
        labels.append(label); types.append(ctype); magnitudes.append(mag)
        med_means.append(np.mean(seed_meds) if seed_meds else np.nan)
        med_sds.append(np.std(seed_meds) if len(seed_meds) > 1 else 0)
        ff = [x for x in seed_f1s if not np.isnan(x)]
        f1_means.append(np.mean(ff) if ff else np.nan)
        f1_sds.append(np.std(ff) if len(ff) > 1 else 0)
        n_fusions.append(np.mean(seed_n) if seed_n else 0)

    x = np.arange(len(labels))
    bar_colors = [COLOR[t] for t in types]

    # Left: median E2E
    ax = axes[0]
    bars = ax.bar(x, med_means, yerr=med_sds, color=bar_colors, alpha=0.85,
                  error_kw=dict(elinewidth=1.5, capsize=4, ecolor="#333"))
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=9)
    ax.set_ylabel("Median E2E latency (ms)", fontsize=11)
    ax.set_title("Median E2E Latency", fontsize=11)
    ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.3, axis="y")

    # Middle: F1
    ax2 = axes[1]
    ax2.bar(x, f1_means, yerr=f1_sds, color=bar_colors, alpha=0.85,
            error_kw=dict(elinewidth=1.5, capsize=4, ecolor="#333"))
    for i, (fm, fd) in enumerate(zip(f1_means, f1_sds)):
        if not np.isnan(fm):
            ax2.text(i, fm + fd + 0.01, f"{fm:.2f}", ha="center",
                     fontsize=9, fontweight="bold", color=bar_colors[i])
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, rotation=30, ha="right", fontsize=9)
    ax2.set_ylabel("F1 score", fontsize=11)
    ax2.set_title("Fire Detection F1 Score", fontsize=11)
    ax2.set_ylim(0, 1.0)
    ax2.axhline(f1_means[0], color="#4CAF50", linestyle="--", linewidth=1.2,
                alpha=0.7, label=f"Baseline F1={f1_means[0]:.2f}")
    ax2.legend(fontsize=9)
    ax2.grid(True, alpha=0.3, axis="y")

    # Right: mean fusion count
    ax3 = axes[2]
    ax3.bar(x, n_fusions, color=bar_colors, alpha=0.85)
    ax3.set_xticks(x)
    ax3.set_xticklabels(labels, rotation=30, ha="right", fontsize=9)
    ax3.set_ylabel("Mean fusions per seed", fontsize=11)
    ax3.set_title("Fusion Throughput", fontsize=11)
    ax3.set_ylim(bottom=0)
    ax3.grid(True, alpha=0.3, axis="y")
    for i, n in enumerate(n_fusions):
        ax3.text(i, n + 0.3, f"{n:.0f}", ha="center", fontsize=9, color=bar_colors[i])

    legend_patches = [
        mpatches.Patch(color=COLOR["none"],   label="Baseline (no error)"),
        mpatches.Patch(color=COLOR["offset"], label="Clock offset"),
        mpatches.Patch(color=COLOR["jitter"], label="Clock jitter"),
    ]
    fig.legend(handles=legend_patches, loc="lower center", ncol=3, fontsize=10,
               bbox_to_anchor=(0.5, -0.05))

    plt.tight_layout()
    out = OUT_DIR / "16_clock_sync_effect.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


def plot_fire_growth():
    """
    Plot 17 — fire_cell_count and fire_visible_count over time for baseline,
    showing how fire grows and how much of it is actually visible to sensors.
    """
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle(
        f"Fire Growth Over Time — Baseline ({len(SEEDS)} seeds)\n"
        "fire_cell_count = total burning cells  |  fire_visible_count = cells visible to sensors",
        fontsize=12, fontweight="bold",
    )

    for ax, col, ylabel, color, title in [
        (axes[0], "fire_cell_count",    "Fire cells (total)",    "#F44336", "Total Fire Area"),
        (axes[1], "fire_visible_count", "Visible fire cells",    "#FF9800", "Visible Fire Area"),
    ]:
        series_list = []
        for s in SEEDS:
            df = load(s, "baseline")
            if df.empty or col not in df.columns or "fusion_done_ns" not in df.columns:
                continue
            t0 = float(df["fusion_done_ns"].iloc[0])
            t = (df["fusion_done_ns"].astype(float) - t0) / 1e9
            series_list.append((t.values, df[col].values))

        if not series_list:
            ax.text(0.5, 0.5, f"No {col} data", ha="center", va="center",
                    transform=ax.transAxes, fontsize=11)
            continue

        max_t = np.percentile([s[0][-1] for s in series_list], 95)
        t_grid = np.linspace(0, max_t, 80)
        grid = np.array([np.interp(t_grid, t, v, left=np.nan, right=np.nan)
                         for t, v in series_list])
        mean_v = np.nanmean(grid, axis=0)
        sd_v   = np.nanstd(grid, axis=0)

        # Faint individual seeds
        rng = np.random.default_rng(42)
        sample = rng.choice(len(series_list), size=min(6, len(series_list)), replace=False)
        for idx in sample:
            t, v = series_list[idx]
            mask = t <= max_t
            ax.plot(t[mask], v[mask], color="#cccccc", linewidth=0.8, alpha=0.5, zorder=1)

        ax.fill_between(t_grid, mean_v - sd_v, mean_v + sd_v,
                        alpha=0.3, color=color, label="Mean ± 1 SD", zorder=2)
        ax.plot(t_grid, mean_v, color=color, linewidth=2.5, label="Mean", zorder=3)
        ax.set_xlabel("Time since start (s)", fontsize=11)
        ax.set_ylabel(ylabel, fontsize=11)
        ax.set_title(title, fontsize=11)
        ax.set_xlim(0, max_t)
        ax.set_ylim(bottom=0)
        ax.legend(fontsize=10)
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    out = OUT_DIR / "17_fire_growth.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────
# 18.  Confusion balance by experiment (TP/FN share)
# ─────────────────────────────────────────────────────────────────────────
def plot_confusion_balance():
    exps = [
        "baseline", "cam_delay_10ms", "cam_delay_100ms", "cam_delay_500ms",
        "thermal_delay_10ms", "thermal_delay_100ms", "thermal_delay_500ms",
        "cam_10ms_loss1pct", "cam_100ms_loss5pct",
        "clock_offset_100ms", "clock_offset_500ms", "clock_jitter_200ms",
        "dist_drop_enabled",
    ]
    labels, tp_share, fn_share = [], [], []

    for exp in exps:
        tp = fn = 0
        for s in SEEDS:
            df = load(s, exp)
            if df.empty or "hit_miss" not in df.columns:
                continue
            hm = collections.Counter(df["hit_miss"].tolist())
            tp += hm.get("TP", 0)
            fn += hm.get("FN", 0)
        total = tp + fn
        if total == 0:
            continue
        labels.append(exp.replace("_", "\n"))
        tp_share.append(100.0 * tp / total)
        fn_share.append(100.0 * fn / total)

    fig, ax = plt.subplots(figsize=(14, 5.5))
    x = np.arange(len(labels))
    ax.bar(x, tp_share, color="#4CAF50", alpha=0.9, label="TP share")
    ax.bar(x, fn_share, bottom=tp_share, color="#F44336", alpha=0.9, label="FN share")
    for i, v in enumerate(tp_share):
        ax.text(i, v / 2, f"{v:.0f}%", ha="center", va="center", fontsize=8, color="white", fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8.5)
    ax.set_ylim(0, 100)
    ax.set_ylabel("Share of positive events (%)")
    ax.set_title("Detection Quality by Experiment (TP vs FN)")
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend(fontsize=10)
    plt.tight_layout()
    out = OUT_DIR / "18_confusion_balance.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────
# 19.  Queuing wait vs fire visibility (baseline)
# ─────────────────────────────────────────────────────────────────────────
def plot_queue_vs_visibility():
    qwait, visible = [], []
    for s in SEEDS:
        df = load(s, "baseline")
        if df.empty:
            continue
        req = ["e2e_ms", "thermal_net_ns", "imagery_net_ns", "thermal_proc_ns", "imagery_proc_ns", "fusion_proc_ns"]
        if not all(c in df.columns for c in req):
            continue
        comp_ms = df[["thermal_net_ns", "imagery_net_ns", "thermal_proc_ns", "imagery_proc_ns", "fusion_proc_ns"]].sum(axis=1) / 1e6
        q = (df["e2e_ms"] - comp_ms).clip(lower=0)
        qwait.extend(q.tolist())
        if "fire_visible_count" in df.columns:
            visible.extend(df["fire_visible_count"].fillna(0).astype(float).tolist())
        else:
            visible.extend([0.0] * len(q))

    if not qwait:
        return

    qwait = np.array(qwait)
    visible = np.array(visible)
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.scatter(visible, qwait, s=16, alpha=0.3, color="#1E88E5")
    if len(np.unique(visible)) > 1:
        slope, intercept, r, p, _ = stats.linregress(visible, qwait)
        xs = np.linspace(float(np.min(visible)), float(np.max(visible)), 120)
        ax.plot(xs, slope * xs + intercept, color="#D81B60", linewidth=2, label=f"fit r={r:.2f}, p={p:.3f}")
        ax.legend(fontsize=10)
    ax.set_xlabel("Visible fire cells")
    ax.set_ylabel("Queuing wait (ms)")
    ax.set_title("Queuing Wait vs Fire Visibility (Baseline, pooled)")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    out = OUT_DIR / "19_queue_vs_visibility.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────
# 20.  Experiment stability map (mean vs SD of E2E)
# ─────────────────────────────────────────────────────────────────────────
def plot_stability_map():
    experiments = sorted({d.name for d in SEED_DIRS[SEEDS[0]].iterdir() if d.is_dir() and d.name != "sweep_plots"})
    rows = []
    for exp in experiments:
        seed_means = []
        for s in SEEDS:
            df = load(s, exp)
            if df.empty or "e2e_ms" not in df.columns:
                continue
            seed_means.append(float(df["e2e_ms"].mean()))
        if seed_means:
            rows.append((exp, float(np.mean(seed_means)), float(np.std(seed_means, ddof=0))))
    if not rows:
        return

    fig, ax = plt.subplots(figsize=(9, 7))
    for exp, mu, sd in rows:
        ax.scatter(mu, sd, s=65, alpha=0.8, color="#6A1B9A")
        ax.annotate(exp.replace("_", " "), (mu, sd), textcoords="offset points", xytext=(6, 3), fontsize=8)
    ax.set_xlabel("Mean E2E latency across seeds (ms)")
    ax.set_ylabel("SD across seeds (ms)")
    ax.set_title("Experiment Stability Map (lower-left is better)")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    out = OUT_DIR / "20_stability_map.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ──────────────────────────────────────────────────────────────────────────
def main() -> None:
    global OUT_DIR, SEED_DIRS, SEEDS, PALETTE, SEED_LABELS
    ap = argparse.ArgumentParser(description="Cross-seed latency comparison figures.")
    ap.add_argument(
        "--outdir", type=pathlib.Path, default=pathlib.Path("results/results_combined"),
        help="Output directory for PNGs (default: results/results_combined)",
    )
    args = ap.parse_args()
    OUT_DIR = args.outdir
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SEED_DIRS, SEEDS = _discover_seeds()
    PALETTE = _palette(SEEDS)
    SEED_LABELS = {s: f"seed {s}" for s in SEEDS}

    print(f"\nGenerating combined comparison figures → {OUT_DIR}/")
    print(f"  Seeds discovered: {SEEDS}\n")
    plot_latency_sweep()           # 1  - latency vs delay (line + error bars)
    plot_cdf()                     # 4  - smooth CDF across conditions
    plot_loss_effect()             # 5  - boxplots: loss effect across seeds
    plot_latency_breakdown()       # 6  - stacked bar: component breakdown
    plot_distance_vs_latency()     # 7  - scatter: separation vs latency
    plot_experiment_comparison()   # 8  - horizontal bar chart: all experiments
    plot_summary_table()           # 9  - summary stats table (now includes F1 + clock exps)
    plot_timeseries_baseline()     # 10 - timeseries: mean ± SD envelope
    plot_trajectory_overlay()      # 11 - XY 2D occupancy map + tracks
    plot_trajectory_z_time()       # 12 - Z altitude: mean ± SD envelope
    plot_violin_all_experiments()  # 13 - violin: full distribution all experiments
    plot_detection_performance()   # 14 - precision / recall / F1 per experiment
    plot_queuing_wait()            # 15 - queuing wait decomposition (fast vs slow path)
    plot_clock_sync_effect()       # 16 - clock offset / jitter impact
    plot_fire_growth()             # 17 - fire cell count & visibility over time
    plot_confusion_balance()       # 18 - TP/FN composition per experiment
    plot_queue_vs_visibility()     # 19 - queuing wait vs visibility
    plot_stability_map()           # 20 - mean-vs-std stability map
    print(f"\nDone — {len(list(OUT_DIR.glob('*.png')))} plots in {OUT_DIR}/")


if __name__ == "__main__":
    main()
