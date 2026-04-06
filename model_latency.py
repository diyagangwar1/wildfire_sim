#!/usr/bin/env python3
"""
Lightweight latency surrogate: fit E2E ~ f(camera_delay_ms, thermal_delay_ms, separation_xy_m, ...)

Uses per-fusion rows from latency_log.jsonl (Monte Carlo outputs). For a quick baseline
without sklearn, fits polynomial or linear least squares via numpy.

Example:
  python3 model_latency.py results_seed42/baseline/latency_log.jsonl
  python3 model_latency.py results_seed42/cam_delay_100ms/latency_log.jsonl --degree 2
"""

from __future__ import annotations

import argparse
import json
import math
import os
from typing import Any, Dict, List, Tuple

import numpy as np


def load_rows(path: str) -> List[Dict[str, Any]]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build_design(
    rows: List[Dict[str, Any]],
    cam_delay_ms: float,
    th_delay_ms: float,
    degree: int,
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """Target y = e2e_ms; features from log + known link delays."""
    y_list: List[float] = []
    rows_ok: List[Dict[str, Any]] = []
    for r in rows:
        if "e2e_ms" not in r:
            continue
        y_list.append(float(r["e2e_ms"]))
        rows_ok.append(r)
    y = np.array(y_list, dtype=float)
    n = len(y)
    if n < 10:
        raise SystemExit("Not enough fusion rows for regression.")

    sep_xy = np.array(
        [float(r["separation_xy_m"]) if r.get("separation_xy_m") is not None else math.nan for r in rows_ok],
        dtype=float,
    )
    sep_dz = np.array(
        [float(r["separation_dz_m"]) if r.get("separation_dz_m") is not None else math.nan for r in rows_ok],
        dtype=float,
    )
    # Impute missing geometry with median
    for arr in (sep_xy, sep_dz):
        med = np.nanmedian(arr)
        arr[np.isnan(arr)] = med if np.isfinite(med) else 0.0

    cd = np.full(n, cam_delay_ms)
    td = np.full(n, th_delay_ms)

    names: List[str]
    if degree == 1:
        X = np.column_stack([np.ones(n), cd, td, sep_xy, sep_dz])
        names = ["bias", "cam_delay", "thermal_delay", "sep_xy", "sep_dz"]
    else:
        X = np.column_stack([
            np.ones(n), cd, td, sep_xy, sep_dz,
            cd ** 2, td ** 2, sep_xy ** 2, cd * td,
        ])
        names = [
            "bias", "cam_d", "th_d", "sep_xy", "sep_dz",
            "cam_d^2", "th_d^2", "sep_xy^2", "cam_d*th_d",
        ]

    return X, y, names


def fit_ols(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """beta = (X'X)^{-1} X'y"""
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def main() -> None:
    ap = argparse.ArgumentParser(description="Fit latency = f(delays, separation) from latency_log.jsonl")
    ap.add_argument("jsonl", help="Path to latency_log.jsonl")
    ap.add_argument("--camera-delay-ms", type=float, default=0.0, help="Experiment camera link delay (ms)")
    ap.add_argument("--thermal-delay-ms", type=float, default=0.0, help="Experiment thermal link delay (ms)")
    ap.add_argument("--degree", type=int, choices=(1, 2), default=2, help="Feature degree (default: 2)")
    args = ap.parse_args()

    if not os.path.exists(args.jsonl):
        raise SystemExit(f"Missing file: {args.jsonl}")

    rows = load_rows(args.jsonl)
    X, y, names = build_design(rows, args.camera_delay_ms, args.thermal_delay_ms, args.degree)
    beta = fit_ols(X, y)
    pred = X @ beta
    rmse = float(np.sqrt(np.mean((pred - y) ** 2)))
    r2 = float(1.0 - np.sum((y - pred) ** 2) / np.sum((y - np.mean(y)) ** 2))

    print(f"File: {args.jsonl}")
    print(f"N = {len(y)}  |  RMSE = {rmse:.2f} ms  |  R² = {r2:.4f}")
    print("Coefficients:")
    for name, b in zip(names, beta):
        print(f"  {name:14s}  {b:12.4f}")
    print(
        "\nInterpretation: delays are experiment-level constants here; geometry varies per fusion. "
        "For a full surrogate across experiments, stack multiple JSONL files or add delay columns in the log."
    )


if __name__ == "__main__":
    main()
