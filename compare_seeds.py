#!/usr/bin/env python3
"""
compare_seeds.py  —  cross-seed aggregate comparison visuals
Reads results_seed42/, results_seed99/, results_seed123/ and produces
results_combined/ with publication-quality comparison figures.
"""

import json, os, pathlib, warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
from scipy import stats

warnings.filterwarnings("ignore")

# ── paths ──────────────────────────────────────────────────────────────────
SEEDS      = [42, 99, 123]
SEED_DIRS  = {s: pathlib.Path(f"results_seed{s}") for s in SEEDS}
OUT_DIR    = pathlib.Path("results_combined")
OUT_DIR.mkdir(exist_ok=True)

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

PALETTE = {42: "#2196F3", 99: "#FF9800", 123: "#4CAF50"}
SEED_LABELS = {42: "seed 42", 99: "seed 99", 123: "seed 123"}

# ── helpers ────────────────────────────────────────────────────────────────
def load(seed, exp_name):
    p = SEED_DIRS[seed] / exp_name / "latency_log.jsonl"
    if not p.exists():
        return pd.DataFrame()
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    return pd.DataFrame(rows)

def hit_miss_stats(df):
    if df.empty:
        return dict(tp=0, fp=0, fn=0, tn=0, recall=np.nan, precision=np.nan)
    c = df["hit_miss"].value_counts()
    tp = c.get("TP", 0); fp = c.get("FP", 0)
    fn = c.get("FN", 0); tn = c.get("TN", 0)
    recall    = tp / (tp + fn) if (tp + fn) > 0 else np.nan
    precision = tp / (tp + fp) if (tp + fp) > 0 else np.nan
    return dict(tp=tp, fp=fp, fn=fn, tn=tn, recall=recall, precision=precision)

def e2e_stats(df):
    if df.empty:
        return dict(mean=np.nan, median=np.nan, p95=np.nan, p99=np.nan)
    v = df["e2e_ms"].values
    return dict(mean=np.mean(v), median=np.median(v),
                p95=np.percentile(v, 95), p99=np.percentile(v, 99))

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
    fig.suptitle("End-to-End Latency vs Network Delay\n(mean ± 1 SD across 3 seeds, 180 s runs)",
                 fontsize=13, fontweight="bold")

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
# 2.  Detection Recall vs Camera / Thermal Delay  (mean ± SD across seeds)
# ─────────────────────────────────────────────────────────────────────────
def plot_recall_sweep():
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    fig.suptitle("Fire-Detection Recall vs Network Delay\n(mean ± 1 SD across 3 seeds, 180 s runs)",
                 fontsize=13, fontweight="bold")

    for ax, exps, xlabel, title in [
        (axes[0], CAM_EXPS, "Camera (imagery) link delay (ms)", "Camera Link Delay Sweep"),
        (axes[1], TH_EXPS,  "Thermal link delay (ms)",          "Thermal Link Delay Sweep"),
    ]:
        agg_data = agg(exps, hit_miss_stats)
        delays = [d for _, d, _ in exps]

        # Per-seed lines (light)
        for s in SEEDS:
            ys = [agg_data[name][s]["recall"] * 100
                  if not np.isnan(agg_data[name][s]["recall"]) else np.nan
                  for name, *_ in exps]
            ax.plot(delays, ys, marker="o", linewidth=1, alpha=0.45,
                    color=PALETTE[s], label=SEED_LABELS[s])

        # Mean line (dark)
        means, errs = [], []
        for name, *_ in exps:
            vals = [agg_data[name][s]["recall"] * 100 for s in SEEDS]
            m, e = mean_err(vals)
            means.append(m); errs.append(e)
        ax.errorbar(delays, means, yerr=errs, marker="D", linewidth=2.5,
                    color="black", capsize=5, label="mean ± 1 SD", zorder=5)

        ax.set_xlabel(xlabel, fontsize=11)
        ax.set_ylabel("Recall / hit-rate (%)", fontsize=11)
        ax.set_title(title, fontsize=11)
        ax.legend(fontsize=9)
        ax.set_xticks(delays)
        ax.set_ylim(0, 105)
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    out = OUT_DIR / "2_recall_vs_delay.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")

# ─────────────────────────────────────────────────────────────────────────
# 3.  Precision vs Camera / Thermal Delay
# ─────────────────────────────────────────────────────────────────────────
def plot_precision_sweep():
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    fig.suptitle("Precision vs Network Delay\n(mean ± 1 SD across 3 seeds, 180 s runs)",
                 fontsize=13, fontweight="bold")

    for ax, exps, xlabel, title in [
        (axes[0], CAM_EXPS, "Camera (imagery) link delay (ms)", "Camera Link Delay Sweep"),
        (axes[1], TH_EXPS,  "Thermal link delay (ms)",          "Thermal Link Delay Sweep"),
    ]:
        agg_data = agg(exps, hit_miss_stats)
        delays = [d for _, d, _ in exps]

        for s in SEEDS:
            ys = [agg_data[name][s]["precision"] * 100
                  if not np.isnan(agg_data[name][s]["precision"]) else np.nan
                  for name, *_ in exps]
            ax.plot(delays, ys, marker="s", linewidth=1, alpha=0.45,
                    color=PALETTE[s], label=SEED_LABELS[s])

        means, errs = [], []
        for name, *_ in exps:
            vals = [agg_data[name][s]["precision"] * 100
                    for s in SEEDS
                    if not np.isnan(agg_data[name][s]["precision"])]
            m = np.mean(vals) if vals else np.nan
            e = np.std(vals, ddof=0) if len(vals) > 1 else 0
            means.append(m); errs.append(e)
        ax.errorbar(delays, means, yerr=errs, marker="D", linewidth=2.5,
                    color="black", capsize=5, label="mean ± 1 SD", zorder=5)

        ax.set_xlabel(xlabel, fontsize=11)
        ax.set_ylabel("Precision (%)", fontsize=11)
        ax.set_title(title, fontsize=11)
        ax.legend(fontsize=9)
        ax.set_xticks(delays)
        ax.set_ylim(0, 105)
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    out = OUT_DIR / "3_precision_vs_delay.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")

# ─────────────────────────────────────────────────────────────────────────
# 4.  CDF of E2E latency — smooth KDE version + annotated percentiles
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
        "(all 3 seeds pooled — smooth KDE curve)",
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
# 5.  Packet loss effect (delay + loss vs delay only)
# ─────────────────────────────────────────────────────────────────────────
def plot_loss_effect():
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    fig.suptitle("Effect of Packet Loss on Latency and Detection\n(mean ± 1 SD across 3 seeds)",
                 fontsize=13, fontweight="bold")

    labels  = ["10ms\n0% loss", "10ms\n1% loss", "100ms\n0% loss",
               "100ms\n1% loss", "100ms\n5% loss"]
    x       = np.arange(len(LOSS_EXPS))
    bar_w   = 0.22

    e2e_agg  = agg(LOSS_EXPS, e2e_stats)
    hm_agg   = agg(LOSS_EXPS, hit_miss_stats)

    # -- latency bars --
    ax = axes[0]
    for i, s in enumerate(SEEDS):
        vals = [e2e_agg[name][s]["mean"] for name, *_ in LOSS_EXPS]
        ax.bar(x + i * bar_w, vals, bar_w, label=SEED_LABELS[s],
               color=PALETTE[s], alpha=0.85)
    means = []
    for name, *_ in LOSS_EXPS:
        m, _ = mean_err([e2e_agg[name][s]["mean"] for s in SEEDS])
        means.append(m)
    ax.plot(x + bar_w, means, marker="D", color="black",
            linewidth=2, zorder=5, label="mean")
    ax.set_xticks(x + bar_w); ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("Mean E2E latency (ms)", fontsize=11)
    ax.set_title("Latency", fontsize=11)
    ax.legend(fontsize=9); ax.grid(True, alpha=0.3, axis="y")

    # -- recall bars --
    ax = axes[1]
    for i, s in enumerate(SEEDS):
        vals = [hm_agg[name][s]["recall"] * 100
                if not np.isnan(hm_agg[name][s]["recall"]) else 0
                for name, *_ in LOSS_EXPS]
        ax.bar(x + i * bar_w, vals, bar_w, label=SEED_LABELS[s],
               color=PALETTE[s], alpha=0.85)
    means = []
    for name, *_ in LOSS_EXPS:
        m, _ = mean_err([hm_agg[name][s]["recall"] * 100 for s in SEEDS])
        means.append(m)
    ax.plot(x + bar_w, means, marker="D", color="black",
            linewidth=2, zorder=5, label="mean")
    ax.set_xticks(x + bar_w); ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("Recall / hit-rate (%)", fontsize=11)
    ax.set_title("Recall", fontsize=11)
    ax.set_ylim(0, 105)
    ax.legend(fontsize=9); ax.grid(True, alpha=0.3, axis="y")

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
    fig.suptitle("Mean Latency Breakdown by Component\n(pooled across 3 seeds)",
                 fontsize=13, fontweight="bold")

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
    fig.suptitle("E2E Latency vs Drone Distance to Sensor\n(baseline, all seeds pooled)",
                 fontsize=13, fontweight="bold")

    for ax, col, xlabel in [
        (axes[0], "thermal_distance_m", "Thermal drone distance (m)"),
        (axes[1], "imagery_distance_m", "Imagery drone distance (m)"),
    ]:
        all_dist, all_lat = [], []
        for s in SEEDS:
            df = load(s, "baseline")
            if not df.empty:
                all_dist.extend(df[col].tolist())
                all_lat.extend(df["e2e_ms"].tolist())

        ax.scatter(all_dist, all_lat, alpha=0.35, s=18, color="#2196F3")

        # linear regression line
        if all_dist:
            slope, intercept, r, p, _ = stats.linregress(all_dist, all_lat)
            xs = np.linspace(min(all_dist), max(all_dist), 100)
            ax.plot(xs, slope * xs + intercept, color="red", linewidth=2,
                    label=f"r={r:.2f}, p={p:.3f}")

        ax.set_xlabel(xlabel, fontsize=11)
        ax.set_ylabel("E2E latency (ms)", fontsize=11)
        ax.legend(fontsize=10)
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    out = OUT_DIR / "7_distance_vs_latency.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")

# ─────────────────────────────────────────────────────────────────────────
# 8.  Heatmap — recall across ALL 14 experiments × 3 seeds
# ─────────────────────────────────────────────────────────────────────────
def plot_heatmap():
    ALL_EXPS = [
        "baseline",
        "cam_delay_10ms", "cam_delay_50ms", "cam_delay_100ms",
        "cam_delay_500ms", "cam_delay_1000ms",
        "thermal_delay_10ms", "thermal_delay_50ms", "thermal_delay_100ms",
        "thermal_delay_500ms", "thermal_delay_1000ms",
        "cam_10ms_loss1pct", "cam_100ms_loss1pct", "cam_100ms_loss5pct",
    ]

    recalls = np.full((len(ALL_EXPS), len(SEEDS)), np.nan)
    for i, exp in enumerate(ALL_EXPS):
        for j, s in enumerate(SEEDS):
            df = load(s, exp)
            hm = hit_miss_stats(df)
            recalls[i, j] = hm["recall"] * 100 if not np.isnan(hm["recall"]) else np.nan

    fig, ax = plt.subplots(figsize=(7, 9))
    fig.suptitle("Recall (%) — All Experiments × All Seeds",
                 fontsize=13, fontweight="bold", y=1.01)

    im = ax.imshow(recalls, cmap="RdYlGn", vmin=0, vmax=100, aspect="auto")
    plt.colorbar(im, ax=ax, label="Recall (%)", fraction=0.03)

    ax.set_xticks(range(len(SEEDS)))
    ax.set_xticklabels([f"seed {s}" for s in SEEDS], fontsize=10)
    ax.set_yticks(range(len(ALL_EXPS)))
    ax.set_yticklabels(ALL_EXPS, fontsize=9)

    for i in range(len(ALL_EXPS)):
        for j in range(len(SEEDS)):
            v = recalls[i, j]
            text = f"{v:.0f}" if not np.isnan(v) else "—"
            ax.text(j, i, text, ha="center", va="center",
                    fontsize=9, color="black" if 30 < v < 75 else "white")

    plt.tight_layout()
    out = OUT_DIR / "8_recall_heatmap.png"
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
        e2e_vals  = [e2e_stats(load(s, exp))["mean"]        for s in SEEDS]
        rec_vals  = [hit_miss_stats(load(s, exp))["recall"] * 100 for s in SEEDS]
        prec_vals = [hit_miss_stats(load(s, exp))["precision"] * 100
                     if not np.isnan(hit_miss_stats(load(s, exp))["precision"])
                     else np.nan for s in SEEDS]
        e_m, e_s = mean_err(e2e_vals)
        r_m, r_s = mean_err(rec_vals)
        p_m, p_s = mean_err([v for v in prec_vals if not np.isnan(v)])
        rows.append([label,
                     f"{cam_d}",
                     f"{th_d}",
                     f"{loss:.0f}%",
                     f"{e_m:.0f} ± {e_s:.0f}",
                     f"{r_m:.1f} ± {r_s:.1f}",
                     f"{p_m:.1f} ± {p_s:.1f}"])

    col_headers = ["Experiment", "Cam\ndelay", "Thermal\ndelay", "Loss",
                   "E2E mean (ms)\n± SD", "Recall (%)\n± SD", "Precision (%)\n± SD"]
    col_widths   = [0.22, 0.07, 0.09, 0.06, 0.17, 0.14, 0.14]

    fig, ax = plt.subplots(figsize=(15, 8))
    ax.axis("off")
    fig.suptitle("Summary: All Experiments — Mean ± SD Across Seeds 42 / 99 / 123",
                 fontsize=13, fontweight="bold", y=0.98)

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

    # alternate row shading
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
# 10.  E2E latency timeseries (baseline) — all 3 seeds overlaid
# ─────────────────────────────────────────────────────────────────────────
def plot_timeseries_baseline():
    fig, ax = plt.subplots(figsize=(13, 5))
    fig.suptitle("E2E Latency Over Time — Baseline (all 3 seeds)",
                 fontsize=13, fontweight="bold")

    for s in SEEDS:
        df = load(s, "baseline")
        if df.empty:
            continue
        t0 = df["fusion_done_ns"].iloc[0]
        t  = (df["fusion_done_ns"] - t0) / 1e9
        ax.scatter(t, df["e2e_ms"], s=15, alpha=0.6, color=PALETTE[s],
                   label=SEED_LABELS[s])
        ax.plot(t, df["e2e_ms"].rolling(5, min_periods=1).mean(),
                linewidth=1.5, color=PALETTE[s])

    ax.set_xlabel("Time since start (s)", fontsize=11)
    ax.set_ylabel("E2E latency (ms)", fontsize=11)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.set_ylim(bottom=0)

    plt.tight_layout()
    out = OUT_DIR / "10_timeseries_baseline.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")

# ──────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\nGenerating combined comparison figures → {OUT_DIR}/\n")
    plot_latency_sweep()
    plot_recall_sweep()
    plot_precision_sweep()
    plot_cdf()
    plot_loss_effect()
    plot_latency_breakdown()
    plot_distance_vs_latency()
    plot_heatmap()
    plot_summary_table()
    plot_timeseries_baseline()
    print(f"\nDone — {len(list(OUT_DIR.glob('*.png')))} plots in {OUT_DIR}/")
