"""
Phase 3–5 — Latency Analysis & Plots

Reads latency_log.jsonl produced by controller.py and generates:
- Pie chart of mean contribution (%) — including inter-stream sync gap
- E2E latency time series with component breakdown
- Phase 4: Rolling window (raw_signal vs decision, confirmations vs threshold)
- Phase 5: Drone distance over time, E2E vs distance scatter

Key insight: E2E = sync_gap + thermal_net + imagery_net + thermal_proc + imagery_proc + fusion_proc
The sync_gap (time waiting for the slower stream) dominates at low delay.

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
    p.add_argument("--label", default="", help="Optional run label for plot titles")
    return p.parse_args()


def percentile(xs: List[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = int(round((p / 100.0) * (len(xs) - 1)))
    k = max(0, min(k, len(xs) - 1))
    return xs[k]


def mean(xs: List[float]) -> float:
    return sum(xs) / max(1, len(xs))


def main() -> None:
    args = parse_args()
    os.makedirs(args.outdir, exist_ok=True)
    label = f" ({args.label})" if args.label else ""

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

    e2e_ms        = [float(r["e2e_ms"]) for r in rows if "e2e_ms" in r]
    t_proc_ms     = [r.get("thermal_proc_ns", 0) / 1e6 for r in rows]
    i_proc_ms     = [r.get("imagery_proc_ns", 0) / 1e6 for r in rows]
    t_net_ms      = [r.get("thermal_net_ns", 0) / 1e6 for r in rows]
    i_net_ms      = [r.get("imagery_net_ns", 0) / 1e6 for r in rows]
    f_proc_ms     = [r.get("fusion_proc_ns", 0) / 1e6 for r in rows]

    # Inter-stream sync gap: the dominant component not captured by individual measurements.
    # E2E starts from min(thermal_tx, imagery_tx). The gap = E2E minus all measured parts.
    measured_ms   = [tp + ip + tn + in_ + fp
                     for tp, ip, tn, in_, fp
                     in zip(t_proc_ms, i_proc_ms, t_net_ms, i_net_ms, f_proc_ms)]
    sync_gap_ms   = [max(0.0, e - m) for e, m in zip(e2e_ms, measured_ms)]

    raw      = [r.get("raw_signal", False) for r in rows]
    decision = [r.get("decision", False) for r in rows]

    print(f"Summary{label}:")
    print(f"  events         : {len(e2e_ms)}")
    print(f"  e2e mean       : {mean(e2e_ms):.2f} ms")
    print(f"  e2e median     : {percentile(e2e_ms, 50):.2f} ms")
    print(f"  e2e p95        : {percentile(e2e_ms, 95):.2f} ms")
    print(f"  e2e p99        : {percentile(e2e_ms, 99):.2f} ms")
    print(f"  sync_gap mean  : {mean(sync_gap_ms):.2f} ms")
    print(f"  thermal_net    : {mean(t_net_ms):.3f} ms")
    print(f"  imagery_net    : {mean(i_net_ms):.3f} ms")
    print(f"  thermal_proc   : {mean(t_proc_ms):.3f} ms")
    print(f"  imagery_proc   : {mean(i_proc_ms):.3f} ms")
    print(f"  fusion_proc    : {mean(f_proc_ms):.3f} ms")
    print(f"  raw fire rate  : {100*sum(raw)/max(1,len(raw)):.1f}%")
    print(f"  decision rate  : {100*sum(decision)/max(1,len(decision)):.1f}%")

    # --- 1. Pie chart — now includes sync gap ---
    parts = {
        "Sync gap\n(stream misalignment)": mean(sync_gap_ms),
        "Thermal net": mean(t_net_ms),
        "Imagery net": mean(i_net_ms),
        "Thermal proc": mean(t_proc_ms),
        "Imagery proc": mean(i_proc_ms),
        "Fusion proc": mean(f_proc_ms),
    }
    labels = list(parts.keys())
    values = list(parts.values())

    plt.figure(figsize=(7, 7))
    plt.pie(values, labels=labels, autopct="%1.1f%%", startangle=140)
    plt.title(f"Mean E2E Latency Contribution{label}\n(mean={mean(e2e_ms):.0f}ms)")
    pie_path = os.path.join(args.outdir, "latency_contribution_pie.png")
    plt.savefig(pie_path, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"Wrote {pie_path}")

    # --- 2. E2E latency time series with stacked components ---
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)

    x = list(range(len(e2e_ms)))
    axes[0].plot(x, e2e_ms, color="steelblue", alpha=0.8, linewidth=0.8, label="E2E (ms)")
    axes[0].set_ylabel("E2E latency (ms)")
    axes[0].set_title(f"End-to-End Latency Over Time{label}")
    axes[0].legend()

    # Stacked area: sync gap vs network vs proc
    axes[1].stackplot(
        x,
        sync_gap_ms,
        [tn + in_ for tn, in_ in zip(t_net_ms, i_net_ms)],
        [tp + ip + fp for tp, ip, fp in zip(t_proc_ms, i_proc_ms, f_proc_ms)],
        labels=["Sync gap", "Network (both streams)", "Processing (both + fusion)"],
        alpha=0.7,
    )
    axes[1].set_xlabel("Fusion event index")
    axes[1].set_ylabel("Latency (ms)")
    axes[1].set_title("Latency Component Breakdown (stacked)")
    axes[1].legend(loc="upper right")

    plt.tight_layout()
    ts_path = os.path.join(args.outdir, "e2e_latency_timeseries.png")
    plt.savefig(ts_path, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"Wrote {ts_path}")

    # --- 3. Phase 4: Rolling window ---
    if rows and "raw_signal" in rows[0]:
        raw_int   = [1 if r.get("raw_signal") else 0 for r in rows]
        dec_int   = [1 if r.get("decision") else 0 for r in rows]
        confirms  = [r.get("window_confirmations", 0) for r in rows]
        confirm_k = rows[0].get("fire_confirm_k", 3)

        fig, axes = plt.subplots(2, 1, figsize=(12, 6), sharex=True)
        axes[0].plot(x, raw_int,  label="raw_signal", alpha=0.8, drawstyle="steps-post")
        axes[0].plot(x, dec_int,  label="decision",   alpha=0.8, drawstyle="steps-post")
        axes[0].set_ylabel("Signal (0/1)")
        axes[0].set_title(f"Phase 4: Raw Signal vs Rolling Window Decision{label}")
        axes[0].legend(loc="upper right")
        axes[0].set_ylim(-0.1, 1.2)

        axes[1].bar(x, confirms, alpha=0.7, label="confirmations in window")
        axes[1].axhline(y=confirm_k, color="r", linestyle="--", label=f"threshold={confirm_k}")
        axes[1].set_xlabel("Fusion event index")
        axes[1].set_ylabel("Confirmations")
        axes[1].legend(loc="upper right")

        plt.tight_layout()
        phase4_path = os.path.join(args.outdir, "phase4_rolling_window.png")
        plt.savefig(phase4_path, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"Wrote {phase4_path}")

    # --- 4. Phase 5: Drone distance over time ---
    t_dist_all = [r.get("thermal_distance_m") for r in rows]
    i_dist_all = [r.get("imagery_distance_m") for r in rows]
    t_dist = [d for d in t_dist_all if d is not None]
    i_dist = [d for d in i_dist_all if d is not None]

    if t_dist or i_dist:
        plt.figure(figsize=(10, 4))
        if t_dist:
            plt.plot(t_dist, label="thermal drone", alpha=0.8)
        if i_dist:
            plt.plot(i_dist, label="imagery drone", alpha=0.8)
        plt.xlabel("Fusion event index")
        plt.ylabel("Distance to controller (m)")
        plt.title(f"Phase 5: Drone Distance Over Time{label}")
        plt.legend()
        d_path = os.path.join(args.outdir, "phase5_drone_distance.png")
        plt.savefig(d_path, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"Wrote {d_path}")

    # --- 5. Phase 5: E2E vs distance scatter ---
    valid = [
        (e, td, id_)
        for e, td, id_ in zip(e2e_ms, t_dist_all, i_dist_all)
        if td is not None or id_ is not None
    ]
    if valid:
        e_v  = [v[0] for v in valid]
        td_v = [v[1] if v[1] is not None else 0 for v in valid]
        id_v = [v[2] if v[2] is not None else 0 for v in valid]

        plt.figure(figsize=(8, 5))
        plt.scatter(td_v, e_v, alpha=0.5, label="thermal drone", s=20)
        plt.scatter(id_v, e_v, alpha=0.5, label="imagery drone",  s=20)
        plt.xlabel("Drone distance (m)")
        plt.ylabel("E2E latency (ms)")
        plt.title(f"Phase 5: E2E Latency vs Drone Distance{label}")
        plt.legend()
        sc_path = os.path.join(args.outdir, "phase5_e2e_vs_distance.png")
        plt.savefig(sc_path, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"Wrote {sc_path}")


if __name__ == "__main__":
    main()
