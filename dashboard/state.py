import json
import os
import time
from pathlib import Path

from topology import layout

MAX_TAIL = 2000000
TIMELINE_LIMIT = 80
CONTROLLER_LOG = "controller.jsonl"
LIVE_WINDOW = 10.0


def read_jsonl(path):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > MAX_TAIL:
                f.seek(size - MAX_TAIL)
                f.readline()
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    records = []
    for line in data.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def by_time(records):
    return sorted(records, key=lambda r: r.get("t", 0))


def pair_label(a, b):
    lo, hi = sorted((a, b))
    return "s%s-s%s" % (lo, hi)


def summarise_client(name, records):
    known = {}
    recent = []
    rtts = []
    ok = failed = 0
    current = None
    for r in by_time(records):
        kind = r.get("event")
        if kind == "discovered":
            known[r.get("server", "?")] = r.get("ip")
        elif kind == "expired":
            known.pop(r.get("server", "?"), None)
        elif kind == "request_ok":
            ok += 1
            recent.append(1)
            current = r.get("server")
            if isinstance(r.get("rtt_ms"), (int, float)):
                rtts.append(r["rtt_ms"])
        elif kind == "request_failed":
            failed += 1
            recent.append(0)
    recent = recent[-20:]
    rtts = rtts[-10:]
    if not recent:
        status = "idle"
    else:
        status = "ok" if recent[-1] else "failing"
    return {
        "name": name,
        "known": sorted(known),
        "current": current,
        "ok": ok,
        "failed": failed,
        "recent_pct": round(100.0 * sum(recent) / len(recent), 1) if recent else None,
        "rtt_ms": round(sum(rtts) / len(rtts), 2) if rtts else None,
        "status": status,
    }


def build_state(log_dir, now=None):
    now = time.time() if now is None else now
    log_dir = Path(log_dir)
    ctrl = by_time(read_jsonl(log_dir / CONTROLLER_LOG))
    client_logs = {}
    for path in sorted(log_dir.glob("*.jsonl")):
        if path.name != CONTROLLER_LOG:
            client_logs[path.stem] = read_jsonl(path)

    servers = {}
    switches = {}
    links = {}
    blocked = []
    stats = {}
    timeline = []

    def add(t, source, tone, text):
        timeline.append({"t": t, "source": source, "tone": tone, "text": text})

    for r in ctrl:
        kind = r.get("event")
        t = r.get("t", 0)
        if kind in ("server_registered", "server_moved", "server_refreshed"):
            name = r.get("name", "?")
            prev = servers.get(name)
            since = prev["since"] if prev and prev["status"] == "up" else t
            servers[name] = {
                "name": name, "ip": r.get("ip"), "switch": r.get("dpid"),
                "port": r.get("port"), "status": "up", "since": since, "reason": None,
            }
            if kind == "server_registered":
                add(t, "controller", "good", "%s registered on s%s port %s" % (name, r.get("dpid"), r.get("port")))
            elif kind == "server_moved":
                add(t, "controller", "warn", "%s moved to s%s port %s" % (name, r.get("dpid"), r.get("port")))
        elif kind == "server_removed":
            name = r.get("name", "?")
            if name in servers:
                servers[name]["status"] = "down"
                servers[name]["reason"] = r.get("reason")
                servers[name]["since"] = t
            add(t, "controller", "bad", "%s removed (%s)" % (name, r.get("reason", "unknown")))
        elif kind in ("link_up", "link_down"):
            key = tuple(sorted((r.get("src", 0), r.get("dst", 0))))
            state = "up" if kind == "link_up" else "down"
            if links.get(key) != state:
                links[key] = state
                add(t, "controller", "good" if state == "up" else "bad",
                    "link %s %s" % (pair_label(*key), state))
        elif kind in ("switch_up", "switch_down"):
            switches[r.get("dpid")] = "up" if kind == "switch_up" else "down"
            add(t, "controller", "info" if kind == "switch_up" else "bad",
                "switch s%s %s" % (r.get("dpid"), "connected" if kind == "switch_up" else "left"))
        elif kind == "rebuild":
            add(t, "controller", "info", "paths rebuilt after %s (%s servers, %s ms)" % (
                r.get("reason"), r.get("servers"), r.get("ms")))
        elif kind == "rogue_blocked":
            blocked.append({"mac": r.get("mac"), "switch": r.get("dpid"), "port": r.get("port"),
                            "reason": r.get("reason"), "t": t})
            add(t, "controller", "bad", "blocked %s on s%s port %s: %s" % (
                r.get("mac"), r.get("dpid"), r.get("port"), r.get("reason")))
        elif kind == "session_installed":
            path = " > ".join("s%s" % d for d in r.get("path", []))
            add(t, "controller", "info", "path installed for %s to %s via %s" % (
                r.get("client"), r.get("server"), path))
        elif kind == "stats":
            stats = {k: v for k, v in r.items() if k not in ("t", "event")}

    clients = []
    latest = ctrl[-1].get("t", 0) if ctrl else 0
    for name, records in client_logs.items():
        clients.append(summarise_client(name, records))
        for r in records:
            t = r.get("t", 0)
            latest = max(latest, t)
            kind = r.get("event")
            who = r.get("client", name)
            if kind == "discovered":
                add(t, who, "good", "%s discovered %s" % (who, r.get("server")))
            elif kind == "expired":
                add(t, who, "warn", "%s dropped %s from its registry (timeout)" % (who, r.get("server")))
            elif kind == "advert_rejected":
                add(t, who, "warn", "%s rejected an unsigned advert from %s" % (who, r.get("src")))

    timeline.sort(key=lambda e: e["t"])
    timeline = timeline[-TIMELINE_LIMIT:][::-1]

    return {
        "now": now,
        "latest": latest,
        "live": bool(latest) and now - latest < LIVE_WINDOW,
        "has_data": bool(ctrl or client_logs),
        "servers": sorted(servers.values(), key=lambda s: s["name"]),
        "clients": sorted(clients, key=lambda c: c["name"]),
        "links": [{"a": a, "b": b, "state": s} for (a, b), s in sorted(links.items())],
        "switches": {"s%s" % d: s for d, s in switches.items()},
        "blocked": blocked[-10:],
        "stats": stats,
        "timeline": timeline,
        "layout": {
            "switches": layout.SWITCHES,
            "links": [{"a": a, "b": b} for a, _, b, _ in layout.LINKS],
            "hosts": {n: {"ip": h["ip"], "switch": h["switch"], "mac": layout.mac_of(n)}
                      for n, h in layout.HOSTS.items()},
            "clients": layout.CLIENTS,
            "servers": layout.SERVERS,
        },
    }
