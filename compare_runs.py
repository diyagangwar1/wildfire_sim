"""
Cross-run comparison plots for slides.

Usage:
  python3 compare_runs.py --outdir results/comparison
"""

from __future__ import annotations
import argparse
import json
import os
from typing import Dict, List, Any

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np


RUNS = {
    "Baseline\n(no stress)":      "results/baseline/latency_log.jsonl",
    "Delay\n20/80ms\n(asymmetric)":  "results/delay_20_80/latency_log.jsonl",
    "Loss 1%\n(both streams)":    "results/loss_1_1/latency_log.jsonl",
    "Delay\n100ms\n(symmetric)":  "results/delay_100_100/latency_log.jsonl",
}

COLORS = {
    "Sync gap":      "#e05c5c",
    "Network":       "#f0a500",
    "Processing":    "#5ba85b",
    "Unaccounted":   "#aaaaaa",
}

PALETTE = ["#4c72b0", "#dd8452", "#55a868", "#c44e52"]


def load(path: str) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def percentile(xs, p):
    xs = sorted(xs)
    k = int(round(p / 100 * (len(xs) - 1)))
    return xs[max(0, min(k, len(xs) - 1))]


def mean(xs):
    return sum(xs) / max(1, len(xs))


def extract(rows):
    e2e   = [r["e2e_ms"] for r in rows]
    t_net = [r.get("thermal_net_ns", 0) / 1e6 for r in rows]
    i_net = [r.get("imagery_net_ns", 0) / 1e6 for r in rows]
    t_proc= [r.get("thermal_proc_ns", 0) / 1e6 for r in rows]
    i_proc= [r.get("imagery_proc_ns", 0) / 1e6 for r in rows]
    f_proc= [r.get("fusion_proc_ns", 0) / 1e6 for r in rows]
    meas  = [a+b+c+d+e for a,b,c,d,e in zip(t_net, i_net, t_proc, i_proc, f_proc)]
    sync  = [max(0.0, x - m) for x, m in zip(e2e, meas)]
    raw   = [r.get("raw_signal", False) for r in rows]
    dec   = [r.get("decision", False) for r in rows]
    return dict(
        n          = len(e2e),
        e2e_mean   = mean(e2e),
        e2e_p50    = percentile(e2e, 50),
        e2e_p95    = percentile(e2e, 95),
        e2e_p99    = percentile(e2e, 99),
        sync_mean  = mean(sync),
        net_mean   = mean([a+b for a,b in zip(t_net, i_net)]),
        t_net_mean = mean(t_net),
        i_net_mean = mean(i_net),
        proc_mean  = mean([a+b+c for a,b,c in zip(t_proc, i_proc, f_proc)]),
        raw_pct    = 100 * sum(raw) / max(1, len(raw)),
        dec_pct    = 100 * sum(dec) / max(1, len(dec)),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="results/comparison")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    data = {}
    for label, path in RUNS.items():
        if not os.path.exists(path):
            print(f"Missing: {path} — skipping {label.strip()}")
            continue
        data[label] = extract(load(path))

    labels  = list(data.keys())
    short   = [l.replace("\n", " ") for l in labels]
    N       = len(labels)
    x       = np.arange(N)

    # ── 1. E2E latency bar chart (mean / p50 / p95) ──────────────────────────
    fig, ax = plt.subplots(figsize=(10, 6))
    w = 0.25
    means = [data[l]["e2e_mean"] for l in labels]
    p50s  = [data[l]["e2e_p50"]  for l in labels]
    p95s  = [data[l]["e2e_p95"]  for l in labels]

    ax.bar(x - w, means, w, label="Mean",   color="#4c72b0")
    ax.bar(x,     p50s,  w, label="Median", color="#dd8452")
    ax.bar(x + w, p95s,  w, label="P95",    color="#c44e52", alpha=0.85)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylabel("Latency (ms)", fontsize=12)
    ax.set_title("End-to-End Latency Comparison Across Runs", fontsize=13, fontweight="bold")
    ax.legend(fontsize=11)
    ax.yaxis.grid(True, alpha=0.4)
    ax.set_axisbelow(True)
    for i, (m, p5, p9) in enumerate(zip(means, p50s, p95s)):
        ax.text(i - w, m + 8, f"{m:.0f}", ha="center", fontsize=8)
        ax.text(i,     p5 + 8, f"{p5:.0f}", ha="center", fontsize=8)
        ax.text(i + w, p9 + 8, f"{p9:.0f}", ha="center", fontsize=8)
    plt.tight_layout()
    plt.savefig(os.path.join(args.outdir, "e2e_comparison.png"), dpi=200, bbox_inches="tight")
    plt.close()
    print("Wrote e2e_comparison.png")

    # ── 2. Stacked bar: sync gap vs network vs processing ────────────────────
    fig, ax = plt.subplots(figsize=(10, 6))
    syncs = [data[l]["sync_mean"]  for l in labels]
    nets  = [data[l]["net_mean"]   for l in labels]
    procs = [data[l]["proc_mean"]  for l in labels]

    ax.bar(x, syncs, label="Sync gap (stream misalignment)", color=COLORS["Sync gap"])
    ax.bar(x, nets,  bottom=syncs, label="Network (both streams)",  color=COLORS["Network"])
    ax.bar(x, procs, bottom=[s+n for s,n in zip(syncs,nets)],
           label="Processing (workers + fusion)", color=COLORS["Processing"])

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylabel("Mean latency (ms)", fontsize=12)
    ax.set_title("Latency Component Breakdown by Run", fontsize=13, fontweight="bold")
    ax.legend(fontsize=10, loc="upper left")
    ax.yaxis.grid(True, alpha=0.4)
    ax.set_axisbelow(True)
    for i, (s, n, p) in enumerate(zip(syncs, nets, procs)):
        total = s + n + p
        ax.text(i, total + 5, f"{total:.0f}ms", ha="center", fontsize=9, fontweight="bold")
    plt.tight_layout()
    plt.savefig(os.path.join(args.outdir, "latency_breakdown_stacked.png"), dpi=200, bbox_inches="tight")
    plt.close()
    print("Wrote latency_breakdown_stacked.png")

    # ── 3. Network delay: thermal vs imagery per run ─────────────────────────
    fig, ax = plt.subplots(figsize=(10, 5))
    t_nets = [data[l]["t_net_mean"] for l in labels]
    i_nets = [data[l]["i_net_mean"] for l in labels]
    ax.bar(x - 0.2, t_nets, 0.4, label="Thermal network delay", color="#4c72b0")
    ax.bar(x + 0.2, i_nets, 0.4, label="Imagery network delay",  color="#55a868")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylabel("Mean network delay (ms)", fontsize=12)
    ax.set_title("Per-Stream Network Delay Across Runs", fontsize=13, fontweight="bold")
    ax.legend(fontsize=11)
    ax.yaxis.grid(True, alpha=0.4)
    ax.set_axisbelow(True)
    for i, (t, im) in enumerate(zip(t_nets, i_nets)):
        ax.text(i - 0.2, t + 1, f"{t:.1f}", ha="center", fontsize=9)
        ax.text(i + 0.2, im + 1, f"{im:.1f}", ha="center", fontsize=9)
    plt.tight_layout()
    plt.savefig(os.path.join(args.outdir, "network_delay_per_stream.png"), dpi=200, bbox_inches="tight")
    plt.close()
    print("Wrote network_delay_per_stream.png")

    # ── 4. Fire detection rate: raw vs rolling decision ──────────────────────
    fig, ax = plt.subplots(figsize=(10, 5))
    raws = [data[l]["raw_pct"] for l in labels]
    decs = [data[l]["dec_pct"] for l in labels]
    ax.bar(x - 0.2, raws, 0.4, label="Raw signal rate (%)", color="#dd8452", alpha=0.9)
    ax.bar(x + 0.2, decs, 0.4, label="Rolling decision rate (%)", color="#c44e52")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylabel("Rate (%)", fontsize=12)
    ax.set_title("Fire Detection Rate: Raw Signal vs Rolling Window Decision", fontsize=13, fontweight="bold")
    ax.legend(fontsize=11)
    ax.yaxis.grid(True, alpha=0.4)
    ax.set_axisbelow(True)
    for i, (r, d) in enumerate(zip(raws, decs)):
        ax.text(i - 0.2, r + 0.2, f"{r:.1f}%", ha="center", fontsize=9)
        ax.text(i + 0.2, d + 0.2, f"{d:.1f}%", ha="center", fontsize=9)
    plt.tight_layout()
    plt.savefig(os.path.join(args.outdir, "detection_rate_comparison.png"), dpi=200, bbox_inches="tight")
    plt.close()
    print("Wrote detection_rate_comparison.png")

    # ── 5. Summary table ─────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(13, 3.2))
    ax.axis("off")

    col_labels = ["Run", "Events", "E2E Mean", "E2E p50", "E2E p95",
                  "Sync Gap", "Net (T)", "Net (I)", "Raw Fire", "Decision"]
    rows_data = []
    for l in labels:
        d = data[l]
        rows_data.append([
            l.replace("\n", " "),
            str(d["n"]),
            f"{d['e2e_mean']:.0f} ms",
            f"{d['e2e_p50']:.0f} ms",
            f"{d['e2e_p95']:.0f} ms",
            f"{d['sync_mean']:.0f} ms",
            f"{d['t_net_mean']:.1f} ms",
            f"{d['i_net_mean']:.1f} ms",
            f"{d['raw_pct']:.1f}%",
            f"{d['dec_pct']:.1f}%",
        ])

    tbl = ax.table(
        cellText=rows_data,
        colLabels=col_labels,
        loc="center",
        cellLoc="center",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9.5)
    tbl.scale(1, 2.0)

    for j in range(len(col_labels)):
        tbl[0, j].set_facecolor("#2c3e50")
        tbl[0, j].set_text_props(color="white", fontweight="bold")

    for i, row in enumerate(rows_data):
        bg = "#f7f7f7" if i % 2 == 0 else "#ffffff"
        for j in range(len(col_labels)):
            tbl[i + 1, j].set_facecolor(bg)

    plt.title("Phase 6 Experiment Summary", fontsize=13, fontweight="bold", pad=12)
    plt.tight_layout()
    plt.savefig(os.path.join(args.outdir, "summary_table.png"), dpi=200, bbox_inches="tight")
    plt.close()
    print("Wrote summary_table.png")

    # ── 6. Sync gap vs total E2E — shows tradeoff clearly ───────────────────
    fig, ax = plt.subplots(figsize=(9, 5))
    e2e_means = [data[l]["e2e_mean"] for l in labels]
    sync_means = [data[l]["sync_mean"] for l in labels]
    net_means = [data[l]["net_mean"] for l in labels]

    ax.plot(range(N), e2e_means,  "o-", color="#c44e52", linewidth=2, markersize=8, label="E2E mean")
    ax.plot(range(N), sync_means, "s--", color="#e05c5c", linewidth=1.5, markersize=7, label="Sync gap")
    ax.plot(range(N), net_means,  "^--", color="#f0a500", linewidth=1.5, markersize=7, label="Network total")

    ax.set_xticks(range(N))
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylabel("Latency (ms)", fontsize=12)
    ax.set_title("E2E vs Sync Gap vs Network Cost Across Stress Conditions", fontsize=12, fontweight="bold")
    ax.legend(fontsize=11)
    ax.yaxis.grid(True, alpha=0.4)
    ax.set_axisbelow(True)
    plt.tight_layout()
    plt.savefig(os.path.join(args.outdir, "e2e_vs_components_line.png"), dpi=200, bbox_inches="tight")
    plt.close()
    print("Wrote e2e_vs_components_line.png")

    print(f"\nAll plots saved to: {os.path.abspath(args.outdir)}/")


if __name__ == "__main__":
    main()
