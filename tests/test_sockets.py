import tempfile
import time
import unittest

from app import protocol
from app.client import DiscoveryClient, Registry
from app.server import ServiceServer

ADV = 25871
BCAST = "127.255.255.255"


def wait_for(predicate, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class ProtocolTests(unittest.TestCase):
    def test_signed_advert_round_trip(self):
        data = protocol.make_advert("srv1", "echo", "10.0.0.11", 6000, 0, 1, "k")
        msg = protocol.parse_advert(data, "k")
        self.assertEqual(msg["name"], "srv1")
        self.assertNotIn("sig", msg)

    def test_wrong_key_rejected(self):
        data = protocol.make_advert("srv1", "echo", "10.0.0.11", 6000, 0, 1, "k")
        self.assertIsNone(protocol.parse_advert(data, "other"))

    def test_tampered_advert_rejected(self):
        data = protocol.make_advert("srv1", "echo", "10.0.0.11", 6000, 0, 1, "k")
        self.assertIsNone(protocol.parse_advert(data.replace(b"10.0.0.11", b"10.0.0.99"), "k"))

    def test_garbage_rejected(self):
        self.assertIsNone(protocol.parse_advert(b"\xff\xfe", "k"))
        self.assertIsNone(protocol.parse_advert(b"{}", "k"))
        self.assertIsNone(protocol.parse_advert(b"[1,2]", "k"))


class RegistryTests(unittest.TestCase):
    def advert(self, name, ip, load=0):
        return {"name": name, "service": "echo", "ip": ip, "port": 6000, "load": load}

    def test_entries_expire_after_ttl(self):
        reg = Registry(ttl=2.0)
        self.assertTrue(reg.update(self.advert("a", "1.1.1.1"), 100.0))
        self.assertFalse(reg.update(self.advert("a", "1.1.1.1"), 101.0))
        self.assertEqual(reg.expire(102.5), [])
        gone = reg.expire(103.5)
        self.assertEqual(len(gone), 1)
        self.assertIsNone(reg.choose("echo"))

    def test_choose_least_loaded_then_lowest_ip(self):
        reg = Registry(ttl=5.0)
        reg.update(self.advert("a", "1.1.1.2", load=3), 0)
        reg.update(self.advert("b", "1.1.1.1", load=1), 0)
        reg.update(self.advert("c", "1.1.1.3", load=1), 0)
        self.assertEqual(reg.choose("echo")[1], "b")


class LoopbackTests(unittest.TestCase):
    def setUp(self):
        self.servers = []
        self.clients = []
        self.log = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False).name

    def tearDown(self):
        for s in self.servers:
            s.stop()
        for c in self.clients:
            c.close()
        time.sleep(0.6)

    def server(self, name, ip, port, key=protocol.DEFAULT_KEY, interval=0.3):
        s = ServiceServer(name, "echo", ip, port, BCAST, ADV, interval, key)
        s.start()
        self.servers.append(s)
        return s

    def client(self, ttl=1.0, verify=True):
        c = DiscoveryClient("c", "echo", ADV, ttl, protocol.DEFAULT_KEY, verify, self.log)
        import threading
        threading.Thread(target=c.listen_loop, daemon=True).start()
        threading.Thread(target=c.reap_loop, daemon=True).start()
        self.clients.append(c)
        return c

    def test_client_discovers_multiple_servers(self):
        c = self.client()
        self.server("srv1", "127.0.0.1", 26101)
        self.server("srv2", "127.0.0.2", 26102)
        self.assertTrue(wait_for(lambda: c.registry.names() == ["srv1", "srv2"], 3))

    def test_server_removed_after_it_stops_advertising(self):
        c = self.client(ttl=1.0)
        s1 = self.server("srv1", "127.0.0.1", 26111)
        self.assertTrue(wait_for(lambda: c.registry.names() == ["srv1"], 3))
        s1.stop()
        self.assertTrue(wait_for(lambda: c.registry.names() == [], 4))

    def test_request_response_via_discovered_server(self):
        c = self.client()
        self.server("srv1", "127.0.0.1", 26121)
        self.assertTrue(wait_for(lambda: c.registry.names() == ["srv1"], 3))
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        (ip, port), name = c.pick()
        c.exchange(sock, 1, ip, port, name)
        sock.close()
        self.assertEqual(c.ok, 1)

    def test_client_rejects_wrongly_signed_advert(self):
        c = self.client()
        self.server("rogue", "127.0.0.3", 26131, key="not-the-key")
        time.sleep(1.2)
        self.assertEqual(c.registry.names(), [])

    def test_unverified_client_accepts_rogue(self):
        c = self.client(verify=False)
        self.server("rogue", "127.0.0.3", 26141, key="not-the-key")
        self.assertTrue(wait_for(lambda: c.registry.names() == ["rogue"], 3))


if __name__ == "__main__":
    unittest.main()
