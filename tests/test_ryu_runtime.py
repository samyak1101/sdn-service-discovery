import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RYU_MANAGER = Path(sys.executable).parent / "ryu-manager"

OFP_VERSION = 4
OFPT_HELLO = 0
OFPT_FEATURES_REQUEST = 5
OFPT_FEATURES_REPLY = 6
OFPT_FLOW_MOD = 14
OFPFC_ADD = 0
OFPFC_DELETE = 3


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def read_message(sock):
    header = b""
    while len(header) < 8:
        chunk = sock.recv(8 - len(header))
        if not chunk:
            return None
        header += chunk
    version, kind, length, xid = struct.unpack("!BBHI", header)
    body = b""
    while len(body) < length - 8:
        chunk = sock.recv(length - 8 - len(body))
        if not chunk:
            return None
        body += chunk
    return kind, xid, body


@unittest.skipUnless(RYU_MANAGER.exists(), "needs the real ryu-manager next to this python")
class RealRyuTests(unittest.TestCase):
    def setUp(self):
        self.port = free_port()
        self.log = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False).name
        env = dict(os.environ, PYTHONPATH=str(ROOT), SD_LOG=self.log, SD_STATS="0")
        self.out = tempfile.NamedTemporaryFile(suffix=".out", delete=False)
        self.proc = subprocess.Popen(
            [str(RYU_MANAGER), "--ofp-tcp-listen-port", str(self.port), "controller.sd_controller"],
            cwd=ROOT, env=env, stdout=self.out, stderr=subprocess.STDOUT)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self.fail("ryu-manager exited: " + Path(self.out.name).read_text()[-600:])
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", self.port)) == 0:
                    return
            time.sleep(0.2)
        self.fail("ryu-manager never started listening")

    def tearDown(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.out.close()

    def test_switch_handshake_gets_table_miss_rule(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        sock.sendall(struct.pack("!BBHI", OFP_VERSION, OFPT_HELLO, 8, 1))
        got_features_request = False
        flow_mods = []
        deadline = time.time() + 10
        while time.time() < deadline and len(flow_mods) < 2:
            msg = read_message(sock)
            self.assertIsNotNone(msg, "controller closed the connection")
            kind, xid, body = msg
            if kind == OFPT_FEATURES_REQUEST:
                got_features_request = True
                reply = struct.pack("!BBHIQIBB2xII", OFP_VERSION, OFPT_FEATURES_REPLY, 32, xid,
                                    7, 256, 254, 0, 0, 0)
                sock.sendall(reply)
            elif kind == OFPT_FLOW_MOD:
                fields = struct.unpack("!QQBBHHHIIIH2x", body[:40])
                flow_mods.append({"command": fields[3], "priority": fields[6]})
        sock.close()
        self.assertTrue(got_features_request)
        self.assertEqual(flow_mods[0]["command"], OFPFC_DELETE)
        self.assertEqual(flow_mods[1]["command"], OFPFC_ADD)
        self.assertEqual(flow_mods[1]["priority"], 0)


if __name__ == "__main__":
    unittest.main()
