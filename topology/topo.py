import argparse
import sys

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSKernelSwitch, RemoteController

from topology import layout


def build_network(loss=None, bw=None, delay=None, controller_ip="127.0.0.1",
                  controller_port=6653, static_arp=False):
    net = Mininet(controller=None, switch=OVSKernelSwitch, link=TCLink,
                  autoSetMacs=False, autoStaticArp=False, build=False)
    net.addController("c0", controller=RemoteController, ip=controller_ip, port=controller_port)
    switches = {}
    for dpid in layout.SWITCHES:
        switches[dpid] = net.addSwitch("s%d" % dpid, dpid="%016x" % dpid,
                                       protocols="OpenFlow13", failMode="secure")
    hosts = {}
    for name, h in layout.HOSTS.items():
        hosts[name] = net.addHost(name, ip="%s/24" % h["ip"], mac=layout.mac_of(name))
    for name, h in layout.HOSTS.items():
        net.addLink(hosts[name], switches[h["switch"]], port2=h["port"])
    for a, pa, b, pb in layout.LINKS:
        net.addLink(switches[a], switches[b], port1=pa, port2=pb,
                    loss=loss or None, bw=bw, delay=delay)
    net.build()
    net.start()
    if static_arp:
        net.staticArp()
    return net


def main(argv=None):
    p = argparse.ArgumentParser(description="Diamond Mininet topology for the service discovery project")
    p.add_argument("--mode", choices=["dynamic", "static"], default="dynamic")
    p.add_argument("--loss", type=float, default=0.0)
    p.add_argument("--port", type=int, default=6653)
    args = p.parse_args(argv)
    setLogLevel("info")
    net = build_network(loss=args.loss, controller_port=args.port,
                        static_arp=args.mode == "static")
    try:
        CLI(net)
    finally:
        net.stop()


if __name__ == "__main__":
    sys.exit(main())
