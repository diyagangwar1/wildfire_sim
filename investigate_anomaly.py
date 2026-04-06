#!/usr/bin/env python3
"""
Inspect the camera-delay sweep for non-monotone E2E (e.g. dip near 50–100 ms).

Reads latency_log.jsonl under each experiment folder and prints mean / p95 E2E per
condition. Hypotheses to discuss: buffering, sync-threshold alignment, tick overlap.

Example:
  python3 investigate_anomaly.py --root results_seed42
  python3 investigate_anomaly.py --root results_seed0 --glob 'cam_delay_*'
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
from typing import List, Optional, Tuple


def _load_e2e(path: str) -> Optional[List[float]]:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    out: List[float] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "e2e_ms" in r:
                out.append(float(r["e2e_ms"]))
    return out if out else None


def _pct(xs: List[float], p: float) -> float:
    s = sorted(xs)
    k = int(round((p / 100.0) * (len(s) - 1)))
    k = max(0, min(k, len(s) - 1))
    return s[k]


def main() -> None:
    ap = argparse.ArgumentParser(description="Summarize E2E by experiment for delay-sweep debugging")
    ap.add_argument("--root", default="results_seed42", help="Results directory containing experiment subfolders")
    ap.add_argument("--glob", default="*", help="Subfolder glob (e.g. cam_delay_*)")
    args = ap.parse_args()

    pattern = os.path.join(args.root, args.glob, "latency_log.jsonl")
    paths = sorted(glob.glob(pattern))
    if not paths:
        print(f"No files matched: {pattern}")
        return

    rows: List[Tuple[str, int, float, float, float]] = []
    for p in paths:
        exp = os.path.basename(os.path.dirname(p))
        e2e = _load_e2e(p)
        if not e2e:
            continue
        rows.append(
            (exp, len(e2e), statistics.mean(e2e), _pct(e2e, 95), _pct(e2e, 50)),
        )

    rows.sort(key=lambda x: x[0])
    print(f"{'Experiment':<28} {'N':>6} {'mean':>10} {'p50':>10} {'p95':>10}")
    for exp, n, m, p95, p50 in rows:
        print(f"{exp:<28} {n:6d} {m:10.1f} {p50:10.1f} {p95:10.1f}")
    print(
        "\nTip: compare cam_delay_50ms vs cam_delay_100ms mean E2E. "
        "If mean drops at higher delay, check sync_gap_ms vs network delay composition "
        "in analyze_latency.py pie chart for those runs."
    )


if __name__ == "__main__":
    main()
