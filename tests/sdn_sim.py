import json
import os
import tempfile
from types import SimpleNamespace

from tests import ryu_shim

ryu_shim.install()

from ryu.lib.packet import arp, ethernet, ether_types, ipv4, packet, udp  # noqa: E402
from ryu.ofproto import ofproto_v1_3, ofproto_v1_3_parser  # noqa: E402

from app import protocol  # noqa: E402
from topology import layout  # noqa: E402

BCAST_MAC = "ff:ff:ff:ff:ff:ff"
MAX_HOPS = 40


class FakeDatapath:
    ofproto = ofproto_v1_3
    ofproto_parser = ofproto_v1_3_parser

    def __init__(self, dpid, net):
        self.id = dpid
        self.net = net

    def send_msg(self, msg):
        msg.xid = 1
        msg.serialize()
        self.net.receive(self, msg)


class Entry:
    def __init__(self, priority, match, outputs, cookie, idle, hard, flags, now):
        self.priority = priority
        self.match = dict(match.items())
        self.outputs = outputs
        self.cookie = cookie
        self.idle = idle
        self.hard = hard
        self.flags = flags
        self.installed = now
        self.last_hit = now


class Host:
    def __init__(self, name):
        self.name = name
        self.ip = layout.HOSTS[name]["ip"]
        self.mac = layout.mac_of(name)
        self.inbox = []

    def adverts(self):
        return [p for p in self.inbox if p["kind"] == "advert"]


class Network:
    def __init__(self, controller):
        self.ctrl = controller
        self.now = 0
        self.dps = {d: FakeDatapath(d, self) for d in layout.SWITCHES}
        self.tables = {d: [] for d in layout.SWITCHES}
        self.hosts = {n: Host(n) for n in layout.HOSTS}
        self.wires = {}
        self.host_at = {}
        self.link_state = {}
        self.deliveries = 0
        for name, h in layout.HOSTS.items():
            self.host_at[(h["switch"], h["port"])] = name
        for a, pa, b, pb in layout.LINKS:
            self.wires[(a, pa)] = (b, pb)
            self.wires[(b, pb)] = (a, pa)

    def boot(self):
        for dpid, dp in self.dps.items():
            self.ctrl.on_features(SimpleNamespace(msg=SimpleNamespace(datapath=dp)))
        for dpid, dp in self.dps.items():
            ports = [SimpleNamespace(port_no=p) for p in sorted(layout.switch_ports(dpid))]
            self.ctrl.on_switch_enter(SimpleNamespace(switch=SimpleNamespace(dp=dp, ports=ports)))
        for a, pa, b, pb in layout.LINKS:
            self.link_up(a, pa, b, pb)

    def link_event(self, a, pa, b, pb):
        return SimpleNamespace(link=SimpleNamespace(
            src=SimpleNamespace(dpid=a, port_no=pa), dst=SimpleNamespace(dpid=b, port_no=pb)))

    def link_up(self, a, pa, b, pb):
        self.wires[(a, pa)] = (b, pb)
        self.wires[(b, pb)] = (a, pa)
        self.ctrl.on_link_add(self.link_event(a, pa, b, pb))
        self.ctrl.on_link_add(self.link_event(b, pb, a, pa))

    def link_down(self, a, pa, b, pb):
        self.wires.pop((a, pa), None)
        self.wires.pop((b, pb), None)
        self.ctrl.on_link_delete(self.link_event(a, pa, b, pb))
        self.ctrl.on_link_delete(self.link_event(b, pb, a, pa))

    def receive(self, dp, msg):
        parser = ofproto_v1_3_parser
        if isinstance(msg, parser.OFPFlowMod):
            self.flow_mod(dp.id, msg)
        elif isinstance(msg, parser.OFPPacketOut):
            for action in msg.actions:
                self.output(dp.id, action.port, msg.in_port, msg.data, 0)

    def flow_mod(self, dpid, msg):
        ofp = ofproto_v1_3
        table = self.tables[dpid]
        if msg.command == ofp.OFPFC_DELETE:
            self.tables[dpid] = [e for e in table
                                 if (e.cookie & msg.cookie_mask) != (msg.cookie & msg.cookie_mask)]
            return
        outputs = []
        for inst in msg.instructions:
            outputs += [a.port for a in inst.actions]
        entry = Entry(msg.priority, msg.match, outputs, msg.cookie, msg.idle_timeout,
                      msg.hard_timeout, msg.flags, self.now)
        table[:] = [e for e in table if not (e.priority == entry.priority and e.match == entry.match)]
        table.append(entry)

    def fields(self, data):
        pkt = packet.Packet(data)
        eth = pkt.get_protocol(ethernet.ethernet)
        f = {"eth_src": eth.src, "eth_dst": eth.dst, "eth_type": eth.ethertype}
        ip = pkt.get_protocol(ipv4.ipv4)
        if ip:
            f["ipv4_src"], f["ipv4_dst"], f["ip_proto"] = ip.src, ip.dst, ip.proto
        dg = pkt.get_protocol(udp.udp)
        if dg:
            f["udp_dst"] = dg.dst_port
        return f

    def lookup(self, dpid, in_port, fields):
        best = None
        for e in self.tables[dpid]:
            if all(({"in_port": in_port, **fields}).get(k) == v for k, v in e.match.items()):
                if best is None or e.priority > best.priority:
                    best = e
        return best

    def process(self, dpid, in_port, data, hops=0):
        assert hops < MAX_HOPS, "forwarding loop detected"
        fields = self.fields(data)
        entry = self.lookup(dpid, in_port, fields)
        if entry is None:
            return
        entry.last_hit = self.now
        for port in entry.outputs:
            self.output(dpid, port, in_port, data, hops)

    def output(self, dpid, port, in_port, data, hops):
        ofp = ofproto_v1_3
        parser = ofproto_v1_3_parser
        if port == ofp.OFPP_CONTROLLER:
            dp = self.dps[dpid]
            msg = parser.OFPPacketIn(dp, match=parser.OFPMatch(in_port=in_port), data=data)
            self.ctrl.on_packet_in(SimpleNamespace(msg=msg))
            return
        if port == ofp.OFPP_IN_PORT:
            port = in_port
        if port == in_port:
            return
        host = self.host_at.get((dpid, port))
        if host:
            self.deliver(host, data)
            return
        peer = self.wires.get((dpid, port))
        if peer:
            self.process(peer[0], peer[1], data, hops + 1)

    def deliver(self, host_name, data):
        self.deliveries += 1
        pkt = packet.Packet(data)
        eth = pkt.get_protocol(ethernet.ethernet)
        record = {"kind": "other", "eth": eth, "pkt": pkt}
        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            record["kind"] = "arp"
            record["arp"] = pkt.get_protocol(arp.arp)
        elif pkt.get_protocol(udp.udp):
            dg = pkt.get_protocol(udp.udp)
            record["payload"] = pkt.protocols[-1]
            record["kind"] = "advert" if dg.dst_port == protocol.ADV_PORT else "udp"
        self.hosts[host_name].inbox.append(record)

    def inject(self, host_name, data):
        h = layout.HOSTS[host_name]
        self.process(h["switch"], h["port"], data)

    def advert(self, host_name, key=protocol.DEFAULT_KEY, claimed_ip=None, interval_load=0):
        host = self.hosts[host_name]
        payload = protocol.make_advert(host_name, "echo", claimed_ip or host.ip,
                                       protocol.SERVICE_PORT, interval_load, 1, key)
        pkt = packet.Packet()
        pkt.add_protocol(ethernet.ethernet(dst=BCAST_MAC, src=host.mac, ethertype=ether_types.ETH_TYPE_IP))
        pkt.add_protocol(ipv4.ipv4(src=host.ip, dst=layout.BROADCAST, proto=17))
        pkt.add_protocol(udp.udp(src_port=40000, dst_port=protocol.ADV_PORT))
        pkt.add_protocol(payload)
        pkt.serialize()
        self.inject(host_name, pkt.data)

    def arp_request(self, src_name, dst_ip):
        src = self.hosts[src_name]
        pkt = packet.Packet()
        pkt.add_protocol(ethernet.ethernet(dst=BCAST_MAC, src=src.mac, ethertype=ether_types.ETH_TYPE_ARP))
        pkt.add_protocol(arp.arp(opcode=arp.ARP_REQUEST, src_mac=src.mac, src_ip=src.ip,
                                 dst_mac="00:00:00:00:00:00", dst_ip=dst_ip))
        pkt.serialize()
        self.inject(src_name, pkt.data)

    def udp(self, src_name, dst_name, payload=b"hello"):
        src, dst = self.hosts[src_name], self.hosts[dst_name]
        pkt = packet.Packet()
        pkt.add_protocol(ethernet.ethernet(dst=dst.mac, src=src.mac, ethertype=ether_types.ETH_TYPE_IP))
        pkt.add_protocol(ipv4.ipv4(src=src.ip, dst=dst.ip, proto=17))
        pkt.add_protocol(udp.udp(src_port=41000, dst_port=protocol.SERVICE_PORT))
        pkt.add_protocol(payload)
        pkt.serialize()
        self.inject(src_name, pkt.data)

    def advance(self, seconds):
        ofp = ofproto_v1_3
        for _ in range(int(seconds)):
            self.now += 1
            expired = []
            for dpid in list(self.tables):
                alive = []
                for e in self.tables[dpid]:
                    idle_out = e.idle and self.now - e.last_hit >= e.idle
                    hard_out = e.hard and self.now - e.installed >= e.hard
                    if idle_out or hard_out:
                        if e.flags & ofp.OFPFF_SEND_FLOW_REM:
                            reason = ofp.OFPRR_IDLE_TIMEOUT if idle_out else ofp.OFPRR_HARD_TIMEOUT
                            expired.append((dpid, e, reason))
                    else:
                        alive.append(e)
                self.tables[dpid] = alive
            for dpid, e, reason in expired:
                self.flow_removed(dpid, e, reason)

    def flow_removed(self, dpid, entry, reason):
        dp = self.dps[dpid]
        msg = SimpleNamespace(datapath=dp, cookie=entry.cookie, reason=reason)
        self.ctrl.on_flow_removed(SimpleNamespace(msg=msg))

    def flows_with_cookie(self, sid):
        return [(d, e) for d, t in self.tables.items() for e in t if e.cookie >> 8 == sid and e.cookie & 0xff]


def make_network():
    from controller.sd_controller import SDController
    os.environ["SD_STATS"] = "0"
    log = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False).name
    os.environ["SD_LOG"] = log
    ctrl = SDController()
    net = Network(ctrl)
    net.boot()
    net.log_path = log
    return net


def events(net, name=None):
    with open(net.log_path) as f:
        records = [json.loads(line) for line in f if line.strip()]
    return [r for r in records if name is None or r["event"] == name]
