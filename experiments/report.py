import argparse
import csv
from collections import defaultdict
from pathlib import Path

from experiments.lab import ROOT


def load(path):
    if not path.exists():
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def avg(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def fmt(value, digits=2, none="no recovery"):
    return none if value is None else ("%." + str(digits) + "f") % value


def group(rows, keys):
    out = defaultdict(list)
    for r in rows:
        out[tuple(r[k] for k in keys)].append(r)
    return out


def table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def e1(rows):
    out = ["## E1 Server crash: detection and client recovery", ""]
    body = []
    for (mode, interval), rs in sorted(group(rows, ["mode", "adv_interval_s"]).items()):
        body.append([mode, interval, len(rs),
                     fmt(avg([num(r["controller_detect_s"]) for r in rs]), none="n/a"),
                     fmt(avg([num(r["mean_client_recovery_s"]) for r in rs])),
                     fmt(avg([num(r["request_success_rate"]) for r in rs]) and 100 * avg([num(r["request_success_rate"]) for r in rs]), 1, "n/a")])
    out.append(table(["mode", "advert interval (s)", "runs", "controller detects (s)",
                      "client recovery (s)", "requests answered (%)"], body))
    return out


def e2(rows):
    out = ["", "## E2 Link failure on the primary path", ""]
    body = []
    for (mode,), rs in sorted(group(rows, ["mode"]).items()):
        rate = avg([num(r["request_success_rate"]) for r in rs])
        body.append([mode, len(rs), fmt(avg([num(r["mean_outage_s"]) for r in rs]), none="never recovered"),
                     fmt(avg([num(r["max_outage_s"]) for r in rs]), none="never recovered"),
                     fmt(None if rate is None else 100 * rate, 1, "n/a")])
    out.append(table(["mode", "runs", "mean outage (s)", "worst outage (s)", "requests answered (%)"], body))
    return out


def e3(rows):
    out = ["", "## E3 Packet loss sweep (loss on each inter-switch link)", ""]
    body = []
    for (loss, mode), rs in sorted(group(rows, ["loss_pct_per_link", "mode"]).items(), key=lambda kv: (float(kv[0][0]), kv[0][1])):
        rate = avg([num(r["request_success_rate"]) for r in rs])
        body.append([loss, mode, len(rs), fmt(None if rate is None else 100 * rate, 1, "n/a"),
                     fmt(avg([num(r["client_registry_expiries"]) for r in rs]), 1, "n/a"),
                     fmt(avg([num(r["controller_false_removals"]) for r in rs]), 1, "n/a")])
    out.append(table(["loss (%)", "mode", "runs", "requests answered (%)",
                      "registry expiries (3 clients)", "controller false removals"], body))
    return out


def e4(rows):
    out = ["", "## E4 Control plane overhead", ""]
    body = []
    for r in rows:
        sent = num(r["adverts_sent_approx"])
        pin = num(r["advert_packet_ins"])
        saved = None if not sent or pin is None else 100 * (1 - pin / sent)
        body.append([r["adv_interval_s"], r["servers"], int(sent) if sent else "n/a",
                     int(pin) if pin is not None else "n/a", r["total_packet_ins"], r["flow_mods"],
                     fmt(saved, 1, "n/a")])
    out.append(table(["advert interval (s)", "servers", "adverts sent (approx)", "advert packet-ins",
                      "all packet-ins", "flow-mods", "adverts kept off controller (%)"], body))
    out.append("")
    out.append("The comparison baseline is a controller that handles every advert itself, "
               "which costs one packet-in per advert. That figure is computed from the number of "
               "adverts sent, not measured separately.")
    return out


def plots(directory, e1_rows, e2_rows, e3_rows):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []
    made = []
    if e3_rows:
        fig, ax = plt.subplots(figsize=(6, 3.6))
        for mode in ("dynamic", "static"):
            pts = sorted((float(k[0]), avg([num(r["request_success_rate"]) for r in rs]) * 100)
                         for k, rs in group([r for r in e3_rows if r["mode"] == mode], ["loss_pct_per_link"]).items())
            if pts:
                ax.plot([p[0] for p in pts], [p[1] for p in pts], marker="o", label=mode)
        ax.set_xlabel("loss per inter-switch link (%)")
        ax.set_ylabel("requests answered (%)")
        ax.legend()
        fig.tight_layout()
        fig.savefig(directory / "e3_loss.png", dpi=150)
        made.append("e3_loss.png")
        plt.close(fig)
    if e1_rows:
        fig, ax = plt.subplots(figsize=(6, 3.6))
        labels, values = [], []
        for (mode, interval), rs in sorted(group(e1_rows, ["mode", "adv_interval_s"]).items()):
            v = avg([num(r["mean_client_recovery_s"]) for r in rs])
            labels.append("%s\n%gs adverts" % (mode, float(interval)))
            values.append(v if v is not None else 0)
        ax.bar(labels, values)
        ax.set_ylabel("client recovery after crash (s)")
        fig.tight_layout()
        fig.savefig(directory / "e1_recovery.png", dpi=150)
        made.append("e1_recovery.png")
        plt.close(fig)
    return made


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--results", default=str(ROOT / "results" / "experiments"))
    args = p.parse_args(argv)
    d = Path(args.results)
    r1, r2 = load(d / "e1_server_crash.csv"), load(d / "e2_link_failure.csv")
    r3, r4 = load(d / "e3_loss_sweep.csv"), load(d / "e4_control_overhead.csv")
    lines = ["# Experiment results", ""]
    if r1:
        lines += e1(r1)
    if r2:
        lines += e2(r2)
    if r3:
        lines += e3(r3)
    if r4:
        lines += e4(r4)
    for name in plots(d, r1, r2, r3):
        lines.append("")
        lines.append("![%s](%s)" % (name, name))
    (d / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
