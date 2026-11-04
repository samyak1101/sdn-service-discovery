# Dynamic UDP Service Discovery in Software-Defined Networks

SDN-Socket Programming, Jackfruit mini project (Computer Networks, PES University)

Team: Samyak Sharma, Sajid Arman, Samruddhi Shyamkant Wagh

Stack: Python UDP sockets, Mininet, Open vSwitch, Ryu (OpenFlow 1.3)

## What it does

A client normally needs the server's IP address configured in advance. Here it does not.
Servers broadcast small signed JSON adverts over UDP. Clients listen and keep a live
registry of servers with a timeout per entry. The Ryu controller reads the adverts too,
registers each server, and installs the flow rules that carry the adverts and the
client traffic. If a server stops, moves, or joins, or if a link fails, the network and the
clients adapt without anyone editing configuration.

```
        c1   c2   c3                       srv1  srv2  srv3         rogue
         \    |    /                          \    |    /             |
          +---s1---+                           +---s4---+            |
              /  \                                 / \               |
            s2    s3 ----------------------------+   +--------------s2 (port 3)
              \____ s2-s4 ____/
```

Diamond topology: clients on s1, servers on s4, two equal-length paths (s1-s2-s4 and
s1-s3-s4), and an unauthorised host on s2.

## Repository layout

| Path | Purpose |
|---|---|
| `app/protocol.py` | Advert, request and response formats, HMAC signing |
| `app/server.py` | UDP server: periodic broadcast adverts plus a UDP service |
| `app/client.py` | UDP client: receiver thread, TTL registry behind a lock, request loop |
| `controller/sd_controller.py` | Ryu app: dynamic behaviour (the project's SDN logic) |
| `controller/graph.py` | Topology graph, spanning tree and path computation (no Ryu dependency) |
| `controller/static_controller.py` | Baseline: proactive static flows, no reaction to anything |
| `topology/layout.py` | One definition of hosts, ports and links, shared by everything |
| `topology/topo.py` | Mininet topology, runnable with a CLI |
| `dashboard/` | Live web dashboard: topology, servers, clients, event timeline, flow tables |
| `experiments/` | Functional tests (T1-T8), performance experiments (E1-E4), report generator |
| `tests/` | Unit and simulation tests that run without Mininet |
| `docs/` | Architecture, testing guide, rubric mapping |

## Setup (Ubuntu 22.04 or 20.04, needs sudo)

```
./scripts/setup_ubuntu.sh
```

The script installs Mininet and Open vSwitch with apt, then builds a separate Python 3.9
environment in `venv/` with Ryu 4.34 and the eventlet version it needs. Ryu is old and does
not run on Python 3.10 or newer, which is why it gets its own Python 3.9 (downloaded by the
`uv` tool, so it works even on newer Ubuntu versions). Mininet things use the normal system
Python. You never need to activate `venv/`, the scripts call it directly.

Use a real Ubuntu install or VM. WSL2 does not have the Open vSwitch kernel module.

## Run it by hand (demo)

Terminal 1, the controller (its events are written to `logs/controller.jsonl`):

```
./scripts/run_controller.sh
```

Terminal 2, the network. Start it from the project folder so the hosts can write to `logs/`:

```
sudo python3 -m topology.topo
```

In the Mininet CLI, start servers and a client:

```
mininet> srv1 python3 -m app.server --name srv1 --ip 10.0.0.11 &
mininet> srv2 python3 -m app.server --name srv2 --ip 10.0.0.12 &
mininet> c1 python3 -m app.client --name c1 --duration 120 --log logs/c1.jsonl &
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s1
```

Terminal 3, the dashboard (open http://127.0.0.1:8080). Use sudo if you want the flow tables panel to work:

```
sudo ./scripts/run_dashboard.sh
```

Things to try during the demo:

- Kill `srv1` (`sh pkill -f "name 10.0.0.11"`; Mininet replaces host names with IPs inside commands) and watch the controller log `server_removed` and the client switch to `srv2`.
- `link s2 s4 down` and watch the traffic move to the s3 branch with a `rebuild` event.
- Start `rogue python3 -m app.server --name rogue --ip 10.0.0.99 --key wrong &` and watch `rogue_blocked`.

## Dashboard

A small web page that shows the network live: the diamond topology with registered servers
in green, the server each client is using, link failures in red, blocked hosts, an event
timeline, the controller's packet-in and flow-mod counters, and the flow table of every
switch. It reads the same log files the controller and clients already write, so it needs no
extra packages.

```
sudo ./scripts/run_dashboard.sh                 # live view of logs/
./scripts/run_dashboard.sh results/functional/t3_crash   # replay a finished test run
python3 -m dashboard.sample && ./scripts/run_dashboard.sh logs/sample   # preview without Mininet
```

The controller log must be called `controller.jsonl`; every other `.jsonl` file in the folder
is treated as a client. The status badge reads "live" while events are arriving and "replay"
for old logs. Flow tables are read with `ovs-ofctl`, so that panel only works on the machine
running Mininet and usually needs sudo. The page binds to 127.0.0.1 only.

## Run the automated tests

Without Mininet (finishes in about 15 seconds, uses the real Ryu from `venv/`):

```
venv/bin/python -m tests.run_all
```

It runs each test file in its own process, because Ryu patches Python's sockets and timers
when it is imported, which would break the other tests if they shared one process.

With Mininet, Open vSwitch and Ryu (needs root, use the system Python and not the venv one):

```
sudo /usr/bin/python3 -m experiments.functional_tests
sudo /usr/bin/python3 -m experiments.run_experiments --repeats 3
```

The first writes `results/functional/report.md`. The second writes CSV files and
`results/experiments/summary.md`. Details are in `docs/TESTING.md`.

## Configuration

| Setting | Where | Default |
|---|---|---|
| Advert interval | `--interval` on the server | 2 s |
| Client registry timeout | `--ttl` on the client | 6 s (three adverts) |
| Controller liveness timeout | `SD_ADV_IDLE` environment variable | 6 s |
| Shared signing key | `--key` on server and client, `SD_KEY` for the controller | built-in demo key |
| Rogue quarantine time | `SD_BLOCK_SECONDS` | 30 s |
| Controller log file | `SD_LOG` | stdout |

## Known limits

- The signing key is shared, so a compromised server can sign adverts. A real deployment would use per-server keys.
- Adverts are signed but carry no timestamp, so a recorded advert can be replayed.
- A host that spoofs a legitimate server's MAC address is not detected.
- Broadcast discovery stays inside one Layer 2 domain by design.
- The controller is a single instance with no replication.
