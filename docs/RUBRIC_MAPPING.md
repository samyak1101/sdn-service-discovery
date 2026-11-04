# Rubric mapping

## D1 (15 marks)

| Component | Marks | Where to look |
|---|---|---|
| Problem understanding and architecture | 2 | `README.md`, `docs/ARCHITECTURE.md` |
| TCP/UDP socket implementation | 4 | `app/server.py`, `app/client.py`, `app/protocol.py`. Low-level UDP sockets, broadcast, receiver thread, lock-protected registry, thread-pool server, error handling for malformed and unsigned messages |
| Mininet + SDN implementation | 4 | `topology/topo.py`, `topology/layout.py`, `controller/sd_controller.py`. Diamond topology, reactive controller, tree and path flow rules |
| Socket-SDN integration | 3 | Advert causes registration and flow installation. Rules decide which path client traffic takes. See "Socket and SDN integration" in `docs/ARCHITECTURE.md` |
| Initial testing and demo | 2 | `experiments/functional_tests.py` T1, T2 and the manual demo in `README.md` |

## D2 (25 marks)

| Component | Marks | Where to look |
|---|---|---|
| Complete application functionality | 4 | Discovery, registry with expiry, load-aware choice, request and response, multiple servers and clients. T1, T4 |
| Dynamic SDN functionality | 6 | Reactive registration, liveness through flow idle timeout, deregistration, link-failure rerouting, rogue blocking, proxy ARP policy. T3, T5, T6, T8 |
| Challenging scenario and robustness | 4 | Server crash (T3), link failure (T5), rogue server (T6), packet loss (T7) |
| Performance evaluation and comparison | 5 | E1 to E4 against the static baseline in `controller/static_controller.py` |
| Documentation, final demo and viva | 6 | This repository's `README.md` and `docs/`. The live dashboard (`dashboard/`) makes the demo easier to follow. For the viva, each member should be able to explain the flow in `docs/ARCHITECTURE.md` and one component in depth |
