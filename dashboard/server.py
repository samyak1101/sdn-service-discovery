import argparse
import json
import re
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dashboard.state import build_state
from topology import layout

PAGE = Path(__file__).resolve().parent / "index.html"
FLOW_NOISE = re.compile(r"(duration|n_bytes|idle_age|hard_age)=[^,\s]+,?\s*")
PRIORITY = re.compile(r"priority=(\d+)")


def read_flows():
    if not shutil.which("ovs-ofctl"):
        return None
    result = {}
    for dpid in layout.SWITCHES:
        name = "s%d" % dpid
        try:
            out = subprocess.run(["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", name],
                                 capture_output=True, text=True, timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if out.returncode != 0:
            return None
        rows = [FLOW_NOISE.sub("", line.strip()) for line in out.stdout.splitlines()
                if line.strip().startswith("cookie=")]
        rows.sort(key=lambda row: -int((PRIORITY.search(row) or [0, 0])[1]))
        result[name] = rows
    return result


class FlowCache:
    def __init__(self, enabled, ttl=2.0):
        self.enabled = enabled
        self.ttl = ttl
        self.lock = threading.Lock()
        self.stamp = 0.0
        self.value = None

    def get(self):
        if not self.enabled:
            return None
        with self.lock:
            if time.time() - self.stamp > self.ttl:
                self.value = read_flows()
                self.stamp = time.time()
            return self.value


def make_handler(log_dir, flows):
    class Handler(BaseHTTPRequestHandler):
        def send_body(self, status, content_type, body):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self.send_body(200, "text/html; charset=utf-8", PAGE.read_bytes())
            elif path == "/api/state":
                state = build_state(log_dir)
                state["flows"] = flows.get()
                self.send_body(200, "application/json", json.dumps(state).encode())
            else:
                self.send_body(404, "text/plain", b"not found")

        def log_message(self, *args):
            pass

    return Handler


def main(argv=None):
    p = argparse.ArgumentParser(description="Live dashboard for the service discovery project")
    p.add_argument("--logs", default="logs", help="folder with controller.jsonl and client *.jsonl logs")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--no-flows", action="store_true", help="do not read flow tables with ovs-ofctl")
    args = p.parse_args(argv)
    Path(args.logs).mkdir(parents=True, exist_ok=True)
    handler = make_handler(args.logs, FlowCache(not args.no_flows))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print("dashboard on http://%s:%d reading %s" % (args.host, args.port, args.logs))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
