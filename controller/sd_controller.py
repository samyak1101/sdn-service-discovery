import json
import os
import sys
import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.lib.packet import arp, ethernet, ether_types, in_proto, ipv4, packet, udp
from ryu.ofproto import ofproto_v1_3
from ryu.topology import event

from app import protocol
from controller.graph import Topology

PRIORITY_MISS = 0
PRIORITY_DATA = 100
PRIORITY_ADV = 200
PRIORITY_BLOCK = 300

KIND_ADV = 1
KIND_DATA = 2
KIND_BLOCK = 3
SERVER_MASK = 0xFFFFFFFFFFFFFF00

BROADCAST_MAC = "ff:ff:ff:ff:ff:ff"


def make_cookie(sid, kind):
    return (sid << 8) | kind


class SDController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(SDController, self).__init__(*args, **kwargs)
        self.key = os.environ.get("SD_KEY", protocol.DEFAULT_KEY)
        self.adv_idle = int(os.environ.get("SD_ADV_IDLE", "6"))
        self.data_idle = int(os.environ.get("SD_DATA_IDLE", "30"))
        self.block_seconds = int(os.environ.get("SD_BLOCK_SECONDS", "30"))
        self.log_path = os.environ.get("SD_LOG")
        self.adv_port = int(os.environ.get("SD_ADV_PORT", str(protocol.ADV_PORT)))
        self.topo = Topology()
        self.dps = {}
        self.hosts = {}
        self.ip_to_mac = {}
        self.servers = {}
        self.mac_to_sid = {}
        self.sessions = {}
        self.next_sid = 1
        self.counters = {"packet_in": 0, "packet_in_advert": 0, "packet_in_arp": 0,
                         "packet_in_data": 0, "flow_mods": 0, "adverts_dropped": 0}
        self.log_file = open(self.log_path, "a", buffering=1) if self.log_path else None
        if os.environ.get("SD_STATS", "1") == "1":
            hub.spawn(self.stats_loop)

    def log(self, kind, **fields):
        record = {"t": round(time.time(), 4), "event": kind}
        record.update(fields)
        line = json.dumps(record)
        if self.log_file:
            self.log_file.write(line + "\n")
        else:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    def stats_loop(self):
        while True:
            hub.sleep(1)
            self.log("stats", servers=len(self.servers), **self.counters)

    def send(self, dp, msg):
        self.counters["flow_mods"] += isinstance(msg, dp.ofproto_parser.OFPFlowMod)
        dp.send_msg(msg)

    def add_flow(self, dp, priority, match, actions, cookie, idle=0, hard=0, flags=0):
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        instructions = []
        if actions:
            instructions = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        self.send(dp, parser.OFPFlowMod(
            datapath=dp, cookie=cookie, cookie_mask=0, table_id=0, command=ofp.OFPFC_ADD,
            idle_timeout=idle, hard_timeout=hard, priority=priority,
            buffer_id=ofp.OFP_NO_BUFFER, out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
            flags=flags, match=match, instructions=instructions))

    def delete_flows(self, dp, cookie, mask):
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        self.send(dp, parser.OFPFlowMod(
            datapath=dp, cookie=cookie, cookie_mask=mask, table_id=ofp.OFPTT_ALL,
            command=ofp.OFPFC_DELETE, out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
            match=parser.OFPMatch()))

    def packet_out(self, dp, in_port, out_ports, data):
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        actions = [parser.OFPActionOutput(p) for p in out_ports]
        self.send(dp, parser.OFPPacketOut(
            datapath=dp, buffer_id=ofp.OFP_NO_BUFFER, in_port=in_port,
            actions=actions, data=data))

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def on_features(self, ev):
        dp = ev.msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        self.dps[dp.id] = dp
        self.delete_flows(dp, 0, 0)
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self.add_flow(dp, PRIORITY_MISS, match, actions, 0)

    @set_ev_cls(event.EventSwitchEnter)
    def on_switch_enter(self, ev):
        switch = ev.switch
        self.dps[switch.dp.id] = switch.dp
        self.topo.add_switch(switch.dp.id, [p.port_no for p in switch.ports])
        self.log("switch_up", dpid=switch.dp.id)

    @set_ev_cls(event.EventSwitchLeave)
    def on_switch_leave(self, ev):
        dpid = ev.switch.dp.id
        self.topo.remove_switch(dpid)
        self.dps.pop(dpid, None)
        for sid in [s for s, srv in self.servers.items() if srv["dpid"] == dpid]:
            self.deregister(sid, "switch_left")
        self.log("switch_down", dpid=dpid)
        self.rebuild_all("switch_left")

    @set_ev_cls(event.EventLinkAdd)
    def on_link_add(self, ev):
        link = ev.link
        self.topo.add_link(link.src.dpid, link.src.port_no, link.dst.dpid, link.dst.port_no)
        self.log("link_up", src=link.src.dpid, dst=link.dst.dpid)
        self.rebuild_all("link_up")

    @set_ev_cls(event.EventLinkDelete)
    def on_link_delete(self, ev):
        link = ev.link
        if self.topo.remove_link(link.src.dpid, link.src.port_no):
            self.log("link_down", src=link.src.dpid, dst=link.dst.dpid)
            self.rebuild_all("link_down")

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def on_packet_in(self, ev):
        msg = ev.msg
        dp = msg.datapath
        in_port = msg.match["in_port"]
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        self.counters["packet_in"] += 1
        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            self.counters["packet_in_arp"] += 1
            self.handle_arp(dp, in_port, eth, pkt.get_protocol(arp.arp))
        elif eth.ethertype == ether_types.ETH_TYPE_IP:
            self.handle_ip(dp, in_port, eth, pkt, msg.data)

    def learn_host(self, dpid, port, mac, ip):
        if self.topo.is_inter_switch_port(dpid, port):
            return False
        self.hosts[mac] = {"ip": ip, "dpid": dpid, "port": port}
        self.ip_to_mac[ip] = mac
        return True

    def handle_arp(self, dp, in_port, eth, pkt_arp):
        if pkt_arp is None:
            return
        if not self.learn_host(dp.id, in_port, pkt_arp.src_mac, pkt_arp.src_ip):
            return
        if pkt_arp.opcode != arp.ARP_REQUEST:
            return
        target_mac = self.ip_to_mac.get(pkt_arp.dst_ip)
        if target_mac is None:
            return
        if target_mac not in self.mac_to_sid and pkt_arp.src_mac not in self.mac_to_sid:
            return
        reply = packet.Packet()
        reply.add_protocol(ethernet.ethernet(
            ethertype=ether_types.ETH_TYPE_ARP, dst=pkt_arp.src_mac, src=target_mac))
        reply.add_protocol(arp.arp(
            opcode=arp.ARP_REPLY, src_mac=target_mac, src_ip=pkt_arp.dst_ip,
            dst_mac=pkt_arp.src_mac, dst_ip=pkt_arp.src_ip))
        reply.serialize()
        self.packet_out(dp, dp.ofproto.OFPP_CONTROLLER, [in_port], reply.data)

    def handle_ip(self, dp, in_port, eth, pkt, data):
        ip = pkt.get_protocol(ipv4.ipv4)
        datagram = pkt.get_protocol(udp.udp)
        if ip is None:
            return
        if datagram is not None and eth.dst == BROADCAST_MAC and datagram.dst_port == self.adv_port:
            self.counters["packet_in_advert"] += 1
            self.handle_advert(dp, in_port, eth, ip, pkt.protocols[-1], data)
            return
        if self.topo.is_inter_switch_port(dp.id, in_port):
            return
        self.counters["packet_in_data"] += 1
        self.learn_host(dp.id, in_port, eth.src, ip.src)
        self.handle_unicast(dp, in_port, eth, ip, data)

    def handle_advert(self, dp, in_port, eth, ip, payload, data):
        if self.topo.is_inter_switch_port(dp.id, in_port):
            return
        if not isinstance(payload, (bytes, bytearray)):
            self.block(dp, in_port, eth.src, "malformed_advert")
            return
        advert = protocol.parse_advert(bytes(payload), self.key)
        if advert is None:
            self.block(dp, in_port, eth.src, "bad_signature")
            return
        if advert["ip"] != ip.src:
            self.block(dp, in_port, eth.src, "spoofed_address")
            return
        self.learn_host(dp.id, in_port, eth.src, ip.src)
        sid = self.mac_to_sid.get(eth.src)
        if sid is None:
            sid = self.next_sid
            self.next_sid += 1
            self.mac_to_sid[eth.src] = sid
            self.sessions[sid] = set()
            event_name = "server_registered"
        else:
            old = self.servers[sid]
            moved = (old["dpid"], old["port"]) != (dp.id, in_port)
            event_name = "server_moved" if moved else "server_refreshed"
        self.servers[sid] = {
            "sid": sid, "mac": eth.src, "ip": ip.src, "port_no": advert["port"],
            "name": advert["name"], "service": advert["service"],
            "dpid": dp.id, "port": in_port,
        }
        self.log(event_name, sid=sid, name=advert["name"], ip=ip.src, dpid=dp.id, port=in_port)
        outputs = self.rebuild_server(sid)
        self.packet_out(dp, in_port, outputs.get(dp.id, []), data)

    def install_tree(self, srv):
        parent, in_ports, children = self.topo.tree(srv["dpid"])
        outputs = {}
        parser = None
        for dpid in parent:
            dp = self.dps.get(dpid)
            if dp is None:
                continue
            parser = dp.ofproto_parser
            in_port = srv["port"] if dpid == srv["dpid"] else in_ports[dpid]
            outs = [p for _, p in children[dpid]]
            outs += [p for p in self.topo.host_ports(dpid) if p != in_port]
            outputs[dpid] = outs
            match = parser.OFPMatch(
                in_port=in_port, eth_src=srv["mac"], eth_dst=BROADCAST_MAC,
                eth_type=ether_types.ETH_TYPE_IP, ip_proto=in_proto.IPPROTO_UDP,
                udp_dst=self.adv_port)
            actions = [parser.OFPActionOutput(p) for p in outs]
            self.add_flow(dp, PRIORITY_ADV, match, actions, make_cookie(srv["sid"], KIND_ADV),
                          idle=self.adv_idle, flags=dp.ofproto.OFPFF_SEND_FLOW_REM)
        return outputs

    def install_session(self, srv, client_mac, first_packet=None):
        client = self.hosts.get(client_mac)
        if client is None:
            return False
        path = self.topo.shortest_path(client["dpid"], srv["dpid"])
        if path is None:
            return False
        cookie = make_cookie(srv["sid"], KIND_DATA)
        self.install_path(path, srv["port"], srv["mac"], srv["ip"], cookie)
        self.install_path(list(reversed(path)), client["port"], client_mac, client["ip"], cookie)
        return True

    def install_path(self, path, last_port, dst_mac, dst_ip, cookie):
        hops = self.topo.out_ports(path) + [last_port]
        for dpid, out_port in zip(path, hops):
            dp = self.dps.get(dpid)
            if dp is None:
                continue
            parser = dp.ofproto_parser
            match = parser.OFPMatch(eth_dst=dst_mac, eth_type=ether_types.ETH_TYPE_IP, ipv4_dst=dst_ip)
            self.add_flow(dp, PRIORITY_DATA, match, [parser.OFPActionOutput(out_port)],
                          cookie, idle=self.data_idle)

    def handle_unicast(self, dp, in_port, eth, ip, data):
        src_sid = self.mac_to_sid.get(eth.src)
        dst_mac = self.ip_to_mac.get(ip.dst)
        dst_sid = self.mac_to_sid.get(dst_mac) if dst_mac else None
        if dst_mac is None or (src_sid is None and dst_sid is None):
            return
        if dst_sid is not None:
            srv, client_mac = self.servers[dst_sid], eth.src
        else:
            srv, client_mac = self.servers[src_sid], dst_mac
        if not self.install_session(srv, client_mac):
            return
        self.sessions[srv["sid"]].add(client_mac)
        path = self.topo.shortest_path(dp.id, self.hosts[dst_mac]["dpid"])
        if path is None:
            return
        if len(path) == 1:
            out_port = self.hosts[dst_mac]["port"]
        else:
            out_port = self.topo.out_ports(path)[0]
        self.packet_out(dp, in_port, [out_port], data)
        self.log("session_installed", server=srv["name"], client=self.hosts[client_mac]["ip"],
                 path=path)

    def block(self, dp, in_port, mac, reason):
        parser = dp.ofproto_parser
        match = parser.OFPMatch(in_port=in_port, eth_src=mac)
        self.add_flow(dp, PRIORITY_BLOCK, match, [], make_cookie(0, KIND_BLOCK),
                      hard=self.block_seconds)
        self.counters["adverts_dropped"] += 1
        self.log("rogue_blocked", mac=mac, dpid=dp.id, port=in_port, reason=reason)

    @set_ev_cls(ofp_event.EventOFPFlowRemoved, MAIN_DISPATCHER)
    def on_flow_removed(self, ev):
        msg = ev.msg
        dp = msg.datapath
        if msg.reason != dp.ofproto.OFPRR_IDLE_TIMEOUT:
            return
        if msg.cookie & 0xff != KIND_ADV:
            return
        sid = msg.cookie >> 8
        srv = self.servers.get(sid)
        if srv is not None and srv["dpid"] == dp.id:
            self.deregister(sid, "advert_timeout")

    def deregister(self, sid, reason):
        srv = self.servers.pop(sid, None)
        if srv is None:
            return
        self.mac_to_sid.pop(srv["mac"], None)
        self.sessions.pop(sid, None)
        for dp in self.dps.values():
            self.delete_flows(dp, make_cookie(sid, 0), SERVER_MASK)
        self.log("server_removed", sid=sid, name=srv["name"], ip=srv["ip"], reason=reason)

    def rebuild_server(self, sid):
        srv = self.servers[sid]
        for dp in self.dps.values():
            self.delete_flows(dp, make_cookie(sid, 0), SERVER_MASK)
        outputs = self.install_tree(srv)
        for client_mac in list(self.sessions.get(sid, ())):
            if not self.install_session(srv, client_mac):
                self.sessions[sid].discard(client_mac)
        return outputs

    def rebuild_all(self, reason):
        started = time.time()
        for sid in list(self.servers):
            self.rebuild_server(sid)
        if self.servers:
            self.log("rebuild", reason=reason, servers=len(self.servers),
                     ms=round((time.time() - started) * 1000, 2))
