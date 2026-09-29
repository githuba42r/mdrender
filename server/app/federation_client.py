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
    merged = {"Content-Type": "application/json"}
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, data=body, method="POST", headers=merged)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def enrol(config, conn, identity):
    """Register this slave with the configured master; persist the secret."""
    master = (getattr(config, "MASTER_URL", "") or "").rstrip("/")
    if not master:
        raise ValueError("MASTER_URL is not configured")
    body = json.dumps({
        "server_id": identity["server_id"],
        "hostname": identity["hostname"],
        "base_url": config.PUSH_PUBLIC_URL,
        "public_key": identity["public_key"],
    }).encode()
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
