# server/app/deployment.py
"""Deployment role detection and stable server identity (Phase B).

A server is a **master** when it can send FCM (a valid service account), a
**slave** when it cannot and must relay through a master, and may also be a
**standalone** (own FCM, not federated). Detection is advisory: an explicit
``ROLE`` config always wins. See the federated design §3.
"""
import base64
import socket
import time
import uuid

from server.app import crypto

ROLES = ("master", "slave", "standalone")


def detect_role(config, fcm_available: bool | None = None) -> str:
    """Return the effective role: explicit ``ROLE`` else by FCM availability."""
    explicit = (getattr(config, "ROLE", "") or "").strip().lower()
    if explicit in ROLES:
        return explicit
    if fcm_available is None:
        fcm_available = bool(getattr(config, "FCM_SERVER_KEY", ""))
    return "master" if fcm_available else "slave"


def get_or_create_identity(conn):
    """Return this server's stable identity row (server_id + federation keypair).

    Minted once and reused, so a slave always presents the same server_id and
    public key to its master.
    """
    row = conn.execute("SELECT * FROM server_identity WHERE id = 1").fetchone()
    if row is not None:
        return row
    server_id = str(uuid.uuid4())
    priv, pub = crypto.generate_rsa_keypair()
    conn.execute(
        "INSERT INTO server_identity"
        " (id, server_id, private_key_pem, public_key, hostname, created_at)"
        " VALUES (1, ?, ?, ?, ?, ?)",
        (server_id, crypto.private_to_pem(priv).decode(),
         base64.b64encode(crypto.public_to_spki_der(pub)).decode(),
         socket.gethostname(), int(time.time())),
    )
    conn.commit()
    return conn.execute("SELECT * FROM server_identity WHERE id = 1").fetchone()


def probe_fcm(config) -> bool:
    """Live FCM probe: mint an OAuth token from the service account.

    Confirms the credential file is present *and* valid. Never raises; any
    failure (missing/invalid file, network) means FCM is unavailable. Callers
    re-probe periodically so a later-valid credential recovers.
    """
    if not getattr(config, "FCM_SERVER_KEY", ""):
        return False
    try:
        from server.app import fcm as fcm_mod
        account = fcm_mod.load_service_account(config.FCM_SERVER_KEY)
        fcm_mod.FcmClient(account)._get_access_token()
        return True
    except Exception:  # noqa: BLE001 - a probe never crashes startup
        return False
