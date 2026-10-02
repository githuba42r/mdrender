# server/app/federation_client.py
"""Slave-side federation client: enrol with a master, heartbeat, restart ping.

The slave presents its stable `server_id` + public key and the master verifies
it with a signed callback before issuing a secret. All subsequent calls are
signed with the slave keypair (design §5a).
"""
import json
import time
import urllib.request

from server.app import federation


def get_state(conn):
    return conn.execute("SELECT * FROM federation_client WHERE id = 1").fetchone()


def save_registration(conn, master_url, server_secret) -> None:
    conn.execute(
        "INSERT INTO federation_client (id, master_url, server_secret, status,"
        " last_heartbeat) VALUES (1, ?, ?, 'active', ?)"
        " ON CONFLICT(id) DO UPDATE SET master_url = excluded.master_url,"
        " server_secret = excluded.server_secret, status = 'active',"
        " last_heartbeat = excluded.last_heartbeat",
        (master_url.rstrip("/"), server_secret, int(time.time())))
    conn.commit()


def clear(conn) -> None:
    conn.execute("DELETE FROM federation_client WHERE id = 1")
    conn.commit()


def _post(url: str, body: bytes, headers: dict | None = None, timeout: int = 15):
    merged = {"Content-Type": "application/json", "User-Agent": federation.USER_AGENT}
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, data=body, method="POST", headers=merged)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def enrol(config, conn, identity, *, master_url=None, code=None):
    """Register this slave with its master; persist the secret.

    `master_url` overrides the configured MASTER_URL (an operator-chosen
    master); `code` is the single-use operator consent code that master minted
    when an admin approved this slave on /federation/connect (design §5).
    """
    master = ((master_url if master_url is not None
               else getattr(config, "MASTER_URL", "")) or "").rstrip("/")
    if not master:
        raise ValueError("no master URL is configured")
    payload = {
        "server_id": identity["server_id"],
        "hostname": identity["hostname"],
        "base_url": config.PUSH_PUBLIC_URL,
        "public_key": identity["public_key"],
    }
    if code:
        payload["code"] = code
    body = json.dumps(payload).encode()
    reply = _post(f"{master}/api/federation/enrol", body)
    save_registration(conn, master, reply["server_secret"])
    return reply


def send_heartbeat(config, conn, identity, path="/api/federation/heartbeat"):
    """Signed heartbeat (or ping). Flushes any queued master→slave messages."""
    state = get_state(conn)
    if state is None:
        raise ValueError("not registered with a master")
    headers = federation.sign_request(identity["private_key_pem"], "POST", path, b"")
    headers["Authorization"] = f"Bearer {identity['server_id']}.{state['server_secret']}"
    reply = _post(state["master_url"] + path, b"", headers=headers)
    conn.execute("UPDATE federation_client SET status = 'active', last_heartbeat = ?"
                 " WHERE id = 1", (int(time.time()),))
    conn.commit()
    return reply


def ping(config, conn, identity):
    """Announce 'back online' (e.g. on restart)."""
    return send_heartbeat(config, conn, identity, path="/api/federation/ping")


def _signed_request(conn, identity, method, path, body, timeout=15):
    state = get_state(conn)
    if state is None:
        raise ValueError("not registered with a master")
    headers = federation.sign_request(identity["private_key_pem"], method, path, body)
    headers["Authorization"] = f"Bearer {identity['server_id']}.{state['server_secret']}"
    headers["Content-Type"] = "application/json"
    headers["User-Agent"] = federation.USER_AGENT
    req = urllib.request.Request(state["master_url"] + path, data=body,
                                 method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def sync_device(config, conn, identity, account_id, device_id, fcm_token, *,
                delete=False):
    """Push a device's no-PII routing tuple to the master (design §6)."""
    path = f"/api/federation/accounts/{account_id}/devices/{device_id}"
    if delete:
        return _signed_request(conn, identity, "DELETE", path, b"")
    return _signed_request(conn, identity, "PUT", path,
                           json.dumps({"fcm_token": fcm_token}).encode())


def ring_via_master(config, conn, identity, device, sealed):
    """Ask the master to send a sealed doorbell (design §7B).

    The no-PII routing tuple is (re)published first (design §6): registration-
    time sync can fail, and a device paired before this code existed has no row
    on the master at all - without it the master answers "device not found" and
    the push stays pending forever.
    """
    device = dict(device)
    account_id = device.get("account_id")
    if not account_id:
        raise ValueError("device is not bound to an account")
    try:
        sync_device(config, conn, identity, account_id,
                    device["device_secret"], device.get("fcm_token") or "")
    except Exception:  # noqa: BLE001 - the ring below surfaces a real failure
        pass
    body = json.dumps({"account_id": account_id,
                       "device_id": device["device_secret"],
                       "sealed": sealed}).encode()
    return _signed_request(conn, identity, "POST", "/api/federation/doorbell", body)


def fetch_plan(config, conn, identity, *, timeout=4):
    """Ask the master which billing plan this server runs under (design §7).

    Signed with the slave keypair like every other call; returns the whoami
    reply, whose `plan` key is null when the master has no plan for us.
    """
    return _signed_request(conn, identity, "GET", "/api/federation/whoami",
                           b"", timeout=timeout)


def _notify_disconnect(conn, identity) -> None:
    """Ask the master to remove this server's row (raises when unreachable)."""
    _signed_request(conn, identity, "POST", "/api/federation/disconnect", b"")


def disconnect(config, conn, identity) -> bool:
    """Terminate the master connection: notify it over the signed link, then
    drop the local registration. The local state is cleared even when the
    master cannot be reached (the caller reports `notified`), so the operator
    is never stranded; a lingering master row can be removed from the master's
    Federation page or simply stops being fed once heartbeats cease.
    """
    if get_state(conn) is None:
        return False
    try:
        _notify_disconnect(conn, identity)
        notified = True
    except Exception:  # noqa: BLE001 - master unreachable: clear locally anyway
        notified = False
    clear(conn)
    return notified
