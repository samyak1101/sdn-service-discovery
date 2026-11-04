SUBNET = "10.0.0.0/24"
BROADCAST = "10.0.0.255"

SWITCHES = [1, 2, 3, 4]

LINKS = [
    (1, 4, 2, 1),
    (1, 5, 3, 1),
    (2, 2, 4, 4),
    (3, 2, 4, 5),
]

HOSTS = {
    "c1": {"ip": "10.0.0.1", "switch": 1, "port": 1},
    "c2": {"ip": "10.0.0.2", "switch": 1, "port": 2},
    "c3": {"ip": "10.0.0.3", "switch": 1, "port": 3},
    "srv1": {"ip": "10.0.0.11", "switch": 4, "port": 1},
    "srv2": {"ip": "10.0.0.12", "switch": 4, "port": 2},
    "srv3": {"ip": "10.0.0.13", "switch": 4, "port": 3},
    "rogue": {"ip": "10.0.0.99", "switch": 2, "port": 3},
}

CLIENTS = ["c1", "c2", "c3"]
SERVERS = ["srv1", "srv2", "srv3"]


def mac_of(name):
    last = int(HOSTS[name]["ip"].split(".")[-1])
    return "00:00:00:00:00:%02x" % last


def switch_ports(dpid):
    ports = {h["port"] for h in HOSTS.values() if h["switch"] == dpid}
    for a, pa, b, pb in LINKS:
        if a == dpid:
            ports.add(pa)
        if b == dpid:
            ports.add(pb)
    return ports
