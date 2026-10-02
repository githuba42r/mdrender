# server/app/federation.py
"""Federation registry, signed requests, nonces, and the master→slave outbox.

Security model (design §5/§5a): a slave holds a federation RSA keypair minted at
first run; the master verifies every slave request with the slave's public key
over a canonical request digest, rejecting stale timestamps and replayed
nonces. See `docs/superpowers/specs/2026-09-29-federated-push-server-design.md`.
"""
import base64
import hashlib
import json
import sqlite3
import time
import urllib.request
import uuid

from cryptography.hazmat.primitives import serialization

from server.app import crypto
from server.app.auth import hash_secret, verify_secret

SIGNATURE_WINDOW_SECONDS = 300
NONCE_TTL_SECONDS = 600
# Cloudflare rejects urllib's default "Python-urllib/x.y" signature with error
# 1010, which breaks every callback once a server sits behind a CF-proxied
# hostname (federated.z42z.com, md.z42z.com). Send a normal UA on both legs.
USER_AGENT = "MDRender/1.0"


# ---- Signing ----------------------------------------------------------------

def _load_private(pem: str):
    return serialization.load_pem_private_key(pem.encode(), password=None)


def new_challenge() -> str:
    return uuid.uuid4().hex


def sign_bytes(private_pem: str, data: bytes) -> str:
    """Base64 signature over arbitrary bytes with this server's key."""
    return base64.b64encode(crypto.sign(_load_private(private_pem), data)).decode()


def canonical_request(method: str, path: str, timestamp: int | str,
                      nonce: str, body: bytes) -> bytes:
    digest = hashlib.sha256(body or b"").hexdigest()
    return "\n".join([method.upper(), path, str(timestamp), nonce, digest]).encode()


def sign_request(private_pem: str, method: str, path: str, body: bytes = b"") -> dict:
    """Return the federation signature headers for a request."""
    ts = int(time.time())
    nonce = uuid.uuid4().hex
    sig = crypto.sign(_load_private(private_pem),
                      canonical_request(method, path, ts, nonce, body))
    return {
        "X-Federation-Timestamp": str(ts),
        "X-Federation-Nonce": nonce,
        "X-Federation-Signature": base64.b64encode(sig).decode(),
    }


def verify_request(conn, server_row, method, path, body, headers) -> bool:
    """Verify a signed slave→master request and burn its nonce.

    Returns False on a missing header, a stale timestamp, a bad signature, or a
    replayed nonce.
    """
    ts = headers.get("X-Federation-Timestamp")
    nonce = headers.get("X-Federation-Nonce")
    sig = headers.get("X-Federation-Signature")
    if not ts or not nonce or not sig:
        return False
    try:
        ts_i = int(ts)
        sig_bytes = base64.b64decode(sig)
        pub = crypto.public_from_spki_der(base64.b64decode(server_row["public_key"]))
    except Exception:  # noqa: BLE001 - malformed input is just a failure
        return False
    if abs(time.time() - ts_i) > SIGNATURE_WINDOW_SECONDS:
        return False
    if not crypto.verify(pub, canonical_request(method, path, ts, nonce, body), sig_bytes):
        return False
    return mark_nonce(conn, server_row["server_id"], nonce)


def mark_nonce(conn, server_id: str, nonce: str, now: int | None = None) -> bool:
    """Record a nonce; False if it was already seen (a replay)."""
    now = int(now or time.time())
    conn.execute("DELETE FROM federated_nonces WHERE expires_at < ?", (now,))
    try:
        conn.execute(
            "INSERT INTO federated_nonces (server_id, nonce, expires_at) VALUES (?, ?, ?)",
            (server_id, nonce, now + NONCE_TTL_SECONDS))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


# ---- Registry ---------------------------------------------------------------

def get_server(conn, server_id):
    return conn.execute("SELECT * FROM federated_servers WHERE server_id = ?",
                        (server_id,)).fetchone()


def list_servers(conn):
    return conn.execute("SELECT * FROM federated_servers ORDER BY created_at").fetchall()


def register_active(conn, *, server_id, hostname, base_url, public_key_b64,
                    plan_id=None):
    """Record a verified slave and issue its federation secret.

    Returns the plaintext secret (only the hash is stored). The caller returns it
    to the slave exactly once. ``plan_id`` is the billing plan the server runs
    under (design §7); the caller resolves it - an already-assigned plan is
    carried across re-enrolments, otherwise the master's default applies.
    """
    secret = uuid.uuid4().hex
    conn.execute(
        "INSERT OR REPLACE INTO federated_servers"
        " (server_id, hostname, base_url, public_key, secret_hash, status,"
        "  created_at, last_seen, plan_id)"
        " VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)",
        (server_id, hostname, base_url, public_key_b64, hash_secret(secret),
         int(time.time()), int(time.time()), plan_id),
    )
    conn.commit()
    return secret


def set_plan(conn, server_id, plan_id):
    """Assign (plan_id) or clear (None) the billing plan of a slave server."""
    conn.execute("UPDATE federated_servers SET plan_id = ? WHERE server_id = ?",
                 (plan_id, server_id))
    conn.commit()


def check_bearer(conn, token: str | None):
    """Resolve a ``server_id.secret`` bearer token to an active slave row."""
    if not token or "." not in token:
        return None
    server_id, secret = token.split(".", 1)
    row = get_server(conn, server_id)
    if row is None or row["status"] in ("revoked", "banned"):
        return None
    if not row["secret_hash"] or not verify_secret(secret, row["secret_hash"]):
        return None
    return row


def touch_seen(conn, server_id, *, up: bool = True) -> None:
    now = int(time.time())
    if up:
        conn.execute("UPDATE federated_servers SET last_seen = ?, status = 'active',"
                     " down_since = NULL WHERE server_id = ?", (now, server_id))
    else:
        conn.execute("UPDATE federated_servers SET down_since = ? WHERE server_id = ?",
                     (now, server_id))
    conn.commit()


def set_status(conn, server_id, status) -> None:
    conn.execute("UPDATE federated_servers SET status = ? WHERE server_id = ?",
                 (status, server_id))
    conn.commit()


def revoke_server(conn, server_id) -> None:
    conn.execute("UPDATE federated_servers SET status = 'revoked', secret_hash = NULL"
                 " WHERE server_id = ?", (server_id,))
    conn.commit()


def delete_server(conn, server_id) -> None:
    conn.execute("DELETE FROM federated_servers WHERE server_id = ?", (server_id,))
    conn.execute("DELETE FROM federated_outbox WHERE server_id = ?", (server_id,))
    conn.commit()


# ---- Outbox (master → slave) ------------------------------------------------

def enqueue(conn, server_id: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO federated_outbox (server_id, payload, created_at, next_retry_at)"
        " VALUES (?, ?, ?, ?)",
        (server_id, json.dumps(payload), int(time.time()), int(time.time())))
    conn.commit()


def pending_outbox(conn, server_id: str):
    return conn.execute(
        "SELECT id, payload FROM federated_outbox"
        " WHERE server_id = ? AND acked_at IS NULL ORDER BY id", (server_id,)).fetchall()


def mark_outbox_sent(conn, ids) -> None:
    if not ids:
        return
    conn.executemany("UPDATE federated_outbox SET acked_at = ? WHERE id = ?",
                     [(int(time.time()), i) for i in ids])
    conn.commit()


# ---- Slave callback (master → slave) ----------------------------------------

def verify_slave_callback(base_url: str, challenge: str, *, timeout: int = 10) -> str:
    """Ask the slave to sign *challenge*; return its signature.

    POSTs to the slave's federation verify endpoint. Raises on transport errors;
    the caller treats any failure as "not verified".
    """
    url = base_url.rstrip("/") + "/api/federation/verify"
    data = json.dumps({"challenge": challenge}).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read()).get("signature", "")


def verify_callback_signature(public_key_b64: str, challenge: str, signature_b64: str) -> bool:
    try:
        pub = crypto.public_from_spki_der(base64.b64decode(public_key_b64))
        return crypto.verify(pub, challenge.encode(), base64.b64decode(signature_b64))
    except Exception:  # noqa: BLE001
        return False


# ---- Operator consent handshake (design §5) --------------------------------
#
# A slave never enrols by itself. Its operator is sent to the master, which
# shows what is being accepted (billing, terms); approving mints a single-use
# code bound to that exact slave, which the slave exchanges during enrolment.
# Enrolling without a code only works when the master already consented to
# this server_id with the same base URL and key and has not revoked it.

CONNECT_TTL_SECONDS = 600
CONNECT_CODE_TTL_SECONDS = 600
# Statuses that count as previously consented; revoked/banned/deactivated and
# changed identity all require a fresh approval.
_CONSENTED_STATUSES = ("active", "down")


def connect_canonical(server_id, hostname, base_url, public_key_b64, state,
                      ts) -> bytes:
    """Bytes a slave signs to request a consent page (browser GET, no body)."""
    return "\n".join(["connect", server_id, hostname, base_url, public_key_b64,
                      state, str(ts)]).encode()


def verify_connect_signature(public_key_b64: str, signature_b64: str, *,
                             server_id: str, hostname: str, base_url: str,
                             state: str, ts) -> bool:
    # verify_callback_signature takes the challenge as str (it encodes).
    return verify_callback_signature(
        public_key_b64,
        connect_canonical(server_id, hostname, base_url, public_key_b64,
                          state, ts).decode(),
        signature_b64)


def open_connect(conn, *, server_id, hostname, base_url, public_key_b64,
                 state, now=None) -> str:
    """Record a signed connect request; returns the id the browser is shown."""
    now = int(now if now is not None else time.time())
    connect_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO federation_connects"
        " (id, server_id, hostname, base_url, public_key, state, created_at,"
        "  expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (connect_id, server_id, hostname, base_url, public_key_b64, state,
         now, now + CONNECT_TTL_SECONDS))
    conn.commit()
    return connect_id


def get_connect(conn, connect_id):
    return conn.execute("SELECT * FROM federation_connects WHERE id = ?",
                        (connect_id,)).fetchone()


def approve_connect(conn, connect_id, now=None) -> str | None:
    """Issue the single-use consent code; None if it cannot be approved."""
    now = int(now if now is not None else time.time())
    row = get_connect(conn, connect_id)
    if (row is None or row["consumed_at"] or row["declined_at"]
            or row["expires_at"] < now or row["code"]):
        return None
    code = uuid.uuid4().hex
    conn.execute("UPDATE federation_connects SET code = ?, expires_at = ?"
                 " WHERE id = ?",
                 (code, now + CONNECT_CODE_TTL_SECONDS, connect_id))
    conn.commit()
    return code


def decline_connect(conn, connect_id, now=None) -> bool:
    now = int(now if now is not None else time.time())
    cur = conn.execute(
        "UPDATE federation_connects SET declined_at = ? WHERE id = ?"
        " AND consumed_at IS NULL AND declined_at IS NULL",
        (now, connect_id))
    conn.commit()
    return cur.rowcount == 1


def find_connect_by_code(conn, code, now=None):
    """The live consent record for *code*, or None (expired/used/declined)."""
    if not code:
        return None
    now = int(now if now is not None else time.time())
    row = conn.execute("SELECT * FROM federation_connects WHERE code = ?",
                       (code,)).fetchone()
    if row is None or row["consumed_at"] or row["declined_at"]:
        return None
    if row["expires_at"] < now:
        return None
    return row


def mark_connect_consumed(conn, code, now=None) -> bool:
    """Consume a code atomically, so it can only ever authorise one enrolment."""
    now = int(now if now is not None else time.time())
    cur = conn.execute(
        "UPDATE federation_connects SET consumed_at = ? WHERE code = ?"
        " AND consumed_at IS NULL AND declined_at IS NULL",
        (now, code))
    conn.commit()
    return cur.rowcount == 1


def consent_not_required(conn, *, server_id, base_url, public_key_b64) -> bool:
    """True when this exact slave was consented to earlier and not revoked.

    Identity is the triple (server_id, base_url, public_key): any change -
    a re-keyed slave, a new URL behind the same id - needs approval again.
    """
    row = get_server(conn, server_id)
    return (row is not None and row["status"] in _CONSENTED_STATUSES
            and row["base_url"] == base_url
            and row["public_key"] == public_key_b64)


def probe_server(server_row, *, timeout: int = 10) -> bool:
    """Master→slave liveness probe: challenge the slave and verify the reply."""
    challenge = new_challenge()
    try:
        url = server_row["base_url"].rstrip("/") + "/api/federation/probe"
        data = json.dumps({"challenge": challenge}).encode()
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            reply = json.loads(resp.read())
    except Exception:  # noqa: BLE001 - unreachable/failed == down
        return False
    return verify_callback_signature(server_row["public_key"], challenge,
                                     reply.get("signature", ""))


def sweep_liveness(conn, *, probe=probe_server, threshold: int = 3,
                   now: int | None = None) -> list[str]:
    """Probe active/down slaves; mark down after `threshold` failed probes.

    Returns the server ids newly marked down. Injectable `probe` keeps this
    testable without a live slave.
    """
    now = int(now or time.time())
    newly_down = []
    rows = conn.execute(
        "SELECT * FROM federated_servers WHERE status IN ('active', 'down')").fetchall()
    for row in rows:
        if probe(row):
            conn.execute(
                "UPDATE federated_servers SET last_probe = ?, status = 'active',"
                " down_since = NULL, probe_failures = 0 WHERE server_id = ?",
                (now, row["server_id"]))
            continue
        fails = (row["probe_failures"] or 0) + 1
        if fails >= threshold and row["status"] != "down":
            newly_down.append(row["server_id"])
            conn.execute(
                "UPDATE federated_servers SET last_probe = ?, status = 'down',"
                " down_since = ?, probe_failures = ? WHERE server_id = ?",
                (now, now, fails, row["server_id"]))
        else:
            conn.execute(
                "UPDATE federated_servers SET last_probe = ?, probe_failures = ?"
                " WHERE server_id = ?", (now, fails, row["server_id"]))
    conn.commit()
    return newly_down
