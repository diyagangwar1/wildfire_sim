"""
Phase 3–5 — Latency Analysis & Plots

Reads latency_log.jsonl produced by controller.py and generates:
- Pie chart of mean contribution (%)
- E2E latency time series
- Phase 4: Rolling window (raw_signal vs decision, confirmations vs threshold)
- Phase 5: Drone distance over time, E2E vs distance scatter

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

    # --- Phase 4: Rolling window (raw_signal vs decision, confirmations) ---
    if "raw_signal" in rows[0] and "decision" in rows[0]:
        raw_signals = [1 if r.get("raw_signal") else 0 for r in rows]
        decisions = [1 if r.get("decision") else 0 for r in rows]
        confirmations = [r.get("window_confirmations", 0) for r in rows]
        fire_confirm_k = rows[0].get("fire_confirm_k", 3)

        fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        x = list(range(len(rows)))

        axes[0].plot(x, raw_signals, label="raw_signal", alpha=0.8, drawstyle="steps-post")
        axes[0].plot(x, decisions, label="decision", alpha=0.8, drawstyle="steps-post")
        axes[0].set_ylabel("Signal (0/1)")
        axes[0].set_title("Phase 4: Raw Signal vs Rolling Decision")
        axes[0].legend(loc="upper right")
        axes[0].set_ylim(-0.1, 1.2)

        axes[1].bar(x, confirmations, alpha=0.7, label="confirmations")
        axes[1].axhline(y=fire_confirm_k, color="r", linestyle="--", label=f"threshold (K={fire_confirm_k})")
        axes[1].set_xlabel("Fusion event index")
        axes[1].set_ylabel("Confirmations in window")
        axes[1].set_title("Confirmations per Event vs Threshold")
        axes[1].legend(loc="upper right")

        plt.tight_layout()
        phase4_path = os.path.join(args.outdir, "phase4_rolling_window.png")
        plt.savefig(phase4_path, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"Wrote {phase4_path}")

    # --- Phase 5: Drone distance over time ---
    thermal_dist = [r.get("thermal_distance_m") for r in rows if r.get("thermal_distance_m") is not None]
    imagery_dist = [r.get("imagery_distance_m") for r in rows if r.get("imagery_distance_m") is not None]
    if thermal_dist or imagery_dist:
        plt.figure(figsize=(10, 4))
        if thermal_dist:
            plt.plot(thermal_dist, label="thermal drone", alpha=0.8)
        if imagery_dist:
            plt.plot(imagery_dist, label="imagery drone", alpha=0.8)
        plt.xlabel("Fusion event index")
        plt.ylabel("Distance (m)")
        plt.title("Phase 5: Drone Distance to Controller Over Time")
        plt.legend()
        phase5_dist_path = os.path.join(args.outdir, "phase5_drone_distance.png")
        plt.savefig(phase5_dist_path, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"Wrote {phase5_dist_path}")

    # --- Phase 5: E2E latency vs distance scatter ---
    thermal_dist_all = [r.get("thermal_distance_m") for r in rows]
    imagery_dist_all = [r.get("imagery_distance_m") for r in rows]
    valid = [
        (e, td, id_)
        for e, td, id_ in zip(e2e_ms, thermal_dist_all, imagery_dist_all)
        if td is not None or id_ is not None
    ]
    if valid:
        e2e_vals = [v[0] for v in valid]
        td_vals = [v[1] if v[1] is not None else 0 for v in valid]
        id_vals = [v[2] if v[2] is not None else 0 for v in valid]
        max_dist = max(td_vals + id_vals) or 1

        plt.figure(figsize=(8, 5))
        plt.scatter(td_vals, e2e_vals, alpha=0.5, label="thermal distance", s=20)
        plt.scatter(id_vals, e2e_vals, alpha=0.5, label="imagery distance", s=20)
        plt.xlabel("Drone distance (m)")
        plt.ylabel("E2E latency (ms)")
        plt.title("Phase 5: E2E Latency vs Drone Distance")
        plt.legend()
        phase5_e2e_path = os.path.join(args.outdir, "phase5_e2e_vs_distance.png")
        plt.savefig(phase5_e2e_path, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"Wrote {phase5_e2e_path}")


if __name__ == "__main__":
    main()
