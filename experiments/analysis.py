import json


def read_jsonl(path):
    records = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except ValueError:
                        pass
    except FileNotFoundError:
        pass
    return records


def of_kind(records, kind, **match):
    return [r for r in records
            if r.get("event") == kind and all(r.get(k) == v for k, v in match.items())]


def ok_requests(records):
    return sorted(of_kind(records, "request_ok"), key=lambda r: r["t"])


def failed_requests(records):
    return sorted(of_kind(records, "request_failed"), key=lambda r: r["t"])


def success_rate(records):
    ok = len(of_kind(records, "request_ok"))
    bad = len(of_kind(records, "request_failed"))
    total = ok + bad
    return ok / total if total else 0.0


def steady_success_rate(records):
    events = sorted([r for r in records if r.get("event") in ("request_ok", "request_failed")],
                    key=lambda r: r["t"])
    for i, r in enumerate(events):
        if r["event"] == "request_ok":
            tail = events[i:]
            return sum(1 for e in tail if e["event"] == "request_ok") / len(tail)
    return 0.0


def recovery_time(records, since, dead_server):
    for r in ok_requests(records):
        if r["t"] > since and r.get("server") != dead_server:
            return r["t"] - since
    return None


def outage_around(records, moment):
    ok = [r["t"] for r in ok_requests(records)]
    before = [t for t in ok if t <= moment]
    after = [t for t in ok if t > moment]
    if not after:
        return None
    start = before[-1] if before else moment
    return after[0] - start


def longest_gap(records, start, end):
    ok = [r["t"] for r in ok_requests(records) if start <= r["t"] <= end]
    points = [start] + ok + [end]
    return max(b - a for a, b in zip(points, points[1:]))


def last_stats(records):
    stats = of_kind(records, "stats")
    return stats[-1] if stats else {}


def stats_at(records, moment):
    candidates = [r for r in of_kind(records, "stats") if r["t"] <= moment]
    return candidates[-1] if candidates else {}


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None
