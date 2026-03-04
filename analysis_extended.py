#!/usr/bin/env python3
"""
analysis_extended.py — deeper analysis beyond the basic sweep plots.

Figures produced in results_combined/:
  11_precision_recall_curve.png  — PR curve sweeping K=1..5 per condition
  12_k_sweep_bars.png            — recall & precision at K=1,2,3 for all 14 exps
  13_seed123_drone_distance.png  — why seed123 underperforms (drone trajectory)
  14_false_alarm_vs_recall.png   — false-alarm rate vs recall per condition
  15_time_to_first_detection.png — how quickly each condition fires first alarm
  16_sync_gap_analysis.png       — sync_gap distribution (how stale are matched pairs)
"""

import json, pathlib, warnings
from collections import deque
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec

warnings.filterwarnings("ignore")

SEEDS     = [42, 99, 123]
SEED_DIRS = {s: pathlib.Path(f"results_seed{s}") for s in SEEDS}
OUT_DIR   = pathlib.Path("results_combined")
OUT_DIR.mkdir(exist_ok=True)

PALETTE   = {42: "#2196F3", 99: "#FF9800", 123: "#4CAF50"}

# ── helpers ────────────────────────────────────────────────────────────────
def load(seed, exp):
    p = SEED_DIRS[seed] / exp / "latency_log.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

def pool(exp):
    """Pool all rows from all seeds for one experiment."""
    rows = []
    for s in SEEDS:
        rows.extend(load(s, exp))
    return rows

def simulate_k(rows, k_confirm, k_window=5):
    """Re-run the rolling-window decision with a different K threshold.
    Returns (recall, precision, fpr) or (nan,nan,nan) if no data."""
    if not rows:
        return np.nan, np.nan, np.nan
    window = deque(maxlen=k_window)
    tp = fp = fn = tn = 0
    for r in rows:
        window.append(r["raw_signal"])
        dec = sum(window) >= k_confirm
        fw  = r["fire_window"]
        if   fw and dec:     tp += 1
        elif fw and not dec: fn += 1
        elif not fw and dec: fp += 1
        else:                tn += 1
    recall    = tp / (tp + fn) if (tp + fn) > 0 else np.nan
    precision = tp / (tp + fp) if (tp + fp) > 0 else np.nan
    fpr       = fp / (fp + tn) if (fp + tn) > 0 else np.nan
    return recall, precision, fpr


# ─────────────────────────────────────────────────────────────────────────
# 11.  Precision-Recall curve: sweep K=1..5 per condition
# ─────────────────────────────────────────────────────────────────────────
def plot_pr_curve():
    conditions = [
        ("baseline",            "Baseline (0 ms)",        "#2196F3", "o",  "-"),
        ("cam_delay_100ms",     "Camera 100 ms",           "#FF9800", "s",  "--"),
        ("cam_delay_500ms",     "Camera 500 ms",           "#F44336", "^",  "-."),
        ("cam_delay_1000ms",    "Camera 1000 ms",          "#9C27B0", "D",  ":"),
        ("thermal_delay_500ms", "Thermal 500 ms",          "#009688", "v",  "--"),
        ("thermal_delay_1000ms","Thermal 1000 ms",         "#795548", "P",  "-."),
        ("cam_100ms_loss5pct",  "Cam 100ms + 5% loss",    "#E91E63", "*",  ":"),
    ]
    K_values = [1, 2, 3, 4, 5]

    fig, axes = plt.subplots(1, 2, figsize=(15, 6))
    fig.suptitle(
        "Precision–Recall Tradeoff: Sweeping Confirmation Threshold K  (1 to 5 out of 5)\n"
        "All seeds pooled  •  Each marker = one K value  •  arrow = increasing K",
        fontsize=12, fontweight="bold",
    )

    for ax, title, use_fpr in [
        (axes[0], "Precision vs Recall",               False),
        (axes[1], "False-Alarm Rate vs Recall (ROC)",  True),
    ]:
        for exp, label, color, marker, ls in conditions:
            rows = pool(exp)
            if not rows:
                continue
            recalls, ys = [], []
            for k in K_values:
                rec, prec, fpr = simulate_k(rows, k)
                recalls.append(rec * 100 if not np.isnan(rec) else np.nan)
                y = fpr * 100 if use_fpr else (prec * 100 if not np.isnan(prec) else np.nan)
                ys.append(y)

            ax.plot(recalls, ys, color=color, linestyle=ls,
                    linewidth=1.8, alpha=0.85)
            ax.scatter(recalls, ys, color=color, marker=marker,
                       s=60, zorder=5)

            # label each K dot at K=1 (highest recall)
            if not np.isnan(recalls[0]) and not np.isnan(ys[0]):
                ax.annotate(f"K=1", xy=(recalls[0], ys[0]),
                            xytext=(recalls[0] - 4, ys[0] + 2),
                            fontsize=6.5, color=color, alpha=0.9)
            # K=2 (current setting) gets a star
            if not np.isnan(recalls[1]) and not np.isnan(ys[1]):
                ax.plot(recalls[1], ys[1], marker="*", color=color,
                        markersize=14, zorder=6)

        handles = [plt.Line2D([0], [0], color=c, marker=m, linestyle=ls,
                               linewidth=1.8, label=f"{lbl}")
                   for exp, lbl, c, m, ls in conditions]
        handles.append(plt.Line2D([0], [0], marker="*", color="gray",
                                   markersize=10, linestyle="None",
                                   label="★ = current setting (K=2)"))
        ax.legend(handles=handles, fontsize=7.5, loc="lower left" if not use_fpr else "upper left")

        if use_fpr:
            ax.set_xlabel("False-alarm rate  (%)", fontsize=11)
            ax.set_ylabel("Recall / hit-rate  (%)", fontsize=11)
            ax.plot([0, 100], [0, 100], "k--", linewidth=0.8, alpha=0.4,
                    label="random classifier")
        else:
            ax.set_xlabel("Recall / hit-rate  (%)", fontsize=11)
            ax.set_ylabel("Precision  (%)", fontsize=11)

        ax.set_title(title, fontsize=11)
        ax.set_xlim(-2, 105); ax.set_ylim(-2, 105)
        ax.grid(True, alpha=0.25)

    plt.tight_layout()
    out = OUT_DIR / "11_precision_recall_curve.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────
# 12.  K-sweep bar chart for all 14 experiments × K=1,2,3
# ─────────────────────────────────────────────────────────────────────────
def plot_k_sweep_bars():
    ALL_EXPS = [
        ("baseline",             "Baseline"),
        ("cam_delay_10ms",       "Cam 10ms"),
        ("cam_delay_50ms",       "Cam 50ms"),
        ("cam_delay_100ms",      "Cam 100ms"),
        ("cam_delay_500ms",      "Cam 500ms"),
        ("cam_delay_1000ms",     "Cam 1000ms"),
        ("thermal_delay_10ms",   "Th 10ms"),
        ("thermal_delay_50ms",   "Th 50ms"),
        ("thermal_delay_100ms",  "Th 100ms"),
        ("thermal_delay_500ms",  "Th 500ms"),
        ("thermal_delay_1000ms", "Th 1000ms"),
        ("cam_10ms_loss1pct",    "Cam10+1%loss"),
        ("cam_100ms_loss1pct",   "Cam100+1%loss"),
        ("cam_100ms_loss5pct",   "Cam100+5%loss"),
    ]
    K_vals   = [1, 2, 3]
    K_colors = ["#4CAF50", "#2196F3", "#F44336"]
    K_labels = ["K=1 (loose)", "K=2 (current)", "K=3 (strict)"]

    x    = np.arange(len(ALL_EXPS))
    bw   = 0.25

    fig, axes = plt.subplots(2, 1, figsize=(16, 11), sharex=True)
    fig.suptitle("Effect of Confirmation Threshold K on Recall and Precision\n"
                 "(all seeds pooled)", fontsize=13, fontweight="bold")

    for ax, metric, ylabel, ylim in [
        (axes[0], "recall",    "Recall / hit-rate (%)", (0, 105)),
        (axes[1], "precision", "Precision (%)",         (0, 105)),
    ]:
        for ki, (k, color, label) in enumerate(zip(K_vals, K_colors, K_labels)):
            vals = []
            for exp, _ in ALL_EXPS:
                rows = pool(exp)
                rec, prec, _ = simulate_k(rows, k)
                v = (rec if metric == "recall" else prec)
                vals.append(v * 100 if not np.isnan(v) else 0)
            bars = ax.bar(x + ki * bw, vals, bw, color=color,
                          alpha=0.85, label=label)
            # value labels on top
            for bar, v in zip(bars, vals):
                if v > 5:
                    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                            f"{v:.0f}", ha="center", va="bottom",
                            fontsize=5.5, rotation=90, color=color)

        ax.set_ylabel(ylabel, fontsize=11)
        ax.set_ylim(*ylim)
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.25, axis="y")

        # separator lines between cam / thermal / loss groups
        ax.axvline(5.75, color="gray", linewidth=0.8, linestyle="--", alpha=0.5)
        ax.axvline(10.75, color="gray", linewidth=0.8, linestyle="--", alpha=0.5)
        ax.text(2.5,  ylim[1] - 6, "Camera delay",  ha="center", fontsize=8, color="gray")
        ax.text(8.0,  ylim[1] - 6, "Thermal delay", ha="center", fontsize=8, color="gray")
        ax.text(12.5, ylim[1] - 6, "Packet loss",   ha="center", fontsize=8, color="gray")

    axes[1].set_xticks(x + bw)
    axes[1].set_xticklabels([lbl for _, lbl in ALL_EXPS], rotation=35,
                             ha="right", fontsize=8)

    plt.tight_layout()
    out = OUT_DIR / "12_k_sweep_bars.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────
# 13.  Seed123 investigation — drone distance over time
# ─────────────────────────────────────────────────────────────────────────
def plot_seed123_investigation():
    fig = plt.figure(figsize=(16, 10))
    fig.suptitle("Why Does Seed 123 Underperform on Thermal Delays?\n"
                 "Drone Distance Over Time + Raw Signal Rate",
                 fontsize=13, fontweight="bold")
    gs = GridSpec(2, 3, figure=fig, hspace=0.45, wspace=0.35)

    thermal_exps = [
        ("baseline",             "Baseline"),
        ("thermal_delay_100ms",  "Thermal 100ms"),
        ("thermal_delay_500ms",  "Thermal 500ms"),
    ]

    for col, (exp, title) in enumerate(thermal_exps):
        ax_dist = fig.add_subplot(gs[0, col])
        ax_raw  = fig.add_subplot(gs[1, col])

        for s in SEEDS:
            rows = load(s, exp)
            if not rows:
                continue
            t0 = rows[0]["fusion_done_ns"]
            times = [(r["fusion_done_ns"] - t0) / 1e9 for r in rows]
            dists = [r.get("thermal_distance_m", np.nan) for r in rows]
            raws  = [int(r["raw_signal"]) for r in rows]
            fw    = [r["fire_window"] for r in rows]

            lw   = 2.5 if s == 123 else 1.0
            alpha= 0.9 if s == 123 else 0.45
            ax_dist.plot(times, dists, color=PALETTE[s], linewidth=lw,
                         alpha=alpha, label=f"seed {s}")

            # rolling raw signal rate (window=10)
            roll = pd.Series(raws).rolling(10, min_periods=1).mean() * 100
            ax_raw.plot(times, roll.values, color=PALETTE[s],
                        linewidth=lw, alpha=alpha, label=f"seed {s}")
            # shade fire windows
            for i, (t, f) in enumerate(zip(times, fw)):
                if f:
                    ax_raw.axvspan(t - 0.5, t + 0.5, alpha=0.08, color="red")

        ax_dist.set_title(f"{title}\nThermal Drone Distance", fontsize=9)
        ax_dist.set_ylabel("Distance to controller (m)", fontsize=8)
        ax_dist.set_xlabel("Time (s)", fontsize=8)
        ax_dist.legend(fontsize=7)
        ax_dist.grid(True, alpha=0.25)
        ax_dist.axhline(100, color="red", linewidth=0.8, linestyle=":",
                        alpha=0.5, label="100m (high drop zone)")

        ax_raw.set_title(f"{title}\nRolling Raw Signal Rate", fontsize=9)
        ax_raw.set_ylabel("Raw fire signal rate (%) rolling-10", fontsize=8)
        ax_raw.set_xlabel("Time (s)", fontsize=8)
        ax_raw.legend(fontsize=7)
        ax_raw.grid(True, alpha=0.25)
        ax_raw.set_ylim(0, 105)

    plt.tight_layout()
    out = OUT_DIR / "13_seed123_investigation.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────
# 14.  False-alarm rate vs Recall — one point per experiment (current K=2)
#      + annotated tradeoff frontier
# ─────────────────────────────────────────────────────────────────────────
def plot_fpr_vs_recall():
    CAT = {
        "baseline":             ("Baseline",    "#2196F3", "o"),
        "cam_delay_10ms":       ("Cam 10ms",    "#64B5F6", "s"),
        "cam_delay_50ms":       ("Cam 50ms",    "#42A5F5", "s"),
        "cam_delay_100ms":      ("Cam 100ms",   "#FF9800", "s"),
        "cam_delay_500ms":      ("Cam 500ms",   "#F44336", "s"),
        "cam_delay_1000ms":     ("Cam 1000ms",  "#B71C1C", "s"),
        "thermal_delay_10ms":   ("Th 10ms",     "#81C784", "^"),
        "thermal_delay_50ms":   ("Th 50ms",     "#4CAF50", "^"),
        "thermal_delay_100ms":  ("Th 100ms",    "#388E3C", "^"),
        "thermal_delay_500ms":  ("Th 500ms",    "#1B5E20", "^"),
        "thermal_delay_1000ms": ("Th 1000ms",   "#004D40", "^"),
        "cam_10ms_loss1pct":    ("C10+1%loss",  "#CE93D8", "D"),
        "cam_100ms_loss1pct":   ("C100+1%loss", "#9C27B0", "D"),
        "cam_100ms_loss5pct":   ("C100+5%loss", "#4A148C", "D"),
    }

    fig, axes = plt.subplots(1, 2, figsize=(15, 6))
    fig.suptitle("False-Alarm Rate vs Recall — All Conditions\n"
                 "Left: current K=2 setting  |  Right: K=1 vs K=2 overlay",
                 fontsize=12, fontweight="bold")

    # Left: K=2, one scatter per experiment
    ax = axes[0]
    for exp, (label, color, marker) in CAT.items():
        rows = pool(exp)
        rec, prec, fpr = simulate_k(rows, 2)
        if np.isnan(rec) or np.isnan(fpr):
            continue
        ax.scatter(fpr * 100, rec * 100, color=color, marker=marker,
                   s=90, zorder=5)
        ax.annotate(label, xy=(fpr * 100, rec * 100),
                    xytext=(fpr * 100 + 0.5, rec * 100 + 0.5),
                    fontsize=6.5, color=color)

    ax.plot([0, 100], [0, 100], "k--", linewidth=0.8, alpha=0.3,
            label="random classifier")
    ax.set_xlabel("False-alarm rate (%)", fontsize=11)
    ax.set_ylabel("Recall (%)", fontsize=11)
    ax.set_title("Current setting: K=2 / 5", fontsize=11)
    ax.set_xlim(-2, 80); ax.set_ylim(-2, 105)
    ax.grid(True, alpha=0.25)

    # Ideal corner annotation
    ax.annotate("← ideal\n(low FA, high recall)", xy=(5, 95),
                fontsize=8, color="green",
                arrowprops=dict(arrowstyle="->", color="green"),
                xytext=(15, 80))

    # Right: K=1 vs K=2 per experiment (arrows showing direction of change)
    ax = axes[1]
    for exp, (label, color, marker) in CAT.items():
        rows = pool(exp)
        rec2, _, fpr2 = simulate_k(rows, 2)
        rec1, _, fpr1 = simulate_k(rows, 1)
        if any(np.isnan(v) for v in [rec2, fpr2, rec1, fpr1]):
            continue
        # K=2 point
        ax.scatter(fpr2 * 100, rec2 * 100, color=color, marker=marker,
                   s=70, zorder=5, alpha=0.6)
        # K=1 point  
        ax.scatter(fpr1 * 100, rec1 * 100, color=color, marker="*",
                   s=100, zorder=6)
        # Arrow K=2 → K=1
        ax.annotate("", xy=(fpr1 * 100, rec1 * 100),
                    xytext=(fpr2 * 100, rec2 * 100),
                    arrowprops=dict(arrowstyle="->", color=color,
                                   lw=1.2, alpha=0.7))

    k2_patch = plt.Line2D([0], [0], marker="o", color="gray",
                           markersize=8, linestyle="None",
                           label="● K=2 (current)")
    k1_patch = plt.Line2D([0], [0], marker="*", color="gray",
                           markersize=10, linestyle="None",
                           label="★ K=1 (loose)")
    ax.legend(handles=[k2_patch, k1_patch], fontsize=9)
    ax.plot([0, 100], [0, 100], "k--", linewidth=0.8, alpha=0.3)
    ax.set_xlabel("False-alarm rate (%)", fontsize=11)
    ax.set_ylabel("Recall (%)", fontsize=11)
    ax.set_title("Arrows: K=2 → K=1  (higher recall, more false alarms)", fontsize=11)
    ax.set_xlim(-2, 90); ax.set_ylim(-2, 105)
    ax.grid(True, alpha=0.25)

    plt.tight_layout()
    out = OUT_DIR / "14_false_alarm_vs_recall.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────
# 15.  Time-to-first-detection: how quickly does each condition sound an alarm?
# ─────────────────────────────────────────────────────────────────────────
def plot_time_to_first_detection():
    conditions = [
        ("baseline",            "Baseline",       "#2196F3"),
        ("cam_delay_100ms",     "Cam 100ms",       "#FF9800"),
        ("cam_delay_500ms",     "Cam 500ms",       "#F44336"),
        ("cam_delay_1000ms",    "Cam 1000ms",      "#9C27B0"),
        ("thermal_delay_100ms", "Thermal 100ms",   "#4CAF50"),
        ("thermal_delay_500ms", "Thermal 500ms",   "#009688"),
        ("thermal_delay_1000ms","Thermal 1000ms",  "#795548"),
        ("cam_100ms_loss5pct",  "C100+5%loss",     "#E91E63"),
    ]

    # For each seed+experiment, find the time-since-start of the first TP decision
    fig, axes = plt.subplots(1, 2, figsize=(15, 6))
    fig.suptitle("Time-to-First Fire Detection\n"
                 "(how long after process start until first TP decision, per run)",
                 fontsize=13, fontweight="bold")

    all_ttfd = {exp: [] for exp, _ , _ in conditions}
    for exp, _, _ in conditions:
        for s in SEEDS:
            rows = load(s, exp)
            if not rows:
                continue
            t0_ns = rows[0]["fusion_done_ns"]
            window = deque(maxlen=5)
            for r in rows:
                window.append(r["raw_signal"])
                dec = sum(window) >= 2
                if dec and r["fire_window"]:   # first TP
                    ttfd = (r["fusion_done_ns"] - t0_ns) / 1e9
                    all_ttfd[exp].append(ttfd)
                    break

    # Box plot
    ax = axes[0]
    data   = [all_ttfd[exp] for exp, _, _ in conditions]
    labels = [lbl for _, lbl, _ in conditions]
    colors = [col for _, _, col in conditions]

    bp = ax.boxplot(data, patch_artist=True, notch=False,
                    medianprops=dict(color="black", linewidth=2))
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=8)
    ax.set_ylabel("Seconds to first TP detection", fontsize=11)
    ax.set_title("Distribution (box = min/Q1/median/Q3/max)", fontsize=10)
    ax.grid(True, alpha=0.25, axis="y")

    # Mean bar chart
    ax = axes[1]
    means = [np.mean(v) if v else np.nan for v in data]
    errs  = [np.std(v, ddof=0) if len(v) > 1 else 0 for v in data]
    xpos  = np.arange(len(conditions))
    bars  = ax.bar(xpos, means, color=colors, alpha=0.8, yerr=errs,
                   capsize=4, error_kw=dict(linewidth=1.5))
    for bar, m in zip(bars, means):
        if not np.isnan(m):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                    f"{m:.1f}s", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(xpos)
    ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=8)
    ax.set_ylabel("Mean seconds to first TP detection", fontsize=11)
    ax.set_title("Mean ± SD (across 3 seeds)", fontsize=10)
    ax.grid(True, alpha=0.25, axis="y")
    ax.set_ylim(bottom=0)

    plt.tight_layout()
    out = OUT_DIR / "15_time_to_first_detection.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ─────────────────────────────────────────────────────────────────────────
# 16.  Sync-gap analysis: how stale are the fused pairs?
# ─────────────────────────────────────────────────────────────────────────
def plot_sync_gap():
    conditions = [
        ("baseline",            "Baseline",        "#2196F3"),
        ("cam_delay_10ms",      "Cam 10ms",         "#64B5F6"),
        ("cam_delay_100ms",     "Cam 100ms",         "#FF9800"),
        ("cam_delay_500ms",     "Cam 500ms",         "#F44336"),
        ("cam_delay_1000ms",    "Cam 1000ms",        "#9C27B0"),
        ("thermal_delay_100ms", "Thermal 100ms",     "#4CAF50"),
        ("thermal_delay_1000ms","Thermal 1000ms",    "#795548"),
    ]
    # sync_gap = |imagery_rx_ns - thermal_rx_ns| / 1e6  (arrival time difference)
    # This tells us how "simultaneous" the two sensor readings actually were at the controller.

    fig, axes = plt.subplots(1, 2, figsize=(15, 6))
    fig.suptitle("Sensor Arrival Sync-Gap at Controller\n"
                 "|imagery arrival − thermal arrival|  (ms)  —  all seeds pooled",
                 fontsize=12, fontweight="bold")

    from scipy.stats import gaussian_kde

    ax_cdf = axes[0]
    ax_box = axes[1]

    box_data   = []
    box_labels = []
    box_colors = []

    xs = np.linspace(0, 2000, 800)

    for exp, label, color in conditions:
        rows = pool(exp)
        if not rows:
            continue
        gaps = [abs(r["imagery_rx_ns"] - r["thermal_rx_ns"]) / 1e6
                for r in rows
                if r.get("imagery_rx_ns") and r.get("thermal_rx_ns")]
        if len(gaps) < 5:
            continue
        v = np.array(gaps)

        # smooth CDF
        kde = gaussian_kde(v, bw_method=0.2)
        pdf = kde(xs)
        cdf = np.cumsum(pdf) * (xs[1] - xs[0])
        cdf = cdf / cdf[-1] * 100
        ax_cdf.plot(xs, cdf, linewidth=2, color=color, label=f"{label} (n={len(v)})")
        # mark median
        med_idx = np.searchsorted(cdf, 50)
        if med_idx < len(xs):
            ax_cdf.scatter(xs[med_idx], 50, color=color, s=40, zorder=5)

        box_data.append(np.clip(v, 0, 3000))
        box_labels.append(label)
        box_colors.append(color)

    ax_cdf.axhline(50, color="gray", linewidth=0.8, linestyle=":", alpha=0.6)
    ax_cdf.axhline(95, color="gray", linewidth=0.8, linestyle=":", alpha=0.6)
    ax_cdf.axvline(600, color="red", linewidth=1, linestyle="--", alpha=0.5,
                   label="sync threshold (600ms)")
    ax_cdf.set_xlabel("Arrival sync-gap (ms)", fontsize=11)
    ax_cdf.set_ylabel("Cumulative % of fusions", fontsize=11)
    ax_cdf.set_title("CDF of Sync-Gap", fontsize=11)
    ax_cdf.set_xlim(0, 2000)
    ax_cdf.legend(fontsize=8)
    ax_cdf.grid(True, alpha=0.25)

    bp = ax_box.boxplot(box_data, patch_artist=True,
                        medianprops=dict(color="black", linewidth=2),
                        flierprops=dict(marker=".", markersize=2, alpha=0.3))
    for patch, color in zip(bp["boxes"], box_colors):
        patch.set_facecolor(color); patch.set_alpha(0.7)
    ax_box.set_xticklabels(box_labels, rotation=35, ha="right", fontsize=8)
    ax_box.set_ylabel("Arrival sync-gap (ms)", fontsize=11)
    ax_box.set_title("Sync-Gap Distribution per Condition", fontsize=11)
    ax_box.axhline(600, color="red", linewidth=1.2, linestyle="--",
                   alpha=0.6, label="600ms threshold")
    ax_box.legend(fontsize=8)
    ax_box.grid(True, alpha=0.25, axis="y")
    ax_box.set_ylim(0, 3000)

    plt.tight_layout()
    out = OUT_DIR / "16_sync_gap_analysis.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Wrote {out}")


# ──────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\nRunning extended analysis → {OUT_DIR}/\n")
    plot_pr_curve()
    plot_k_sweep_bars()
    plot_seed123_investigation()
    plot_fpr_vs_recall()
    plot_time_to_first_detection()
    plot_sync_gap()
    new = sorted(OUT_DIR.glob("1[0-9]_*.png"))
    print(f"\nDone — {len(new)} new plots:")
    for p in new:
        print(f"  {p.name}")
