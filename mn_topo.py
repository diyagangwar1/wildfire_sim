"""
Wildfire Multi-Drone Simulation - Mininet Topology

Three hosts: h1 (controller), h2 (thermal worker), h3 (imagery worker).
TCLink allows future delay/loss injection for latency experiments.
"""

from mininet.net import Mininet
from mininet.node import OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI


def run():
    net = Mininet(switch=OVSSwitch, link=TCLink, controller=None)

    h1 = net.addHost("h1")  # Fusion controller
    h2 = net.addHost("h2")  # Thermal worker (drone)
    h3 = net.addHost("h3")  # Imagery worker (drone)
    s1 = net.addSwitch("s1", failMode="standalone")

    net.addLink(h1, s1)
    # TCLink params: delay/loss can be set for asymmetric experiments (Phase 3)
    net.addLink(h2, s1, delay="0ms", loss=0)
    net.addLink(h3, s1, delay="0ms", loss=0)

    net.start()
    CLI(net)
    net.stop()


if __name__ == "__main__":
    run()