import argparse
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

from experiments import analysis as an
from experiments.lab import Lab, ROOT
from topology import layout

SERVERS = layout.SERVERS
CLIENTS = layout.CLIENTS


class Checks:
    def __init__(self):
        self.items = []

    def check(self, condition, message):
        self.items.append((bool(condition), message))

    @property
    def passed(self):
        return all(ok for ok, _ in self.items)


def discovered(records):
    return {r["server"] for r in an.of_kind(records, "discovered")}


def t1_discovery_and_registration(make):
    c = Checks()
    with make("t1_discovery") as lab:
        for s in SERVERS:
            lab.start_server(s)
        time.sleep(3)
        lab.start_clients(8)
        lab.wait_for_clients()
        ctrl = lab.controller_records()
        registered = {r["name"] for r in an.of_kind(ctrl, "server_registered")}
        c.check(registered == set(SERVERS), "controller registered %s" % sorted(registered))
        for name in CLIENTS:
            rec = lab.client_records(name)
            c.check(discovered(rec) == set(SERVERS), "%s discovered %s" % (name, sorted(discovered(rec))))
            c.check(an.steady_success_rate(rec) >= 0.9, "%s request success %.0f%%" % (name, 100 * an.steady_success_rate(rec)))
        flows = lab.dump_flows("s1")
        c.check("tp_dst=5000" in flows, "s1 holds advert flow rules")
        c.check("nw_dst=10.0.0.11" in flows, "s1 holds a data path rule towards srv1")
    return c


def t2_control_plane_efficiency(make):
    c = Checks()
    with make("t2_efficiency") as lab:
        for s in SERVERS:
            lab.start_server(s)
        time.sleep(3)
        lab.start_clients(14)
        lab.wait_for_clients()
        stats = an.last_stats(lab.controller_records())
        sent = len(SERVERS) * int(14 / lab.interval)
        c.check(stats.get("packet_in_advert", 99) <= 2 * len(SERVERS),
                "advert packet-ins %s for about %d adverts sent" % (stats.get("packet_in_advert"), sent))
        c.check(stats.get("flow_mods", 0) > 0, "controller installed flows (%s flow-mods)" % stats.get("flow_mods"))
    return c


def t3_server_crash_failover(make):
    c = Checks()
    with make("t3_crash") as lab:
        for s in SERVERS:
            lab.start_server(s)
        time.sleep(3)
        lab.start_clients(30)
        time.sleep(8)
        t_kill = lab.kill("srv1")
        lab.wait_for_clients()
        ctrl = lab.controller_records()
        removed = [r for r in an.of_kind(ctrl, "server_removed", name="srv1") if r["t"] > t_kill]
        c.check(removed and removed[0]["t"] - t_kill <= lab.adv_idle + 3,
                "controller removed srv1 %.1fs after crash" % (removed[0]["t"] - t_kill if removed else -1))
        for name in CLIENTS:
            rec = lab.client_records(name)
            gone = [r for r in an.of_kind(rec, "expired", server="srv1") if r["t"] > t_kill]
            c.check(gone and gone[0]["t"] - t_kill <= lab.ttl + 2,
                    "%s dropped srv1 from registry %.1fs after crash" % (name, gone[0]["t"] - t_kill if gone else -1))
            rec_t = an.recovery_time(rec, t_kill, "srv1")
            c.check(rec_t is not None and rec_t <= lab.ttl + 4,
                    "%s served by another server after %s s" % (name, "%.1f" % rec_t if rec_t else "never"))
    return c


def t4_new_server_joins(make):
    c = Checks()
    with make("t4_join") as lab:
        lab.start_server("srv1")
        time.sleep(3)
        lab.start_clients(16)
        time.sleep(6)
        t_join = time.time()
        lab.start_server("srv2")
        lab.wait_for_clients()
        ctrl = lab.controller_records()
        c.check({r["name"] for r in an.of_kind(ctrl, "server_registered")} == {"srv1", "srv2"},
                "controller registered both servers")
        for name in CLIENTS:
            rec = lab.client_records(name)
            seen = [r for r in an.of_kind(rec, "discovered", server="srv2") if r["t"] >= t_join]
            c.check(seen and seen[0]["t"] - t_join <= lab.interval + 2,
                    "%s discovered srv2 %.1fs after it started" % (name, seen[0]["t"] - t_join if seen else -1))
    return c


def t5_link_failure_reroute(make):
    c = Checks()
    with make("t5_link") as lab:
        for s in SERVERS:
            lab.start_server(s)
        time.sleep(3)
        lab.start_clients(26)
        time.sleep(8)
        t_fail = lab.set_link("s2", "s4", False)
        time.sleep(6)
        flows_s3 = lab.dump_flows("s3")
        lab.wait_for_clients()
        ctrl = lab.controller_records()
        c.check(an.of_kind(ctrl, "link_down"), "controller saw the link go down")
        c.check([r for r in an.of_kind(ctrl, "rebuild") if r["t"] >= t_fail], "controller rebuilt tree and paths")
        c.check("10.0.0.11" in flows_s3, "s3 now carries the data path to srv1")
        for name in CLIENTS:
            rec = lab.client_records(name)
            gap = an.outage_around(rec, t_fail)
            c.check(gap is not None and gap <= 4.0,
                    "%s outage %s s" % (name, "%.1f" % gap if gap is not None else "unbounded"))
            c.check(not an.of_kind(rec, "expired", server="srv1"), "%s never lost srv1 from its registry" % name)
    return c


def t6_rogue_server_blocked(make):
    c = Checks()
    with make("t6_rogue") as lab:
        lab.start_server("srv1")
        lab.start_server("rogue", key="wrong-key")
        time.sleep(3)
        lab.start_clients(14, verify=False)
        lab.wait_for_clients()
        ctrl = lab.controller_records()
        blocked = an.of_kind(ctrl, "rogue_blocked")
        c.check(blocked and blocked[0]["mac"] == layout.mac_of("rogue"), "controller quarantined the rogue host")
        for name in CLIENTS:
            rec = lab.client_records(name)
            c.check("rogue" not in discovered(rec), "%s never saw the rogue advert (client verification off)" % name)
            c.check("srv1" in discovered(rec), "%s still discovered the real server" % name)
    return c


def t7_packet_loss_robustness(make):
    c = Checks()
    with make("t7_loss", loss=10.0) as lab:
        for s in SERVERS:
            lab.start_server(s)
        time.sleep(3)
        lab.start_clients(22)
        lab.wait_for_clients()
        ctrl = lab.controller_records()
        c.check(not an.of_kind(ctrl, "server_removed"), "no live server was wrongly removed by the controller")
        for name in CLIENTS:
            rec = lab.client_records(name)
            c.check(an.steady_success_rate(rec) >= 0.45,
                    "%s request success %.0f%% at 10%% loss per link" % (name, 100 * an.steady_success_rate(rec)))
            c.check(discovered(rec) == set(SERVERS), "%s discovered all servers" % name)
    return c


def t8_unregistered_host_unreachable(make):
    c = Checks()
    with make("t8_unauthorized") as lab:
        lab.start_server("srv1")
        lab.start_server("rogue", advertise=False)
        time.sleep(3)
        lab.start_client("c1", 8, static="%s:6000" % layout.HOSTS["rogue"]["ip"])
        lab.start_client("c2", 8, static="%s:6000" % layout.HOSTS["srv1"]["ip"])
        lab.wait_for_clients()
        c.check(an.steady_success_rate(lab.client_records("c1")) == 0.0, "traffic to the unregistered host is dropped")
        c.check(an.steady_success_rate(lab.client_records("c2")) >= 0.9, "traffic to the registered server flows")
    return c


SCENARIOS = {
    "T1": ("Discovery and registration", t1_discovery_and_registration),
    "T2": ("Control plane efficiency", t2_control_plane_efficiency),
    "T3": ("Server crash failover", t3_server_crash_failover),
    "T4": ("New server joins", t4_new_server_joins),
    "T5": ("Link failure reroute", t5_link_failure_reroute),
    "T6": ("Rogue server blocked", t6_rogue_server_blocked),
    "T7": ("Packet loss robustness", t7_packet_loss_robustness),
    "T8": ("Unregistered host unreachable", t8_unregistered_host_unreachable),
}


def main(argv=None):
    p = argparse.ArgumentParser(description="Functional test cases (needs root, Mininet, OVS and Ryu)")
    p.add_argument("--only", default="")
    p.add_argument("--results", default=str(ROOT / "results" / "functional"))
    p.add_argument("--ryu-manager", default=None, help="path to ryu-manager, defaults to venv/bin/ryu-manager")
    args = p.parse_args(argv)
    if os.geteuid() != 0:
        sys.exit("run as root: sudo /usr/bin/python3 -m experiments.functional_tests")
    chosen = [k for k in SCENARIOS if not args.only or k in args.only.split(",")]
    results_dir = Path(args.results)
    results_dir.mkdir(parents=True, exist_ok=True)

    def make(name, **kwargs):
        return Lab(name, results_dir, ryu_manager=args.ryu_manager, **kwargs)

    subprocess.run(["mn", "-c"], capture_output=True)
    lines = ["# Functional test report", ""]
    failed = 0
    for key in chosen:
        title, fn = SCENARIOS[key]
        print("== %s %s" % (key, title), flush=True)
        try:
            checks = fn(make)
            items = checks.items
            ok = checks.passed
        except Exception:
            items = [(False, "exception: " + traceback.format_exc().splitlines()[-1])]
            traceback.print_exc()
            ok = False
            subprocess.run(["mn", "-c"], capture_output=True)
        failed += 0 if ok else 1
        lines.append("## %s %s: %s" % (key, title, "PASS" if ok else "FAIL"))
        for good, msg in items:
            lines.append("- [%s] %s" % ("x" if good else " ", msg))
            print("   %s %s" % ("PASS" if good else "FAIL", msg), flush=True)
        lines.append("")
    (results_dir / "report.md").write_text("\n".join(lines))
    print("\n%d of %d scenarios passed" % (len(chosen) - failed, len(chosen)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
