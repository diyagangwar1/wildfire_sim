"""
Phase 3 — Latency Analysis & Plots

Reads latency_log.jsonl produced by controller.py and generates:
- Pie chart of mean contribution (%)
- Latency vs delay (using recorded e2e_ms and parsing args is up to you)
- Basic distribution summaries (mean/median/p95)

This is designed to be a starting point you can extend for:
- latency vs send rate
- latency vs loss
- repeated experiments + labeling runs

Usage:
  python3 analyze_latency.py latency_log.jsonl --outdir plots
"""

from __future__ import annotations
import argparse
import json
import os
from typing import Dict, Any, List

import matplotlib.pyplot as plt


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("jsonl", help="Path to latency_log.jsonl")
    p.add_argument("--outdir", default="plots", help="Output directory for plots")
    return p.parse_args()


def percentile(xs: List[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = int(round((p / 100.0) * (len(xs) - 1)))
    k = max(0, min(k, len(xs) - 1))
    return xs[k]


def main() -> None:
    args = parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    with open(args.jsonl, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))

    if not rows:
        print("No rows found.")
        return

    e2e_ms = [float(r["e2e_ms"]) for r in rows if "e2e_ms" in r]

    # Components (ns -> ms)
    t_proc_ms = [r.get("thermal_proc_ns", 0) / 1e6 for r in rows]
    i_proc_ms = [r.get("imagery_proc_ns", 0) / 1e6 for r in rows]
    t_net_ms = [r.get("thermal_net_ns", 0) / 1e6 for r in rows]
    i_net_ms = [r.get("imagery_net_ns", 0) / 1e6 for r in rows]
    f_proc_ms = [r.get("fusion_proc_ns", 0) / 1e6 for r in rows]

    def mean(xs: List[float]) -> float:
        return sum(xs) / max(1, len(xs))

    summary = {
        "count": len(e2e_ms),
        "e2e_mean_ms": mean(e2e_ms),
        "e2e_median_ms": percentile(e2e_ms, 50),
        "e2e_p95_ms": percentile(e2e_ms, 95),
        "e2e_p99_ms": percentile(e2e_ms, 99),
    }

    print("Summary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    # --- Pie chart of mean contributions ---
    parts = {
        "Thermal proc": mean(t_proc_ms),
        "Thermal net": mean(t_net_ms),
        "Imagery proc": mean(i_proc_ms),
        "Imagery net": mean(i_net_ms),
        "Fusion proc": mean(f_proc_ms),
    }
    labels = list(parts.keys())
    values = list(parts.values())

    plt.figure()
    plt.pie(values, labels=labels, autopct="%1.1f%%")
    plt.title("Mean Latency Contribution (%)")
    pie_path = os.path.join(args.outdir, "latency_contribution_pie.png")
    plt.savefig(pie_path, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"Wrote {pie_path}")

    # --- E2E latency time series (quick sanity plot) ---
    plt.figure()
    plt.plot(e2e_ms)
    plt.title("End-to-End Latency (ms) Over Time")
    plt.xlabel("Fusion event index")
    plt.ylabel("E2E latency (ms)")
    ts_path = os.path.join(args.outdir, "e2e_latency_timeseries.png")
    plt.savefig(ts_path, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"Wrote {ts_path}")


if __name__ == "__main__":
    main()
