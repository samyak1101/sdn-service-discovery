import argparse
import collections
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app import protocol


class ServiceServer:
    def __init__(self, name, service, ip, port, bcast, adv_port, interval, key,
                 delay=0.0, advertise=True):
        self.name = name
        self.service = service
        self.ip = ip
        self.port = port
        self.bcast = bcast
        self.adv_port = adv_port
        self.interval = interval
        self.key = key
        self.delay = delay
        self.advertise = advertise
        self.stop_event = threading.Event()
        self.recent = collections.deque()
        self.lock = threading.Lock()
        self.seq = 0
        self.adverts_sent = 0
        self.served = 0
        self.pool = ThreadPoolExecutor(max_workers=8)

    def load(self):
        now = time.time()
        with self.lock:
            while self.recent and now - self.recent[0] > 5.0:
                self.recent.popleft()
            return len(self.recent)

    def advert_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind((self.ip, 0))
        while not self.stop_event.is_set():
            self.seq += 1
            payload = protocol.make_advert(
                self.name, self.service, self.ip, self.port, self.load(), self.seq, self.key)
            try:
                sock.sendto(payload, (self.bcast, self.adv_port))
                self.adverts_sent += 1
            except OSError:
                pass
            self.stop_event.wait(self.interval)
        sock.close()

    def handle(self, sock, data, addr):
        msg = protocol.parse_message(data, "req")
        if msg is None:
            return
        with self.lock:
            self.recent.append(time.time())
            self.served += 1
        if self.delay:
            time.sleep(self.delay)
        reply = protocol.make_response(msg["id"], self.name, self.load())
        try:
            sock.sendto(reply, addr)
        except OSError:
            pass

    def serve_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.ip, self.port))
        sock.settimeout(0.5)
        while not self.stop_event.is_set():
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            self.pool.submit(self.handle, sock, data, addr)
        sock.close()

    def start(self):
        threads = [threading.Thread(target=self.serve_loop, daemon=True)]
        if self.advertise:
            threads.append(threading.Thread(target=self.advert_loop, daemon=True))
        for t in threads:
            t.start()
        return threads

    def stop(self):
        self.stop_event.set()
        self.pool.shutdown(wait=False)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="UDP service server with periodic broadcast advertisements")
    p.add_argument("--name", required=True)
    p.add_argument("--service", default="echo")
    p.add_argument("--ip", required=True)
    p.add_argument("--port", type=int, default=protocol.SERVICE_PORT)
    p.add_argument("--bcast", default="10.0.0.255")
    p.add_argument("--adv-port", type=int, default=protocol.ADV_PORT)
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--key", default=protocol.DEFAULT_KEY)
    p.add_argument("--delay", type=float, default=0.0)
    p.add_argument("--no-advert", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    server = ServiceServer(args.name, args.service, args.ip, args.port, args.bcast,
                           args.adv_port, args.interval, args.key, args.delay,
                           not args.no_advert)
    server.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
