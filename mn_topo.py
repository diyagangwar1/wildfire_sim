"""
Mininet Topology for Wildfire Multi-Drone Simulation (Phases 1–7)

Phases supported by topology:
- Phase 2 (Robustness): supports loss
- Phase 3 (Timing/Latency): supports asymmetric delay per link (thermal vs imagery)
- Phase 6 (GPS sync): passes --sync-threshold-ms to controller
- Phase 7 (Clock sync): passes per-worker clock offset and jitter independently

Topology:
  h1 (controller) --- s1 --- h2 (thermal)
                   |
                   +--- h3 (imagery)

We make thermal and imagery links configurable independently:
- thermal-delay / thermal-loss applies to link (s1 <-> h2)
- imagery-delay / imagery-loss applies to link (s1 <-> h3)

Each worker also receives its own clock error parameters (--clock-offset-ms and
--clock-jitter-ms), allowing the thermal and imagery clocks to be miscalibrated
independently — matching real-world GPS receiver heterogeneity.

Interactive mode (default):
  sudo python3 mn_topo.py --thermal-delay 20 --imagery-delay 80

Auto mode (used by run_experiments.py):
  sudo python3 mn_topo.py --thermal-delay 20 --imagery-delay 80 \\
      --auto --outdir results/baseline --seed 42 --duration 60
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

from mininet.net import Mininet
from mininet.node import OVSBridge
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--thermal-delay", type=float, default=0,
                   help="Delay (ms) on thermal link (s1<->h2).")
    p.add_argument("--imagery-delay", type=float, default=0,
                   help="Delay (ms) on imagery link (s1<->h3).")
    p.add_argument("--thermal-loss", type=float, default=0.0,
                   help="Loss (%%) on thermal link (s1<->h2)")
    p.add_argument("--imagery-loss", type=float, default=0.0,
                   help="Loss (%%) on imagery link (s1<->h3)")
    p.add_argument("--bw", type=float, default=1.0,
                   help="Bandwidth (Mbps) for all links")

    # Auto mode (used by run_experiments.py — no interactive CLI)
    p.add_argument("--auto", action="store_true",
                   help="Non-interactive: start processes, run duration, stop, exit")
    p.add_argument("--outdir", default=None,
                   help="Output directory for logs/results (auto mode)")
    p.add_argument("--seed", type=int, default=None,
                   help="Random seed for workers")
    p.add_argument("--duration", type=int, default=60,
                   help="Seconds to run in auto mode")
    p.add_argument("--sync-threshold-ms", type=float, default=2000.0,
                   help="GPS pair-matching window passed to controller (ms); default 2000 (2 s)")

    # Shared worker drop settings
    p.add_argument("--fire-seed", type=int, default=0,
                   help="Seed for the fire propagation model (passed to both workers). Default 0.")
    p.add_argument("--base-drop-prob", type=float, default=0.0,
                   help="Base packet drop probability for both workers (before distance scaling)")
    p.add_argument("--dist-drop-slope", type=float, default=0.0,
                   help=(
                       "Drop probability per metre of 3D distance from controller. "
                       "Default 0.0 (disabled). Use 0.001 to enable distance-based drops."
                   ))

    # Per-worker clock error — thermal drone
    p.add_argument("--thermal-clock-offset-ms", type=float, default=0.0,
                   help="Constant tx_ns offset (ms) for the thermal worker. "
                        "Positive = thermal clock running fast.")
    p.add_argument("--thermal-clock-jitter-ms", type=float, default=0.0,
                   help="Std dev (ms) of per-message Gaussian noise on thermal tx_ns.")

    # Per-worker clock error — imagery drone
    p.add_argument("--imagery-clock-offset-ms", type=float, default=0.0,
                   help="Constant tx_ns offset (ms) for the imagery worker.")
    p.add_argument("--imagery-clock-jitter-ms", type=float, default=0.0,
                   help="Std dev (ms) of per-message Gaussian noise on imagery tx_ns.")

    return p.parse_args()


def _run_auto(net, h1, h2, h3, args: argparse.Namespace) -> None:
    """Non-interactive experiment run: start procs, wait, shut down."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    outdir = os.path.abspath(args.outdir) if args.outdir else script_dir
    os.makedirs(outdir, exist_ok=True)

    ctrl_log = open(os.path.join(outdir, "controller.log"), "w")
    thermal_log = open(os.path.join(outdir, "thermal.log"), "w")
    imagery_log = open(os.path.join(outdir, "imagery.log"), "w")

    ctrl_proc = h1.popen(
        ["python3", os.path.join(script_dir, "controller.py"),
         "--outdir", outdir,
         "--sync-threshold-ms", str(args.sync_threshold_ms)],
        stdout=ctrl_log, stderr=ctrl_log,
        cwd=script_dir,
    )
    time.sleep(2)

    # Thermal worker — gets its own clock error settings
    thermal_extra: list[str] = [
        "--fire-seed",         str(args.fire_seed),
        "--base-drop-prob",    str(args.base_drop_prob),
        "--dist-drop-slope",   str(args.dist_drop_slope),
        "--clock-offset-ms",   str(args.thermal_clock_offset_ms),
        "--clock-jitter-ms",   str(args.thermal_clock_jitter_ms),
    ]
    if args.seed is not None:
        thermal_extra += ["--seed", str(args.seed)]

    # Imagery worker — gets its own (potentially different) clock error settings
    # fire-seed must be the same as thermal so both see identical fire state
    imagery_extra: list[str] = [
        "--fire-seed",         str(args.fire_seed),
        "--base-drop-prob",    str(args.base_drop_prob),
        "--dist-drop-slope",   str(args.dist_drop_slope),
        "--clock-offset-ms",   str(args.imagery_clock_offset_ms),
        "--clock-jitter-ms",   str(args.imagery_clock_jitter_ms),
    ]
    if args.seed is not None:
        imagery_extra += ["--seed", str(args.seed)]

    thermal_proc = h2.popen(
        ["python3", os.path.join(script_dir, "thermal_worker.py"),
         h1.IP()] + thermal_extra,
        stdout=thermal_log, stderr=thermal_log,
        cwd=script_dir,
    )
    imagery_proc = h3.popen(
        ["python3", os.path.join(script_dir, "imagery_worker.py"),
         h1.IP()] + imagery_extra,
        stdout=imagery_log, stderr=imagery_log,
        cwd=script_dir,
    )

    print(f"  [auto] running {args.duration}s — h1={h1.IP()}  "
          f"thermal={h2.IP()}  imagery={h3.IP()}", flush=True)

    for i in range(args.duration):
        time.sleep(1)
        print(".", end="", flush=True)
    print(" done")

    for proc in [thermal_proc, imagery_proc, ctrl_proc]:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    for f in [ctrl_log, thermal_log, imagery_log]:
        f.close()

    latency_log = os.path.join(outdir, "latency_log.jsonl")
    if os.path.exists(latency_log) and os.path.getsize(latency_log) > 0:
        n = open(latency_log).read().strip().count("\n") + 1
        print(f"  [auto] {n} fusion(s) recorded -> {latency_log}")
    else:
        print("  [auto] WARNING: no fusions recorded. Dumping logs for debug:")
        for label, path in [("controller", os.path.join(outdir, "controller.log")),
                             ("thermal",    os.path.join(outdir, "thermal.log")),
                             ("imagery",    os.path.join(outdir, "imagery.log"))]:
            try:
                lines = open(path).readlines()
                tail = lines[-20:] if len(lines) > 20 else lines
                print(f"\n--- {label}.log (last {len(tail)} lines) ---")
                print("".join(tail), end="")
            except Exception:
                pass


def main() -> None:
    args = parse_args()
    setLogLevel("warning")

    net = Mininet(controller=None, switch=OVSBridge, link=TCLink, autoSetMacs=True)

    s1 = net.addSwitch("s1")
    h1 = net.addHost("h1")   # controller
    h2 = net.addHost("h2")   # thermal
    h3 = net.addHost("h3")   # imagery

    net.addLink(h1, s1, bw=args.bw)
    net.addLink(h2, s1, bw=args.bw,
                delay=f"{args.thermal_delay}ms", loss=args.thermal_loss)
    net.addLink(h3, s1, bw=args.bw,
                delay=f"{args.imagery_delay}ms", loss=args.imagery_loss)

    net.start()

    try:
        if args.auto:
            _run_auto(net, h1, h2, h3, args)
        else:
            print("\n[INFO] Hosts:")
            print("  h1 = controller")
            print("  h2 = thermal worker")
            print("  h3 = imagery worker\n")
            print("[INFO] Example commands inside Mininet:")
            print("  h1 python3 controller.py [--outdir results/baseline] [--sync-threshold-ms 5] &")
            print("  h2 python3 thermal_worker.py 10.0.0.1 [--seed 42] &")
            print("  h3 python3 imagery_worker.py 10.0.0.1 [--seed 42] &\n")
            CLI(net)
    finally:
        net.stop()


if __name__ == "__main__":
    main()
