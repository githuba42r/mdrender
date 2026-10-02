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
import urllib.parse

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


def public_hostname(config) -> str:
    """The name this server is known by to everyone else (design §5).

    ``SERVER_HOSTNAME`` wins; otherwise the host of ``PUSH_PUBLIC_URL`` - the
    URL clients pair with, so it is the name they already trust. Only a server
    with neither falls back to the container hostname, which is an id nobody
    can recognise in a master's server list.
    """
    explicit = (getattr(config, "SERVER_HOSTNAME", "") or "").strip()
    if explicit:
        return explicit
    host = urllib.parse.urlparse(
        getattr(config, "PUSH_PUBLIC_URL", "") or "").hostname
    return host or socket.gethostname()


def get_or_create_identity(conn, hostname: str | None = None):
    """Return this server's stable identity row (server_id + federation keypair).

    Minted once and reused, so a slave always presents the same server_id and
    public key to its master. When *hostname* is given and the stored name
    differs (a container was recreated, or the operator set SERVER_HOSTNAME),
    the row is corrected in place - server_id and keys never change.
    """
    row = conn.execute("SELECT * FROM server_identity WHERE id = 1").fetchone()
    if row is None:
        server_id = str(uuid.uuid4())
        priv, pub = crypto.generate_rsa_keypair()
        conn.execute(
            "INSERT INTO server_identity"
            " (id, server_id, private_key_pem, public_key, hostname, created_at)"
            " VALUES (1, ?, ?, ?, ?, ?)",
            (server_id, crypto.private_to_pem(priv).decode(),
             base64.b64encode(crypto.public_to_spki_der(pub)).decode(),
             hostname or socket.gethostname(), int(time.time())),
        )
        conn.commit()
        return conn.execute("SELECT * FROM server_identity WHERE id = 1").fetchone()
    if hostname and row["hostname"] != hostname:
        conn.execute("UPDATE server_identity SET hostname = ? WHERE id = 1",
                     (hostname,))
        conn.commit()
        row = conn.execute("SELECT * FROM server_identity WHERE id = 1").fetchone()
    return row


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
