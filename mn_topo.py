"""
Mininet Topology for Wildfire Multi-Drone Simulation
Phases implemented:
- Phase 2 (Robustness testing): supports loss
- Phase 3 (Timing/Latency): supports asymmetric delay per link (thermal vs imagery)

Topology:
  h1 (controller) --- s1 --- h2 (thermal)
                   |
                   +--- h3 (imagery)

We make thermal and imagery links configurable independently:
- thermal-delay / thermal-loss applies to link (s1 <-> h2)
- imagery-delay / imagery-loss applies to link (s1 <-> h3)

Example:
  sudo python3 mn_topo.py --thermal-delay 20 --imagery-delay 80 --thermal-loss 0 --imagery-loss 1
"""

from __future__ import annotations
import argparse

from mininet.net import Mininet
from mininet.node import OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--thermal-delay", type=int, default=0, help="Delay (ms) on thermal link")
    p.add_argument("--imagery-delay", type=int, default=0, help="Delay (ms) on imagery link")
    p.add_argument("--thermal-loss", type=float, default=0.0, help="Loss (%) on thermal link")
    p.add_argument("--imagery-loss", type=float, default=0.0, help="Loss (%) on imagery link")
    p.add_argument("--bw", type=float, default=100.0, help="Bandwidth (Mbps) for all links")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    setLogLevel("info")

    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink, autoSetMacs=True)

    c0 = net.addController("c0")
    s1 = net.addSwitch("s1")

    h1 = net.addHost("h1")  # controller
    h2 = net.addHost("h2")  # thermal
    h3 = net.addHost("h3")  # imagery

    # Controller to switch (usually keep clean / no extra delay)
    net.addLink(h1, s1, bw=args.bw)

    # Thermal link: asymmetric from imagery
    net.addLink(
        h2, s1,
        bw=args.bw,
        delay=f"{args.thermal_delay}ms",
        loss=args.thermal_loss,
    )

    # Imagery link
    net.addLink(
        h3, s1,
        bw=args.bw,
        delay=f"{args.imagery_delay}ms",
        loss=args.imagery_loss,
    )

    net.start()

    print("\n[INFO] Hosts:")
    print("  h1 = controller")
    print("  h2 = thermal worker")
    print("  h3 = imagery worker\n")

    print("[INFO] Example commands inside Mininet:")
    print("  h1 python3 controller.py &")
    print("  h2 python3 thermal_worker.py 10.0.0.1 &")
    print("  h3 python3 imagery_worker.py 10.0.0.1 &\n")

    CLI(net)
    net.stop()


if __name__ == "__main__":
    main()
