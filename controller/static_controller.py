from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, set_ev_cls
from ryu.lib.packet import ether_types
from ryu.ofproto import ofproto_v1_3

from controller.graph import Topology
from topology import layout


class StaticController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(StaticController, self).__init__(*args, **kwargs)
        self.topo = Topology()
        for dpid in layout.SWITCHES:
            self.topo.add_switch(dpid, layout.switch_ports(dpid))
        for a, pa, b, pb in layout.LINKS:
            self.topo.add_link(a, pa, b, pb)
            self.topo.add_link(b, pb, a, pa)

    def next_port(self, dpid, host):
        target = layout.HOSTS[host]["switch"]
        if dpid == target:
            return layout.HOSTS[host]["port"]
        path = self.topo.shortest_path(dpid, target)
        return self.topo.neighbors(dpid)[path[1]]

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def on_features(self, ev):
        dp = ev.msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        dp.send_msg(parser.OFPFlowMod(
            datapath=dp, table_id=ofp.OFPTT_ALL, command=ofp.OFPFC_DELETE,
            out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY, match=parser.OFPMatch()))
        for host in layout.HOSTS:
            match = parser.OFPMatch(eth_dst=layout.mac_of(host))
            actions = [parser.OFPActionOutput(self.next_port(dp.id, host))]
            inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
            dp.send_msg(parser.OFPFlowMod(
                datapath=dp, priority=100, match=match, instructions=inst))
