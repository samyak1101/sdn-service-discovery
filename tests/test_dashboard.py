import json
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from dashboard import sample
from dashboard.server import FlowCache, make_handler
from dashboard.state import MAX_TAIL, build_state, read_jsonl


def write_lines(path, lines):
    Path(path).write_text("\n".join(lines) + "\n")


class StateTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.now = time.time()
        sample.build(self.dir, self.now)
        self.state = build_state(self.dir, self.now)

    def server(self, name):
        return next(s for s in self.state["servers"] if s["name"] == name)

    def test_server_states_follow_events(self):
        self.assertEqual(self.server("srv1")["status"], "down")
        self.assertEqual(self.server("srv1")["reason"], "advert_timeout")
        self.assertEqual(self.server("srv2")["status"], "up")
        self.assertEqual(self.server("srv3")["switch"], 4)

    def test_link_state_and_duplicate_events_collapsed(self):
        links = {(l["a"], l["b"]): l["state"] for l in self.state["links"]}
        self.assertEqual(links[(2, 4)], "down")
        self.assertEqual(links[(1, 2)], "up")
        downs = [e for e in self.state["timeline"] if e["text"] == "link s2-s4 down"]
        self.assertEqual(len(downs), 1)

    def test_rogue_is_reported(self):
        self.assertEqual(len(self.state["blocked"]), 1)
        self.assertEqual(self.state["blocked"][0]["reason"], "bad_signature")

    def test_client_summaries(self):
        by = {c["name"]: c for c in self.state["clients"]}
        self.assertEqual(by["c1"]["current"], "srv2")
        self.assertEqual(by["c1"]["known"], ["srv2", "srv3"])
        self.assertEqual(by["c1"]["status"], "ok")
        self.assertEqual(by["c3"]["status"], "failing")
        self.assertLess(by["c3"]["recent_pct"], 100)
        self.assertEqual(by["c1"]["failed"], 0)

    def test_timeline_newest_first_and_bounded(self):
        times = [e["t"] for e in self.state["timeline"]]
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertLessEqual(len(times), 80)

    def test_stats_and_live_flag(self):
        self.assertEqual(self.state["stats"]["packet_in_advert"], 4)
        self.assertTrue(self.state["live"])
        old = build_state(self.dir, self.now + 600)
        self.assertFalse(old["live"])

    def test_layout_is_included_for_the_page(self):
        self.assertEqual(self.state["layout"]["servers"], ["srv1", "srv2", "srv3"])
        self.assertEqual(len(self.state["layout"]["links"]), 4)

    def test_empty_folder(self):
        state = build_state(tempfile.mkdtemp(), self.now)
        self.assertFalse(state["has_data"])
        self.assertEqual(state["servers"], [])
        self.assertFalse(state["live"])

    def test_missing_fields_and_garbage_lines_do_not_crash(self):
        d = tempfile.mkdtemp()
        write_lines(Path(d) / "controller.jsonl", [
            "not json", "[1,2]", json.dumps({"event": "server_registered"}),
            json.dumps({"event": "link_down"}), json.dumps({"event": "server_removed"}), "{"])
        write_lines(Path(d) / "c1.jsonl", [json.dumps({"event": "request_ok"}), "oops"])
        state = build_state(d, self.now)
        self.assertTrue(state["has_data"])

    def test_re_registration_after_removal_shows_up_again(self):
        d = tempfile.mkdtemp()
        write_lines(Path(d) / "controller.jsonl", [
            json.dumps({"t": 1, "event": "server_registered", "name": "srv1", "ip": "10.0.0.11", "dpid": 4, "port": 1}),
            json.dumps({"t": 2, "event": "server_removed", "name": "srv1", "reason": "advert_timeout"}),
            json.dumps({"t": 3, "event": "server_registered", "name": "srv1", "ip": "10.0.0.11", "dpid": 4, "port": 1})])
        state = build_state(d, 4)
        self.assertEqual(state["servers"][0]["status"], "up")
        self.assertEqual(state["servers"][0]["since"], 3)

    def test_large_log_is_tailed(self):
        d = Path(tempfile.mkdtemp())
        line = json.dumps({"t": 1, "event": "request_ok", "server": "srv1", "pad": "x" * 200})
        with open(d / "c1.jsonl", "w") as f:
            for _ in range(MAX_TAIL // len(line) + 5000):
                f.write(line + "\n")
        records = read_jsonl(d / "c1.jsonl")
        self.assertTrue(0 < len(records) < MAX_TAIL // len(line) + 5000)
        self.assertTrue(all(r["event"] == "request_ok" for r in records))


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        sample.build(cls.dir)
        handler = make_handler(cls.dir, FlowCache(False))
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def get(self, path):
        return urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path), timeout=5)

    def test_page_is_served(self):
        r = self.get("/")
        self.assertEqual(r.status, 200)
        self.assertIn(b"Service Discovery Dashboard", r.read())

    def test_api_returns_state_with_flows_disabled(self):
        data = json.loads(self.get("/api/state").read())
        self.assertIn("servers", data)
        self.assertIsNone(data["flows"])
        self.assertEqual(len(data["clients"]), 3)

    def test_unknown_path_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/../../etc/passwd")
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
