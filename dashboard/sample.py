import argparse
import json
import time
from pathlib import Path

from topology import layout


def write(path, records):
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def build(directory, now=None):
    now = time.time() if now is None else now
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    t0 = now - 60
    ctrl = []
    for dpid in layout.SWITCHES:
        ctrl.append({"t": t0, "event": "switch_up", "dpid": dpid})
    for a, _, b, _ in layout.LINKS:
        ctrl.append({"t": t0 + 1, "event": "link_up", "src": a, "dst": b})
        ctrl.append({"t": t0 + 1, "event": "link_up", "src": b, "dst": a})
    for i, name in enumerate(layout.SERVERS):
        ctrl.append({"t": t0 + 5 + i, "event": "server_registered", "sid": i + 1, "name": name,
                     "ip": layout.HOSTS[name]["ip"], "dpid": 4, "port": i + 1})
    for client in layout.CLIENTS:
        ctrl.append({"t": t0 + 9, "event": "session_installed", "server": "srv1",
                     "client": layout.HOSTS[client]["ip"], "path": [1, 2, 4]})
    ctrl.append({"t": t0 + 20, "event": "rogue_blocked", "mac": layout.mac_of("rogue"),
                 "dpid": 2, "port": 3, "reason": "bad_signature"})
    ctrl.append({"t": t0 + 30, "event": "link_down", "src": 2, "dst": 4})
    ctrl.append({"t": t0 + 30, "event": "link_down", "src": 4, "dst": 2})
    ctrl.append({"t": t0 + 30, "event": "rebuild", "reason": "link_down", "servers": 3, "ms": 2.4})
    ctrl.append({"t": t0 + 42, "event": "server_removed", "sid": 1, "name": "srv1",
                 "ip": layout.HOSTS["srv1"]["ip"], "reason": "advert_timeout"})
    ctrl.append({"t": now - 1, "event": "stats", "servers": 2, "packet_in": 31,
                 "packet_in_advert": 4, "packet_in_arp": 12, "packet_in_data": 15,
                 "flow_mods": 96, "adverts_dropped": 1})
    write(out / "controller.jsonl", ctrl)

    for n, client in enumerate(layout.CLIENTS):
        rec = []
        for i, server in enumerate(layout.SERVERS):
            rec.append({"t": t0 + 6 + i, "client": client, "event": "discovered",
                        "server": server, "ip": layout.HOSTS[server]["ip"]})
        for k in range(30):
            rec.append({"t": t0 + 10 + k, "client": client, "event": "request_ok", "id": k + 1,
                        "server": "srv1", "rtt_ms": 1.1 + 0.1 * n})
        rec.append({"t": t0 + 50, "client": client, "event": "expired", "server": "srv1",
                    "ip": layout.HOSTS["srv1"]["ip"]})
        for k in range(6):
            rec.append({"t": t0 + 50 + k, "client": client, "event": "request_ok", "id": 40 + k,
                        "server": "srv2", "rtt_ms": 1.3 + 0.1 * n})
        if n == 2:
            for k in range(3):
                rec.append({"t": t0 + 56 + k, "client": client, "event": "request_failed",
                            "id": 50 + k, "server": "srv2", "reason": "timeout"})
        write(out / ("%s.jsonl" % client), rec)


def main(argv=None):
    p = argparse.ArgumentParser(description="Write sample logs so the dashboard can be previewed without Mininet")
    p.add_argument("--out", default="logs/sample")
    args = p.parse_args(argv)
    build(args.out)
    print("sample logs written to %s" % args.out)


if __name__ == "__main__":
    main()
