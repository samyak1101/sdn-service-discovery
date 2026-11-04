import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from experiments import analysis
from topology import layout

ROOT = Path(__file__).resolve().parent.parent


def find_ryu_manager(explicit=None):
    if explicit:
        return explicit
    local = ROOT / "venv" / "bin" / "ryu-manager"
    return str(local) if local.exists() else "ryu-manager"


class Lab:
    def __init__(self, name, results_dir, mode="dynamic", loss=0.0, interval=2.0,
                 ryu_manager=None, ofp_port=6653):
        self.name = name
        self.mode = mode
        self.loss = loss
        self.interval = interval
        self.ttl = 3 * interval
        self.adv_idle = int(round(3 * interval))
        self.ryu_manager = find_ryu_manager(ryu_manager)
        self.ofp_port = ofp_port
        self.dir = Path(results_dir) / name
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True)
        self.ctrl_log = self.dir / "controller.jsonl"
        self.ctrl = None
        self.net = None
        self.procs = {}
        self.files = []

    def start(self):
        self.start_controller()
        self.start_network()
        self.wait_ready()
        return self

    def start_controller(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        env["SD_LOG"] = str(self.ctrl_log)
        env["SD_ADV_IDLE"] = str(self.adv_idle)
        module = "controller.sd_controller" if self.mode == "dynamic" else "controller.static_controller"
        cmd = [self.ryu_manager, "--ofp-tcp-listen-port", str(self.ofp_port)]
        if self.mode == "dynamic":
            cmd.append("--observe-links")
        cmd.append(module)
        out = open(self.dir / "ryu.out", "w")
        self.files.append(out)
        self.ctrl = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.ctrl.poll() is not None:
                raise RuntimeError("ryu-manager exited early, see %s" % (self.dir / "ryu.out"))
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", self.ofp_port)) == 0:
                    return
            time.sleep(0.3)
        raise RuntimeError("controller did not start listening")

    def start_network(self):
        from topology.topo import build_network
        self.net = build_network(loss=self.loss, controller_port=self.ofp_port,
                                 static_arp=self.mode == "static")

    def wait_ready(self, timeout=40):
        if self.mode == "static":
            time.sleep(3)
            return
        deadline = time.time() + timeout
        while time.time() < deadline:
            records = analysis.read_jsonl(self.ctrl_log)
            up = len(analysis.of_kind(records, "switch_up"))
            links = len(analysis.of_kind(records, "link_up"))
            if up >= len(layout.SWITCHES) and links >= 2 * len(layout.LINKS):
                time.sleep(1)
                return
            time.sleep(0.5)
        raise RuntimeError("topology discovery did not finish")

    def spawn(self, host_name, label, args):
        host = self.net.get(host_name)
        out = open(self.dir / ("%s.out" % label), "w")
        self.files.append(out)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        proc = host.popen([sys.executable, "-u", "-m"] + args, stdout=out,
                          stderr=subprocess.STDOUT, cwd=str(ROOT), env=env)
        self.procs[label] = proc
        return proc

    def start_server(self, name, advertise=True, key=None, delay=None):
        args = ["app.server", "--name", name, "--ip", layout.HOSTS[name]["ip"],
                "--interval", str(self.interval)]
        if not advertise:
            args.append("--no-advert")
        if key:
            args += ["--key", key]
        if delay:
            args += ["--delay", str(delay)]
        return self.spawn(name, name, args)

    def start_client(self, name, duration, static=None, verify=True, interval=0.5):
        args = ["app.client", "--name", name, "--duration", str(duration),
                "--ttl", str(self.ttl), "--interval", str(interval),
                "--log", str(self.log_path(name))]
        if static:
            args += ["--static", static]
        if not verify:
            args.append("--no-verify")
        return self.spawn(name, "client_" + name, args)

    def start_clients(self, duration, **kwargs):
        for name in layout.CLIENTS:
            self.start_client(name, duration, **kwargs)

    def log_path(self, client):
        return self.dir / ("%s.jsonl" % client)

    def client_records(self, name):
        return analysis.read_jsonl(self.log_path(name))

    def controller_records(self):
        return analysis.read_jsonl(self.ctrl_log)

    def kill(self, label):
        proc = self.procs.pop(label, None)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        return time.time()

    def set_link(self, a, b, up):
        self.net.configLinkStatus(a, b, "up" if up else "down")
        return time.time()

    def dump_flows(self, switch):
        out = subprocess.run(["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", switch],
                             capture_output=True, text=True)
        return out.stdout

    def wait_for_clients(self):
        for label, proc in list(self.procs.items()):
            if label.startswith("client_"):
                proc.wait()

    def stop(self):
        for label in list(self.procs):
            self.kill(label)
        if self.net is not None:
            try:
                self.net.stop()
            except Exception:
                pass
            self.net = None
        if self.ctrl is not None and self.ctrl.poll() is None:
            self.ctrl.terminate()
            try:
                self.ctrl.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.ctrl.kill()
        for f in self.files:
            f.close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
