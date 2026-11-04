import argparse
import json
import socket
import sys
import threading
import time

from app import protocol


class Registry:
    def __init__(self, ttl):
        self.ttl = ttl
        self.lock = threading.Lock()
        self.entries = {}

    def update(self, advert, now):
        key = (advert["ip"], advert["port"])
        with self.lock:
            is_new = key not in self.entries
            self.entries[key] = {
                "name": advert["name"],
                "service": advert["service"],
                "load": advert["load"],
                "last": now,
            }
        return is_new

    def expire(self, now):
        with self.lock:
            stale = [k for k, v in self.entries.items() if now - v["last"] > self.ttl]
            return [(k, self.entries.pop(k)) for k in stale]

    def choose(self, service):
        with self.lock:
            options = [(v["load"], k, v["name"]) for k, v in self.entries.items()
                       if v["service"] == service]
        if not options:
            return None
        options.sort(key=lambda o: (o[0], o[1]))
        return options[0][1], options[0][2]

    def lookup(self, key):
        with self.lock:
            entry = self.entries.get(key)
            return entry["name"] if entry else None

    def names(self):
        with self.lock:
            return sorted(v["name"] for v in self.entries.values())


class DiscoveryClient:
    def __init__(self, name, service, adv_port, ttl, key, verify, log_path,
                 static=None, timeout=0.5, sticky=True):
        self.name = name
        self.service = service
        self.adv_port = adv_port
        self.key = key if verify else None
        self.static = static
        self.timeout = timeout
        self.sticky = sticky
        self.current = None
        self.registry = Registry(ttl)
        self.stop_event = threading.Event()
        self.log_lock = threading.Lock()
        self.log_file = open(log_path, "a", buffering=1) if log_path else None
        self.sent = 0
        self.ok = 0

    def log(self, event, **fields):
        record = {"t": round(time.time(), 4), "client": self.name, "event": event}
        record.update(fields)
        line = json.dumps(record)
        with self.log_lock:
            if self.log_file:
                self.log_file.write(line + "\n")

    def listen_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(("", self.adv_port))
        sock.settimeout(0.5)
        while not self.stop_event.is_set():
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            advert = protocol.parse_advert(data, self.key)
            if advert is None:
                self.log("advert_rejected", src=addr[0])
                continue
            if self.registry.update(advert, time.time()):
                self.log("discovered", server=advert["name"], ip=advert["ip"])
        sock.close()

    def reap_loop(self):
        while not self.stop_event.wait(0.2):
            for key, entry in self.registry.expire(time.time()):
                self.log("expired", server=entry["name"], ip=key[0])

    def request_loop(self, count, interval, duration):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self.timeout)
        started = time.time()
        req_id = 0
        while not self.stop_event.is_set():
            if count and req_id >= count:
                break
            if duration and time.time() - started >= duration:
                break
            req_id += 1
            self.sent += 1
            target = self.pick()
            if target is None:
                self.log("request_failed", id=req_id, reason="no_server")
            else:
                (ip, port), server = target
                self.exchange(sock, req_id, ip, port, server)
            self.stop_event.wait(interval)
        sock.close()

    def pick(self):
        if self.static:
            return self.static, "static"
        if self.sticky and self.current is not None:
            name = self.registry.lookup(self.current)
            if name is not None:
                return self.current, name
        choice = self.registry.choose(self.service)
        self.current = choice[0] if choice else None
        return choice

    def exchange(self, sock, req_id, ip, port, server):
        sent_at = time.time()
        try:
            sock.sendto(protocol.make_request(req_id, self.service), (ip, port))
        except OSError as exc:
            self.log("request_failed", id=req_id, server=server, reason=str(exc))
            return
        deadline = sent_at + self.timeout
        while time.time() < deadline:
            sock.settimeout(max(deadline - time.time(), 0.01))
            try:
                data, _ = sock.recvfrom(2048)
            except socket.timeout:
                break
            except OSError:
                break
            msg = protocol.parse_message(data, "resp")
            if msg and msg["id"] == req_id:
                self.ok += 1
                self.log("request_ok", id=req_id, server=msg.get("server", server),
                         rtt_ms=round((time.time() - sent_at) * 1000, 2))
                return
        self.log("request_failed", id=req_id, server=server, reason="timeout")

    def run(self, count, interval, duration):
        if not self.static:
            threading.Thread(target=self.listen_loop, daemon=True).start()
            threading.Thread(target=self.reap_loop, daemon=True).start()
        try:
            self.request_loop(count, interval, duration)
        except KeyboardInterrupt:
            pass
        self.stop_event.set()
        self.log("summary", sent=self.sent, ok=self.ok)

    def close(self):
        self.stop_event.set()
        if self.log_file:
            self.log_file.close()


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="UDP service client with live server registry")
    p.add_argument("--name", required=True)
    p.add_argument("--service", default="echo")
    p.add_argument("--adv-port", type=int, default=protocol.ADV_PORT)
    p.add_argument("--ttl", type=float, default=6.0)
    p.add_argument("--key", default=protocol.DEFAULT_KEY)
    p.add_argument("--no-verify", action="store_true")
    p.add_argument("--count", type=int, default=0)
    p.add_argument("--duration", type=float, default=0.0)
    p.add_argument("--interval", type=float, default=0.5)
    p.add_argument("--timeout", type=float, default=0.5)
    p.add_argument("--static", default=None, help="fixed server as ip:port, disables discovery")
    p.add_argument("--no-sticky", action="store_true", help="re-pick the least loaded server on every request")
    p.add_argument("--log", default=None)
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    static = None
    if args.static:
        host, port = args.static.rsplit(":", 1)
        static = (host, int(port))
    client = DiscoveryClient(args.name, args.service, args.adv_port, args.ttl, args.key,
                             not args.no_verify, args.log, static, args.timeout,
                             not args.no_sticky)
    client.run(args.count, args.interval, args.duration)
    print(json.dumps({"client": args.name, "sent": client.sent, "ok": client.ok}))
    client.close()


if __name__ == "__main__":
    main()
