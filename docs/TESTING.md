# Testing

## Level 1: tests that need no network emulation

```
venv/bin/python -m tests.run_all
```

Each file runs in its own process because Ryu patches sockets and timers on import.

| File | What it covers |
|---|---|
| `test_sockets.py` | Advert signing and rejection, registry TTL and selection, real UDP broadcast on loopback between servers and clients, request and response |
| `test_graph.py` | Shortest path, rerouting when a link is removed, partition, spanning tree, host-port classification |
| `test_analysis.py` | Log analysis helpers used by the experiments |
| `test_dashboard.py` | Dashboard state building from logs (server, link, client and rogue handling, malformed logs, large logs) and its HTTP endpoints |
| `test_controller_sim.py` | The real controller code driven through an in-memory OpenFlow simulator (`tests/sdn_sim.py`) |
| `test_ryu_runtime.py` | Starts the real `ryu-manager` with the controller and completes an OpenFlow 1.3 handshake with a fake switch, checking the table-miss rule arrives. Skipped when the real Ryu is not installed |

The simulator implements flow tables with priorities, idle and hard timeouts, flow-removed
messages, packet-in and packet-out, and wires the same diamond topology. It checks that:

- an advert reaches every host exactly once and uses no loops,
- later adverts do not reach the controller,
- a silent server is removed after the idle timeout and its flows disappear,
- a wrongly signed or address-spoofing advert is quarantined and never delivered,
- proxy ARP, path installation and two-way delivery work, and unregistered hosts are unreachable,
- after a link failure traffic uses the other branch with no new packet-ins, adverts still arrive once each, and the network recovers when the link returns,
- a server that appears on another switch is re-learned.

This level does not run Open vSwitch or a real Ryu process, so it cannot catch problems in
how OVS interprets a flow rule or in Ryu's LLDP timing. Level 2 covers those.

## Level 2: Mininet functional tests (root)

```
sudo /usr/bin/python3 -m experiments.functional_tests
sudo /usr/bin/python3 -m experiments.functional_tests --only T3,T5
```

Request success rates are measured from each client's first successful request onward, so the moment before the first advert arrives does not count against it. A client that never succeeds scores 0%.

Each scenario starts a fresh controller and network and writes its logs to
`results/functional/<scenario>/`.

| ID | Scenario | Pass criteria |
|---|---|---|
| T1 | Discovery and registration | Controller registers 3 servers, each client discovers all 3, at least 90% of requests answered, advert and path rules present on s1 |
| T2 | Control plane efficiency | Advert packet-ins stay at about one per server while adverts keep flowing |
| T3 | Server crash | Controller removes srv1 within idle timeout plus 3 s, clients drop it within TTL plus 2 s, clients served by another server within TTL plus 4 s |
| T4 | New server joins | Every client discovers srv2 within one interval plus 2 s |
| T5 | Link failure | s2-s4 goes down, controller rebuilds, s3 carries the path, each client outage is at most 4 s, no client loses srv1 from its registry |
| T6 | Rogue server | Controller quarantines the rogue even though client verification is off, no client ever sees its advert |
| T7 | 10% loss per link | No live server removed, all servers discovered, at least 45% of requests answered (about 66% is expected for four lossy hops) |
| T8 | Unregistered host | Requests to an unregistered host all fail, requests to a registered server succeed |

## Level 3: performance experiments (root)

```
sudo /usr/bin/python3 -m experiments.run_experiments --repeats 3
```

| ID | Question | Dynamic vs baseline |
|---|---|---|
| E1 | How fast does the system recover from a server crash? Varies the advert interval (1 s and 2 s) | Dynamic controller and clients vs static clients pinned to one server |
| E2 | How long is the outage when the primary path fails? | Dynamic reroute vs static flows |
| E3 | How does packet loss (0, 5, 10, 20, 30% per inter-switch link) affect answered requests and registry stability? | Dynamic vs static |
| E4 | How many adverts reach the controller as servers and advert rate grow? | Measured packet-ins vs one packet-in per advert (computed) |

Outputs go to `results/experiments/`: CSV files, `summary.md` and PNG charts.
Per-run logs are kept under `results/experiments/runs/`.

## Reproducing from scratch

1. Ubuntu 20.04 or 22.04 VM, then `./scripts/setup_ubuntu.sh`.
2. `venv/bin/python -m tests.run_all` should pass.
3. `sudo mn -c` to clear any old Mininet state.
4. Run the two commands above.
