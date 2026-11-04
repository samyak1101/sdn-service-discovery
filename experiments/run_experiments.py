import argparse
import csv
import os
import subprocess
import sys
import time
from pathlib import Path

from experiments import analysis as an
from experiments.lab import Lab, ROOT
from topology import layout

SERVERS = layout.SERVERS
CLIENTS = layout.CLIENTS
SERVER_STATIC = "%s:6000" % layout.HOSTS["srv1"]["ip"]


class Runner:
    def __init__(self, results_dir, ryu_manager, repeats):
        self.dir = Path(results_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ryu_manager = ryu_manager
        self.repeats = repeats

    def lab(self, name, **kwargs):
        return Lab(name, self.dir / "runs", ryu_manager=self.ryu_manager, **kwargs)

    def write_csv(self, name, header, rows):
        with open(self.dir / name, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)

    def start_workload(self, lab, duration):
        if lab.mode == "dynamic":
            for s in SERVERS:
                lab.start_server(s)
            time.sleep(3)
            lab.start_clients(duration)
        else:
            for s in SERVERS:
                lab.start_server(s, advertise=False)
            time.sleep(2)
            lab.start_clients(duration, static=SERVER_STATIC)

    def e1_server_crash(self):
        rows = []
        for mode in ("dynamic", "static"):
            for interval in (1.0, 2.0):
                if mode == "static" and interval != 2.0:
                    continue
                for rep in range(self.repeats):
                    with self.lab("e1_%s_%g_%d" % (mode, interval, rep), mode=mode, interval=interval) as lab:
                        duration = 18 + 6 * interval
                        self.start_workload(lab, duration)
                        time.sleep(8)
                        t_kill = lab.kill("srv1")
                        lab.wait_for_clients()
                        ctrl = lab.controller_records()
                        removed = [r for r in an.of_kind(ctrl, "server_removed", name="srv1") if r["t"] > t_kill]
                        detect = removed[0]["t"] - t_kill if removed else None
                        recov = [None] * len(CLIENTS)
                        if mode == "dynamic":
                            recov = [an.recovery_time(lab.client_records(c), t_kill, "srv1") for c in CLIENTS]
                        rate = an.mean([an.steady_success_rate(lab.client_records(c)) for c in CLIENTS])
                        rows.append([mode, interval, rep, detect, an.mean(recov), max([r for r in recov if r is not None], default=None), rate])
                        print("E1", rows[-1], flush=True)
        self.write_csv("e1_server_crash.csv",
                       ["mode", "adv_interval_s", "run", "controller_detect_s", "mean_client_recovery_s",
                        "max_client_recovery_s", "request_success_rate"], rows)

    def e2_link_failure(self):
        rows = []
        for mode in ("dynamic", "static"):
            for rep in range(self.repeats):
                with self.lab("e2_%s_%d" % (mode, rep), mode=mode) as lab:
                    self.start_workload(lab, 26)
                    time.sleep(8)
                    t_fail = lab.set_link("s2", "s4", False)
                    lab.wait_for_clients()
                    outages = [an.outage_around(lab.client_records(c), t_fail) for c in CLIENTS]
                    rate = an.mean([an.steady_success_rate(lab.client_records(c)) for c in CLIENTS])
                    rows.append([mode, rep, an.mean(outages), max([o for o in outages if o is not None], default=None), rate])
                    print("E2", rows[-1], flush=True)
        self.write_csv("e2_link_failure.csv",
                       ["mode", "run", "mean_outage_s", "max_outage_s", "request_success_rate"], rows)

    def e3_loss_sweep(self):
        rows = []
        for loss in (0, 5, 10, 20, 30):
            for mode in ("dynamic", "static"):
                for rep in range(self.repeats):
                    with self.lab("e3_%s_%d_%d" % (mode, loss, rep), mode=mode, loss=float(loss)) as lab:
                        self.start_workload(lab, 30)
                        lab.wait_for_clients()
                        recs = [lab.client_records(c) for c in CLIENTS]
                        rate = an.mean([an.steady_success_rate(r) for r in recs])
                        flaps = sum(len(an.of_kind(r, "expired")) for r in recs)
                        removed = len(an.of_kind(lab.controller_records(), "server_removed"))
                        rows.append([mode, loss, rep, rate, flaps, removed])
                        print("E3", rows[-1], flush=True)
        self.write_csv("e3_loss_sweep.csv",
                       ["mode", "loss_pct_per_link", "run", "request_success_rate",
                        "client_registry_expiries", "controller_false_removals"], rows)

    def e4_control_overhead(self):
        rows = []
        duration = 30
        for interval in (1.0, 2.0):
            for n in (1, 2, 3):
                with self.lab("e4_%g_%d" % (interval, n), interval=interval) as lab:
                    for s in SERVERS[:n]:
                        lab.start_server(s)
                    time.sleep(3)
                    lab.start_clients(duration)
                    lab.wait_for_clients()
                    ctrl = lab.controller_records()
                    stats = an.last_stats(ctrl)
                    sent = n * (duration + 3) / interval
                    rows.append([interval, n, round(sent), stats.get("packet_in_advert"),
                                 stats.get("packet_in"), stats.get("flow_mods")])
                    print("E4", rows[-1], flush=True)
        self.write_csv("e4_control_overhead.csv",
                       ["adv_interval_s", "servers", "adverts_sent_approx", "advert_packet_ins",
                        "total_packet_ins", "flow_mods"], rows)


def main(argv=None):
    p = argparse.ArgumentParser(description="Performance experiments (needs root, Mininet, OVS and Ryu)")
    p.add_argument("--only", default="E1,E2,E3,E4")
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--results", default=str(ROOT / "results" / "experiments"))
    p.add_argument("--ryu-manager", default=None, help="path to ryu-manager, defaults to venv/bin/ryu-manager")
    args = p.parse_args(argv)
    if os.geteuid() != 0:
        sys.exit("run as root: sudo /usr/bin/python3 -m experiments.run_experiments")
    subprocess.run(["mn", "-c"], capture_output=True)
    runner = Runner(args.results, args.ryu_manager, args.repeats)
    chosen = args.only.split(",")
    if "E1" in chosen:
        runner.e1_server_crash()
    if "E2" in chosen:
        runner.e2_link_failure()
    if "E3" in chosen:
        runner.e3_loss_sweep()
    if "E4" in chosen:
        runner.e4_control_overhead()
    subprocess.run([sys.executable, "-m", "experiments.report", "--results", args.results])


if __name__ == "__main__":
    main()
