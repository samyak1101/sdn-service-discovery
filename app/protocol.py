import hashlib
import hmac
import json

ADV_PORT = 5000
SERVICE_PORT = 6000
DEFAULT_KEY = "jackfruit-shared-key"
ADVERT_FIELDS = ("name", "service", "ip", "port", "load", "seq")


def _digest(fields, key):
    body = json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


def make_advert(name, service, ip, port, load, seq, key):
    fields = {
        "type": "advert",
        "name": name,
        "service": service,
        "ip": ip,
        "port": port,
        "load": load,
        "seq": seq,
    }
    fields["sig"] = _digest(fields, key)
    return json.dumps(fields).encode()


def parse_advert(data, key=None):
    try:
        msg = json.loads(data.decode())
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(msg, dict) or msg.get("type") != "advert":
        return None
    if any(f not in msg for f in ADVERT_FIELDS):
        return None
    if not isinstance(msg["port"], int) or not isinstance(msg["load"], (int, float)):
        return None
    if key is not None:
        sig = msg.pop("sig", None)
        if not isinstance(sig, str) or not hmac.compare_digest(sig, _digest(msg, key)):
            return None
    else:
        msg.pop("sig", None)
    return msg


def make_request(req_id, service):
    return json.dumps({"type": "req", "id": req_id, "service": service}).encode()


def make_response(req_id, name, load):
    return json.dumps({"type": "resp", "id": req_id, "server": name, "load": load}).encode()


def parse_message(data, kind):
    try:
        msg = json.loads(data.decode())
    except (UnicodeDecodeError, ValueError):
        return None
    if isinstance(msg, dict) and msg.get("type") == kind and "id" in msg:
        return msg
    return None
