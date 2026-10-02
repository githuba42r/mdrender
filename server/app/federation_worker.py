# server/app/federation_worker.py
"""Background federation maintenance.

- **Slave**: send periodic signed heartbeats (which also flush the master's
  outbox). It never enrols by itself: registration is an operator action taken
  from the /federation page (consent handshake, design §5).
- **Master**: periodically probe slaves and mark them down after repeated
  failures.
"""
import time
import urllib.error

from server.app import billing, federation, federation_client, storage
from server.app.deployment import (detect_role, get_or_create_identity,
                                   public_hostname)


def run_forever(config, db, interval: int = 60) -> None:
    while True:
        try:
            tick(config, db)
        except Exception:  # noqa: BLE001 - a maintenance tick must never die
            pass
        time.sleep(interval)


def tick(config, db) -> None:
    with db.connect() as conn:
        try:
            storage.purge_expired(conn, config)  # age/quota hygiene
        except Exception:  # noqa: BLE001
            pass
        if bool(getattr(config, "BILLING_ENFORCEMENT", False)):
            try:
                billing.bill_storage(conn, config)
                billing.bill_messages(conn, config)
            except Exception:  # noqa: BLE001
                pass
        identity = get_or_create_identity(conn, hostname=public_hostname(config))
        role = detect_role(config, bool(getattr(config, "FCM_SERVER_KEY", "")))
        if role == "slave":
            if federation_client.get_state(conn) is None:
                return  # never auto-enrol: the operator Connects from /federation
            try:
                federation_client.send_heartbeat(config, conn, identity)
            except urllib.error.HTTPError as exc:
                # 401/403 means the master no longer knows this slave (its DB
                # was reset, or we were revoked): drop the registration so the
                # operator can run the consent handshake again. Anything else -
                # master down, network - keeps the registration we have.
                if exc.code in (401, 403):
                    federation_client.clear(conn)
            except Exception:  # noqa: BLE001
                pass
        elif role == "master":
            federation.sweep_liveness(conn)
