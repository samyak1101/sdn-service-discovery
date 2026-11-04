# Architecture

## Components

**Server (`app/server.py`).** Binds a UDP socket on the service port and handles each
request on a worker thread pool. A second thread broadcasts an advert every interval to the
subnet broadcast address on UDP port 5000. The advert is JSON with the service name, IP,
port, current load and a sequence number, signed with HMAC-SHA256.

**Client (`app/client.py`).** A receiver thread listens on port 5000 and updates a registry.
Every entry carries a last-seen time. A reaper thread removes entries older than the TTL. A
lock protects the registry because three threads touch it. The request loop picks a server
(least loaded on first choice, then sticky), sends a UDP request, and waits for the reply.

**Controller (`controller/sd_controller.py`).** A Ryu OpenFlow 1.3 application. It
learns the topology from Ryu's LLDP discovery, registers servers from their adverts, and
installs flows.

**Switches.** Open vSwitch instances. They only forward using flow tables.

## Message formats

```
advert:   {"type":"advert","name":"srv1","service":"echo","ip":"10.0.0.11",
           "port":6000,"load":0,"seq":17,"sig":"<hmac-sha256 hex>"}
request:  {"type":"req","id":42,"service":"echo"}
response: {"type":"resp","id":42,"server":"srv1","load":3}
```

## The mechanism

1. **First advert.** A server's first advert matches no rule on its switch. The switch sends a
   packet-in to the controller.
2. **Validation.** The controller parses Ethernet, IPv4, UDP and the JSON payload. It checks
   the signature and that the IP inside the advert equals the IP in the packet header. A
   failure installs a quarantine rule for that host (priority 300, drops all traffic from
   its MAC on that port for 30 s) and logs `rogue_blocked`.
3. **Registration.** A valid advert registers the server (name, IP, MAC, switch, port) and
   gives it an id.
4. **Advert tree.** The controller computes a BFS spanning tree rooted at the server's
   switch and installs one flow per switch. Each rule matches the server's MAC, broadcast
   destination and UDP port 5000, and outputs to the child switches on the tree plus every
   host-facing port. A tree has no loops, so the diamond topology is safe from broadcast
   storms. The first packet is sent on by packet-out.
5. **Steady state.** Later adverts match the rules and never reach the controller.
6. **Proxy ARP.** ARP requests go to the controller, which answers for registered servers and
   for hosts that servers ask about. A host that is not registered gets no answer.
7. **Data path.** The first request packet from a client causes a packet-in. The controller
   installs shortest-path rules in both directions (match on destination MAC and IP).
   Replies skip the controller.

## Dynamic behaviour

| Event | Detection | Controller reaction |
|---|---|---|
| Server stops | Advert flow on its switch idles out after 6 s and the switch sends flow-removed | Deregister, delete every flow carrying the server's cookie |
| New server | First advert causes packet-in | Register, install tree |
| Server moves | Advert arrives on a different switch or port, so no rule matches | Re-register, rebuild tree and paths |
| Link fails | Ryu topology reports link delete | Recompute trees and paths for every server, reinstall, so traffic uses the other branch |
| Link returns | Ryu topology reports link add | Rebuild again |
| Rogue advert | Signature or address check fails | Quarantine rule, nothing delivered to clients |
| Unregistered host contacted | No ARP answer, no path rule | Traffic is dropped |

Flows carry a cookie of `(server id << 8) | kind`, so one delete with a cookie mask removes
everything belonging to a server.

## Why UDP broadcast

Broadcast needs no connection and no knowledge of who is listening, so a server announces
itself to everyone at once. Adverts repeat, so a lost one is replaced by the next.

## Baseline for comparison

`controller/static_controller.py` installs fixed forwarding rules proactively along the s2
path and never changes them. Its clients take the server address as a command-line
argument. A crashed server or a failed link therefore means an outage until someone edits
configuration.

## Socket and SDN integration

- Application events change network rules: a first advert installs the advert tree, a
  request installs the path, a missing advert removes everything.
- Network state changes application behaviour: the flow rules decide which path client traffic
  takes, which clients receive adverts at all (a quarantined rogue never does), and which
  hosts are reachable.
