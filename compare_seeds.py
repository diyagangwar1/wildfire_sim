#!/usr/bin/env python3
"""
compare_seeds.py  —  cross-seed aggregate comparison visuals (latency-focused)

Discovers results_seed*/ directories in the repo (or falls back to seeds 42,99,123)
and writes results_combined/ with comparison figures. Detection precision/recall
plots are omitted — simulation sensing is not a stand-in for real detector metrics.
"""

import argparse
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
OUT_DIR = pathlib.Path("results")


def _discover_seeds() -> Tuple[Dict[int, pathlib.Path], List[int]]:
    dirs: dict[int, pathlib.Path] = {}
    # Look in data/ first (new layout), then fall back to repo root (legacy)
    search_patterns = ["data/results_seed*", "results_seed*"]
    for pattern in search_patterns:
        for p in sorted(glob.glob(pattern)):
            if not os.path.isdir(p):
                continue
            try:
                s = int(os.path.basename(p).replace("results_seed", ""))
                dirs[s] = pathlib.Path(p)
            except ValueError:
                continue
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
    ]
    TYPE_COLOR = {
        "baseline": "#4CAF50",
        "cam":      "#2196F3",
        "thermal":  "#FF9800",
        "loss":     "#E91E63",
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
        ("baseline",            "Baseline",               0,    0,    0.0),
        ("cam_delay_10ms",      "Cam 10 ms",              10,   0,    0.0),
        ("cam_delay_50ms",      "Cam 50 ms",              50,   0,    0.0),
        ("cam_delay_100ms",     "Cam 100 ms",             100,  0,    0.0),
        ("cam_delay_500ms",     "Cam 500 ms",             500,  0,    0.0),
        ("cam_delay_1000ms",    "Cam 1000 ms",            1000, 0,    0.0),
        ("thermal_delay_10ms",  "Thermal 10 ms",          0,    10,   0.0),
        ("thermal_delay_50ms",  "Thermal 50 ms",          0,    50,   0.0),
        ("thermal_delay_100ms", "Thermal 100 ms",         0,    100,  0.0),
        ("thermal_delay_500ms", "Thermal 500 ms",         0,    500,  0.0),
        ("thermal_delay_1000ms","Thermal 1000 ms",        0,    1000, 0.0),
        ("cam_10ms_loss1pct",   "Cam 10ms + 1% loss",    10,   0,    1.0),
        ("cam_100ms_loss1pct",  "Cam 100ms + 1% loss",   100,  0,    1.0),
        ("cam_100ms_loss5pct",  "Cam 100ms + 5% loss",   100,  0,    5.0),
    ]

    rows = []
    for exp, label, cam_d, th_d, loss in ALL_EXPS:
        e2e_vals = [e2e_stats(load(s, exp))["mean"] for s in SEEDS]
        p95_vals = [e2e_stats(load(s, exp))["p95"] for s in SEEDS]
        e_m, e_s = mean_err(e2e_vals)
        p_m, p_s = mean_err(p95_vals)
        rows.append([label,
                     f"{cam_d}",
                     f"{th_d}",
                     f"{loss:.0f}%",
                     f"{e_m:.0f} ± {e_s:.0f}",
                     f"{p_m:.0f} ± {p_s:.0f}"])

    col_headers = ["Experiment", "Cam\ndelay", "Thermal\ndelay", "Loss",
                   "E2E mean (ms)\n± SD", "E2E p95 (ms)\n± SD"]
    col_widths = [0.24, 0.08, 0.1, 0.07, 0.22, 0.22]

    fig, ax = plt.subplots(figsize=(15, 8))
    ax.axis("off")
    fig.suptitle(
        f"Summary: All Experiments — Mean ± SD Across Seeds ({', '.join(map(str, SEEDS))})",
        fontsize=13, fontweight="bold", y=0.98,
    )

    tbl = ax.table(cellText=rows, colLabels=col_headers,
                   colWidths=col_widths,
                   cellLoc="center", loc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.scale(1, 1.55)

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
# 11.  Drone XY density heatmap — hexbin per drone type (baseline, all seeds)
# ─────────────────────────────────────────────────────────────────────────
def plot_trajectory_overlay():
    th_x, th_y, im_x, im_y = [], [], [], []
    for s in SEEDS:
        df = load(s, "baseline")
        if df.empty:
            continue
        if "thermal_x" in df.columns and "thermal_y" in df.columns:
            sub = df.dropna(subset=["thermal_x", "thermal_y"])
            th_x.extend(sub["thermal_x"].tolist())
            th_y.extend(sub["thermal_y"].tolist())
        if "imagery_x" in df.columns and "imagery_y" in df.columns:
            sub = df.dropna(subset=["imagery_x", "imagery_y"])
            im_x.extend(sub["imagery_x"].tolist())
            im_y.extend(sub["imagery_y"].tolist())

    fig, axes = plt.subplots(1, 2, figsize=(13, 6))
    fig.suptitle(
        f"Drone Position Density — Baseline ({len(SEEDS)} seeds pooled)\n"
        "Colour = visit count  (darker = drones spend more time here)",
        fontsize=13, fontweight="bold",
    )

    for ax, xs, ys, title, cmap in [
        (axes[0], th_x, th_y, "Thermal drone",  "Blues"),
        (axes[1], im_x, im_y, "Imagery drone",  "Oranges"),
    ]:
        if xs:
            hb = ax.hexbin(xs, ys, gridsize=30, cmap=cmap, mincnt=1, linewidths=0.2)
            plt.colorbar(hb, ax=ax, label="Visit count")
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
    ]
    TYPE_COLOR = {
        "baseline": "#4CAF50",
        "cam":      "#2196F3",
        "thermal":  "#FF9800",
        "loss":     "#E91E63",
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

# ──────────────────────────────────────────────────────────────────────────
def main() -> None:
    global OUT_DIR, SEED_DIRS, SEEDS, PALETTE, SEED_LABELS
    ap = argparse.ArgumentParser(description="Cross-seed latency comparison figures.")
    ap.add_argument(
        "--outdir", type=pathlib.Path, default=pathlib.Path("results_combined"),
        help="Output directory for PNGs (default: results_combined)",
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
    plot_summary_table()           # 9  - summary stats table
    plot_timeseries_baseline()     # 10 - timeseries: mean ± SD envelope
    plot_trajectory_overlay()      # 11 - XY density hexbin heatmap
    plot_trajectory_z_time()       # 12 - Z altitude: mean ± SD envelope
    plot_violin_all_experiments()  # 13 - violin: full distribution all experiments
    print(f"\nDone — {len(list(OUT_DIR.glob('*.png')))} plots in {OUT_DIR}/")


if __name__ == "__main__":
    main()
