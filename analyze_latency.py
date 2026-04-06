"""
Phase 3–5 — Latency Analysis & Plots

Reads latency_log.jsonl produced by controller.py and generates:
- Pie chart of mean contribution (%) — including inter-stream sync gap
- E2E latency time series with component breakdown
- Empirical CDF of E2E latency (publishable)
- Phase 4: Rolling window (raw_signal vs decision, confirmations vs threshold)
- Phase 5: Inter-drone separation vs latency (XY horizontal distance, |dZ|),
  optional XY trajectory overlay (not distance-to-origin)

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
    p.add_argument(
        "--include-detection-metrics",
        action="store_true",
        help="Emit fire hit/miss table (precision/recall); off by default — "
             "simulation ground truth is not a proxy for real detector quality.",
    )
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

    # Labels include absolute ms so numbers can be compared with Orin / Alice's setup
    abs_labels = [f"{l}\n{v:.1f} ms" for l, v in zip(labels, values)]
    plt.figure(figsize=(8, 8))
    plt.pie(values, labels=abs_labels, autopct="%1.1f%%", startangle=140)
    plt.title(
        f"Mean E2E Latency Contribution{label}\n"
        f"(mean E2E = {mean(e2e_ms):.1f} ms  |  total components = {sum(values):.1f} ms)"
    )
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

    # --- 2b. Empirical CDF of E2E latency ---
    if e2e_ms:
        xs = sorted(e2e_ms)
        n_cdf = len(xs)
        ys = [100.0 * (i + 1) / n_cdf for i in range(n_cdf)]
        plt.figure(figsize=(8, 5))
        plt.plot(xs, ys, color="steelblue", linewidth=1.5, drawstyle="steps-post")
        plt.xlabel("E2E latency (ms)")
        plt.ylabel("Empirical CDF (%)")
        plt.title(f"CDF of End-to-End Latency{label}")
        plt.grid(True, alpha=0.3)
        plt.xlim(left=0)
        plt.ylim(0, 100)
        cdf_path = os.path.join(args.outdir, "e2e_latency_cdf.png")
        plt.savefig(cdf_path, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"Wrote {cdf_path}")

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

    # --- 4. Phase 5: Inter-drone geometry (preferred) + XY trajectories ---
    sep_xy = [r.get("separation_xy_m") for r in rows]
    sep_dz = [r.get("separation_dz_m") for r in rows]
    inter_d = [r.get("inter_drone_dist_m") for r in rows]
    th_x = [r.get("thermal_x") for r in rows]
    th_y = [r.get("thermal_y") for r in rows]
    im_x = [r.get("imagery_x") for r in rows]
    im_y = [r.get("imagery_y") for r in rows]
    has_pos = any(v is not None for v in th_x + im_x)

    if has_pos and any(v is not None for v in th_x) and any(v is not None for v in im_x):
        plt.figure(figsize=(7, 7))
        tx_ok, ty_ok = [], []
        for r in rows:
            x, y = r.get("thermal_x"), r.get("thermal_y")
            if x is not None and y is not None:
                tx_ok.append(float(x))
                ty_ok.append(float(y))
        ix_ok, iy_ok = [], []
        for r in rows:
            x, y = r.get("imagery_x"), r.get("imagery_y")
            if x is not None and y is not None:
                ix_ok.append(float(x))
                iy_ok.append(float(y))
        if tx_ok:
            plt.plot(tx_ok, ty_ok, alpha=0.85, label="thermal (XY)", color="#c44e52", linewidth=1.2)
            plt.scatter(tx_ok[0], ty_ok[0], color="#c44e52", s=80, marker="o", zorder=5, label="_start thermal")
        if ix_ok:
            plt.plot(ix_ok, iy_ok, alpha=0.85, label="imagery (XY)", color="#4c72b0", linewidth=1.2)
            plt.scatter(ix_ok[0], iy_ok[0], color="#4c72b0", s=80, marker="s", zorder=5, label="_start imagery")
        plt.xlabel("East / x (m)")
        plt.ylabel("North / y (m)")
        plt.title(f"Drone trajectories (plan view){label}")
        plt.axis("equal")
        plt.legend(loc="upper right")
        plt.grid(True, alpha=0.3)
        tr_path = os.path.join(args.outdir, "phase5_trajectories_xy.png")
        plt.savefig(tr_path, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"Wrote {tr_path}")

        # Altitude (Z) vs fusion index — prof asked for XY and Z as separate views
        th_z = [r.get("thermal_z") for r in rows]
        im_z = [r.get("imagery_z") for r in rows]
        if any(v is not None for v in th_z) or any(v is not None for v in im_z):
            fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
            x_idx = list(range(len(rows)))
            tz = [float(z) if z is not None else float("nan") for z in th_z]
            iz = [float(z) if z is not None else float("nan") for z in im_z]
            axes[0].plot(x_idx, tz, color="#c44e52", linewidth=1.2, label="thermal Z")
            axes[0].set_ylabel("Altitude Z (m)")
            axes[0].set_title(f"Thermal drone altitude vs fusion index{label}")
            axes[0].legend(loc="upper right")
            axes[0].grid(True, alpha=0.3)
            axes[1].plot(x_idx, iz, color="#4c72b0", linewidth=1.2, label="imagery Z")
            axes[1].set_xlabel("Fusion event index")
            axes[1].set_ylabel("Altitude Z (m)")
            axes[1].set_title(f"Imagery drone altitude vs fusion index{label}")
            axes[1].legend(loc="upper right")
            axes[1].grid(True, alpha=0.3)
            plt.tight_layout()
            tz_path = os.path.join(args.outdir, "phase5_trajectories_z.png")
            plt.savefig(tz_path, dpi=200, bbox_inches="tight")
            plt.close()
            print(f"Wrote {tz_path}")

    # E2E vs horizontal separation between drones (not distance to origin)
    valid_sep = [
        (e, sx, dz, d3)
        for e, sx, dz, d3 in zip(e2e_ms, sep_xy, sep_dz, inter_d)
        if sx is not None and dz is not None
    ]
    if valid_sep:
        e_v = [v[0] for v in valid_sep]
        sx_v = [float(v[1]) for v in valid_sep]
        dz_v = [float(v[2]) for v in valid_sep]

        plt.figure(figsize=(8, 5))
        plt.scatter(sx_v, e_v, alpha=0.45, s=22, c="#2ca02c", label="E2E vs horizontal |drone sep| (XY)")
        plt.xlabel("Inter-drone horizontal separation (m)")
        plt.ylabel("E2E latency (ms)")
        plt.title(f"E2E Latency vs Inter-Drone XY Separation{label}")
        plt.legend()
        plt.grid(True, alpha=0.3)
        p5a = os.path.join(args.outdir, "phase5_e2e_vs_separation_xy.png")
        plt.savefig(p5a, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"Wrote {p5a}")

        plt.figure(figsize=(8, 5))
        plt.scatter(dz_v, e_v, alpha=0.45, s=22, c="#9467bd", label="E2E vs |ΔZ|")
        plt.xlabel("|ΔZ| between drones (m)")
        plt.ylabel("E2E latency (ms)")
        plt.title(f"E2E Latency vs Altitude Separation{label}")
        plt.legend()
        plt.grid(True, alpha=0.3)
        p5b = os.path.join(args.outdir, "phase5_e2e_vs_separation_dz.png")
        plt.savefig(p5b, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"Wrote {p5b}")

        plt.figure(figsize=(8, 5))
        d3_v = [float(v[3]) for v in valid_sep if v[3] is not None]
        e3_v = [v[0] for v in valid_sep if v[3] is not None]
        if d3_v:
            plt.scatter(d3_v, e3_v, alpha=0.45, s=22, color="#17becf")
            plt.xlabel("Inter-drone 3D distance (m)")
            plt.ylabel("E2E latency (ms)")
            plt.title(f"E2E Latency vs Inter-Drone 3D Distance{label}")
            plt.grid(True, alpha=0.3)
            p5c = os.path.join(args.outdir, "phase5_e2e_vs_inter_drone_3d.png")
            plt.savefig(p5c, dpi=200, bbox_inches="tight")
            plt.close()
            print(f"Wrote {p5c}")

    # --- 5. Legacy: E2E vs distance-to-origin (optional, for old logs) ---
    t_dist_all = [r.get("thermal_distance_m") for r in rows]
    i_dist_all = [r.get("imagery_distance_m") for r in rows]
    if not valid_sep and (any(t_dist_all) or any(i_dist_all)):
        valid = [
            (e, td, id_)
            for e, td, id_ in zip(e2e_ms, t_dist_all, i_dist_all)
            if td is not None or id_ is not None
        ]
        if valid:
            e_v = [v[0] for v in valid]
            td_v = [v[1] if v[1] is not None else 0 for v in valid]
            id_v = [v[2] if v[2] is not None else 0 for v in valid]
            plt.figure(figsize=(8, 5))
            plt.scatter(td_v, e_v, alpha=0.5, label="thermal (distance to origin)", s=20)
            plt.scatter(id_v, e_v, alpha=0.5, label="imagery (distance to origin)", s=20)
            plt.xlabel("Distance to reference (m) — legacy")
            plt.ylabel("E2E latency (ms)")
            plt.title(f"E2E vs distance to origin (legacy log){label}")
            plt.legend()
            sc_path = os.path.join(args.outdir, "phase5_e2e_vs_distance_legacy.png")
            plt.savefig(sc_path, dpi=200, bbox_inches="tight")
            plt.close()
            print(f"Wrote {sc_path}")

    # --- 6. Fire window hit / miss table (optional; not recommended for publication) ---
    # Only generated when fire_window ground-truth is present in the log.
    if args.include_detection_metrics and rows and "fire_window" in rows[0]:
        fw   = [bool(r.get("fire_window", False)) for r in rows]
        dec  = [bool(r.get("decision", False))    for r in rows]

        TP = sum(1 for f, d in zip(fw, dec) if     f and     d)
        FN = sum(1 for f, d in zip(fw, dec) if     f and not d)
        FP = sum(1 for f, d in zip(fw, dec) if not f and     d)
        TN = sum(1 for f, d in zip(fw, dec) if not f and not d)

        total_fire    = TP + FN
        total_no_fire = TN + FP
        recall        = TP / total_fire    if total_fire    > 0 else float("nan")
        precision     = TP / (TP + FP)    if (TP + FP)     > 0 else float("nan")
        fpr           = FP / total_no_fire if total_no_fire > 0 else float("nan")
        miss_rate     = FN / total_fire    if total_fire    > 0 else float("nan")

        print(f"\nFire Window Hit/Miss{label}:")
        print(f"  Fire events (TP+FN)  : {total_fire}")
        print(f"  Hits (TP)            : {TP}   ({100*recall:.1f}% recall / hit-rate)")
        print(f"  Misses (FN)          : {FN}   ({100*miss_rate:.1f}% miss-rate)")
        print(f"  False alarms (FP)    : {FP}   ({100*fpr:.1f}% false-alarm rate)")
        print(f"  True quiet (TN)      : {TN}")
        print(f"  Precision            : {100*precision:.1f}%")

        # Save as a PNG table
        fig, ax = plt.subplots(figsize=(9, 4))
        ax.axis("off")

        col_labels = ["Metric", "Count / Rate", "Description"]
        table_data = [
            ["Fire window events (TP+FN)",
             str(total_fire),
             "Fusion events where ground-truth fire was active"],
            ["Hits — True Positive (TP)",
             f"{TP}  ({100*recall:.1f}% recall)",
             "Fire window active AND decision = True"],
            ["Misses — False Negative (FN)",
             f"{FN}  ({100*miss_rate:.1f}% miss rate)",
             "Fire window active AND decision = False"],
            ["False Alarms — False Positive (FP)",
             f"{FP}  ({100*fpr:.1f}% false-alarm rate)",
             "No fire window AND decision = True"],
            ["True Quiet — True Negative (TN)",
             str(TN),
             "No fire window AND decision = False"],
            ["Precision",
             f"{100*precision:.1f}%",
             "Of all detections, fraction that were real fire"],
        ]

        tbl = ax.table(
            cellText=table_data,
            colLabels=col_labels,
            loc="center",
            cellLoc="left",
        )
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(10)
        tbl.scale(1, 2.0)
        tbl.auto_set_column_width([0, 1, 2])

        # Header row
        for j in range(3):
            tbl[0, j].set_facecolor("#2c3e50")
            tbl[0, j].set_text_props(color="white", fontweight="bold")
        # Colour-code rows by outcome
        row_colors = ["#d5f5e3", "#d5f5e3", "#fadbd8", "#fff3cd", "#eaf2ff", "#f8f9fa"]
        for i, color in enumerate(row_colors):
            for j in range(3):
                tbl[i + 1, j].set_facecolor(color)

        plt.title(
            f"Fire Detection — Hit / Miss / False Alarm{label}",
            fontsize=12, fontweight="bold", pad=12,
        )
        plt.tight_layout()
        hm_path = os.path.join(args.outdir, "fire_hit_miss_table.png")
        plt.savefig(hm_path, dpi=200, bbox_inches="tight")
        plt.close()
        print(f"Wrote {hm_path}")


if __name__ == "__main__":
    main()
