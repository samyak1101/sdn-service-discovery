import unittest

from tests.sdn_sim import events, make_network

CLIENTS = ["c1", "c2", "c3"]
OTHERS = ["srv2", "srv3", "rogue"]


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.net = make_network()

    def test_first_advert_registers_server_and_reaches_every_host_once(self):
        self.net.advert("srv1")
        self.assertEqual([e["name"] for e in events(self.net, "server_registered")], ["srv1"])
        for name in CLIENTS + OTHERS:
            self.assertEqual(len(self.net.hosts[name].adverts()), 1, name)
        self.assertEqual(len(self.net.hosts["srv1"].adverts()), 0)

    def test_later_adverts_use_installed_flows_not_the_controller(self):
        self.net.advert("srv1")
        before = self.net.ctrl.counters["packet_in"]
        for _ in range(5):
            self.net.advert("srv1")
        self.assertEqual(self.net.ctrl.counters["packet_in"], before)
        for name in CLIENTS:
            self.assertEqual(len(self.net.hosts[name].adverts()), 6, name)

    def test_multiple_servers_registered_independently(self):
        for s in ("srv1", "srv2", "srv3"):
            self.net.advert(s)
        names = sorted(e["name"] for e in events(self.net, "server_registered"))
        self.assertEqual(names, ["srv1", "srv2", "srv3"])
        for name in CLIENTS:
            self.assertEqual(len(self.net.hosts[name].adverts()), 3)

    def test_advert_flows_exist_on_every_switch_on_the_tree(self):
        self.net.advert("srv1")
        for dpid in (1, 2, 3, 4):
            self.assertTrue(any(e.cookie & 0xff == 1 for e in self.net.tables[dpid]), dpid)


class LivenessTests(unittest.TestCase):
    def setUp(self):
        self.net = make_network()

    def test_server_removed_when_adverts_stop(self):
        self.net.advert("srv1")
        self.net.advance(7)
        removed = events(self.net, "server_removed")
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0]["reason"], "advert_timeout")
        self.assertEqual(self.net.flows_with_cookie(1), [])

    def test_server_stays_while_it_keeps_advertising(self):
        self.net.advert("srv1")
        for _ in range(10):
            self.net.advance(2)
            self.net.advert("srv1")
        self.assertEqual(events(self.net, "server_removed"), [])

    def test_server_reregisters_after_removal(self):
        self.net.advert("srv1")
        self.net.advance(7)
        self.net.advert("srv1")
        self.assertEqual(len(events(self.net, "server_registered")), 2)
        self.assertEqual(len(events(self.net, "server_removed")), 1)
        self.assertEqual(len(self.net.ctrl.servers), 1)
        for name in ("c1", "c2", "c3"):
            self.assertEqual(len(self.net.hosts[name].adverts()), 2, name)


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.net = make_network()
        self.net.advert("srv1")

    def test_wrongly_signed_advert_is_blocked_and_never_delivered(self):
        self.net.advert("rogue", key="stolen-guess")
        blocked = events(self.net, "rogue_blocked")
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["reason"], "bad_signature")
        for name in CLIENTS:
            self.assertEqual(len(self.net.hosts[name].adverts()), 1)

    def test_block_flow_stops_repeat_packet_in(self):
        self.net.advert("rogue", key="stolen-guess")
        before = self.net.ctrl.counters["packet_in"]
        for _ in range(4):
            self.net.advert("rogue", key="stolen-guess")
        self.assertEqual(self.net.ctrl.counters["packet_in"], before)

    def test_spoofed_address_in_signed_advert_is_blocked(self):
        self.net.advert("rogue", claimed_ip="10.0.0.11")
        self.assertEqual(events(self.net, "rogue_blocked")[0]["reason"], "spoofed_address")
        self.assertEqual(len(self.net.ctrl.servers), 1)

    def test_block_expires(self):
        self.net.advert("rogue", key="stolen-guess")
        self.net.advance(31)
        self.net.advert("rogue", key="stolen-guess")
        self.assertEqual(len(events(self.net, "rogue_blocked")), 2)

    def test_unregistered_host_unreachable(self):
        self.net.arp_request("c1", "10.0.0.99")
        self.assertEqual([r for r in self.net.hosts["c1"].inbox if r["kind"] == "arp"], [])
        self.net.udp("c1", "rogue")
        self.assertEqual([r for r in self.net.hosts["rogue"].inbox if r["kind"] == "udp"], [])


class DataPathTests(unittest.TestCase):
    def setUp(self):
        self.net = make_network()
        self.net.advert("srv1")
        self.net.arp_request("c1", "10.0.0.11")
        self.net.arp_request("srv1", "10.0.0.1")

    def arp_replies(self, name):
        return [r for r in self.net.hosts[name].inbox if r["kind"] == "arp"]

    def udp_received(self, name):
        return [r for r in self.net.hosts[name].inbox if r["kind"] == "udp"]

    def test_proxy_arp_answers_for_registered_server(self):
        reply = self.arp_replies("c1")[0]["arp"]
        self.assertEqual(reply.src_mac, "00:00:00:00:00:0b")
        self.assertEqual(reply.src_ip, "10.0.0.11")

    def test_proxy_arp_answers_server_asking_for_client(self):
        reply = self.arp_replies("srv1")[0]["arp"]
        self.assertEqual(reply.src_mac, "00:00:00:00:00:01")

    def test_request_installs_path_and_is_delivered_once(self):
        self.net.udp("c1", "srv1")
        self.assertEqual(len(self.udp_received("srv1")), 1)
        session = events(self.net, "session_installed")[0]
        self.assertEqual(session["path"], [1, 2, 4])

    def test_following_packets_and_replies_skip_the_controller(self):
        self.net.udp("c1", "srv1")
        before = self.net.ctrl.counters["packet_in"]
        self.net.udp("c1", "srv1")
        self.net.udp("srv1", "c1")
        self.assertEqual(self.net.ctrl.counters["packet_in"], before)
        self.assertEqual(len(self.udp_received("srv1")), 2)
        self.assertEqual(len(self.udp_received("c1")), 1)

    def test_server_removal_deletes_session_flows(self):
        self.net.udp("c1", "srv1")
        self.net.advance(7)
        self.assertEqual(self.net.flows_with_cookie(1), [])
        self.net.udp("c1", "srv1")
        self.assertEqual(len(self.udp_received("srv1")), 1)


class LinkFailureTests(unittest.TestCase):
    def setUp(self):
        self.net = make_network()
        self.net.advert("srv1")
        self.net.arp_request("c1", "10.0.0.11")
        self.net.arp_request("srv1", "10.0.0.1")
        self.net.udp("c1", "srv1")
        self.net.udp("srv1", "c1")

    def udp_received(self, name):
        return [r for r in self.net.hosts[name].inbox if r["kind"] == "udp"]

    def test_traffic_is_rerouted_without_new_packet_in(self):
        self.net.link_down(2, 2, 4, 4)
        before = self.net.ctrl.counters["packet_in"]
        self.net.udp("c1", "srv1")
        self.net.udp("srv1", "c1")
        self.assertEqual(self.net.ctrl.counters["packet_in"], before)
        self.assertEqual(len(self.udp_received("srv1")), 2)
        self.assertEqual(len(self.udp_received("c1")), 2)

    def test_rerouted_path_uses_the_other_branch(self):
        self.net.link_down(2, 2, 4, 4)
        s2_flows = [e for e in self.net.tables[2] if e.cookie & 0xff == 2]
        s3_flows = [e for e in self.net.tables[3] if e.cookie & 0xff == 2]
        self.assertEqual(s2_flows, [])
        self.assertEqual(len(s3_flows), 2)

    def test_adverts_still_reach_clients_after_failure_without_duplicates(self):
        self.net.link_down(2, 2, 4, 4)
        before = {n: len(self.net.hosts[n].adverts()) for n in ("c1", "c2", "c3")}
        self.net.advert("srv1")
        for n in before:
            self.assertEqual(len(self.net.hosts[n].adverts()), before[n] + 1, n)

    def test_recovery_after_link_returns(self):
        self.net.link_down(2, 2, 4, 4)
        self.net.link_up(2, 2, 4, 4)
        self.net.udp("c1", "srv1")
        self.assertEqual(len(self.udp_received("srv1")), 2)
        before = len(self.net.hosts["c2"].adverts())
        self.net.advert("srv1")
        self.assertEqual(len(self.net.hosts["c2"].adverts()), before + 1)

    def test_rebuild_event_logged(self):
        self.net.link_down(2, 2, 4, 4)
        self.assertTrue(any(e["reason"] == "link_down" for e in events(self.net, "rebuild")))

    def test_total_partition_leaves_server_unreachable_not_crashing(self):
        self.net.link_down(2, 2, 4, 4)
        self.net.link_down(3, 2, 4, 5)
        before = len(self.udp_received("srv1"))
        self.net.udp("c1", "srv1")
        self.assertEqual(len(self.udp_received("srv1")), before)


class MobilityTests(unittest.TestCase):
    def test_server_moving_to_another_switch_is_relearned(self):
        net = make_network()
        net.advert("srv1")
        net.advance(1)
        net.ctrl.topo.ports[3].add(3)
        net.host_at.pop((4, 1))
        net.host_at[(3, 3)] = "srv1"
        import tests.sdn_sim as sim
        sim.layout.HOSTS["srv1"]["switch"] = 3
        sim.layout.HOSTS["srv1"]["port"] = 3
        try:
            net.advert("srv1")
        finally:
            sim.layout.HOSTS["srv1"]["switch"] = 4
            sim.layout.HOSTS["srv1"]["port"] = 1
        self.assertEqual(len(events(net, "server_moved")), 1)
        self.assertEqual(net.ctrl.servers[1]["dpid"], 3)
        for name in CLIENTS:
            self.assertEqual(len(net.hosts[name].adverts()), 2, name)


if __name__ == "__main__":
    unittest.main()
