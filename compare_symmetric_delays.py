#!/usr/bin/env python3
"""
Compare camera-delay vs thermal-delay runs at the *same* link delay (e.g. 100 ms).

Your prof noted cam_100ms vs thermal_100ms can look similar in some plots because
sync / pairing behavior depends on which stream is late — this script prints side‑by‑side
mean E2E, p95 E2E, and mean estimated sync gap (same definition as analyze_latency.py).

Example:
  python3 compare_symmetric_delays.py --root results_seed42 --delay-ms 100
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Tuple


def _load(path: str) -> List[Dict[str, Any]]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def _pctl(xs: List[float], p: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    k = int(round((p / 100.0) * (len(s) - 1)))
    k = max(0, min(k, len(s) - 1))
    return s[k]


def sync_gap_stats(rows: List[Dict[str, Any]]) -> Tuple[float, float]:
    """Mean sync gap (ms) per row — matches analyze_latency pie chart logic."""
    gaps: List[float] = []
    for r in rows:
        if "e2e_ms" not in r:
            continue
        e2e = float(r["e2e_ms"])
        tp = r.get("thermal_proc_ns", 0) / 1e6
        ip = r.get("imagery_proc_ns", 0) / 1e6
        tn = r.get("thermal_net_ns", 0) / 1e6
        inn = r.get("imagery_net_ns", 0) / 1e6
        fp = r.get("fusion_proc_ns", 0) / 1e6
        measured = tp + ip + tn + inn + fp
        gaps.append(max(0.0, e2e - measured))
    return _mean(gaps), _pctl(gaps, 95)


def summarize(path: str) -> None:
    if not os.path.exists(path):
        print(f"  (missing) {path}")
        return
    rows = _load(path)
    if not rows:
        print(f"  (empty) {path}")
        return
    e2e = [float(r["e2e_ms"]) for r in rows if "e2e_ms" in r]
    sg_mean, sg_p95 = sync_gap_stats(rows)
    print(
        f"  N={len(e2e):5d}  mean E2E={_mean(e2e):8.1f} ms  p95={_pctl(e2e, 95):8.1f} ms  "
        f"sync_gap mean={sg_mean:8.1f} ms  sync_gap p95={sg_p95:8.1f} ms"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare cam vs thermal delay at equal ms")
    ap.add_argument("--root", default="results_seed42", help="Folder containing experiment subdirs")
    ap.add_argument("--delay-ms", type=int, default=100, help="Delay value to compare (default 100)")
    args = ap.parse_args()

    d = args.delay_ms
    cam_name = f"cam_delay_{d}ms"
    th_name = f"thermal_delay_{d}ms"
    cam_p = os.path.join(args.root, cam_name, "latency_log.jsonl")
    th_p = os.path.join(args.root, th_name, "latency_log.jsonl")

    print(f"Symmetric {d} ms delay — root={args.root}\n")
    print(f"--- {cam_name} (thermal=0, imagery={d}) ---")
    summarize(cam_p)
    print(f"--- {th_name} (thermal={d}, imagery=0) ---")
    summarize(th_p)
    print(
        "\nIf mean E2E is similar, the dominant term may be sync/stream alignment rather than "
        "which link is delayed — see per-run latency_contribution_pie.png."
    )


if __name__ == "__main__":
    main()
