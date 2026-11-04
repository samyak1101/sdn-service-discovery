# Team roles

| Member | Part | Files |
|---|---|---|
| Samyak Sharma | Ryu controller: packet-in handling, spanning tree, shortest-path flows, proxy ARP, flow expiry, link failure rebuild, rogue quarantine, static baseline | `controller/` |
| Sajid Arman | Discovery client (registry, TTL, lock, receiver/reaper threads, sticky choice), Mininet diamond topology, T1-T8 functional tests | `app/client.py`, `topology/`, `experiments/functional_tests.py` |
| Samruddhi Shyamkant Wagh | UDP server and message format (broadcast adverts, HMAC signature, load), experiments E1-E4, dashboard | `app/server.py`, `app/protocol.py`, `experiments/run_experiments.py`, `dashboard/` |

Shared summary: servers broadcast signed UDP adverts, clients keep a TTL registry, a Ryu controller learns servers from packet-ins and installs reactive flows, and the network recovers on its own when a server or link fails.
