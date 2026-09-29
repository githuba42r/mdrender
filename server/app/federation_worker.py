# server/app/federation_worker.py
"""Background federation maintenance.

- **Slave**: enrol with the master on first tick, then send periodic signed
  heartbeats (which also flush the master's outbox).
- **Master**: periodically probe slaves and mark them down after repeated
  failures.
"""
import time

from server.app import federation, federation_client
from server.app.deployment import detect_role, get_or_create_identity


def run_forever(config, db, interval: int = 60) -> None:
    while True:
        try:
            tick(config, db)
        except Exception:  # noqa: BLE001 - a maintenance tick must never die
            pass
        time.sleep(interval)


def tick(config, db) -> None:
    with db.connect() as conn:
        identity = get_or_create_identity(conn)
        role = detect_role(config, bool(getattr(config, "FCM_SERVER_KEY", "")))
        if role == "slave":
            if not getattr(config, "MASTER_URL", ""):
                return
            if federation_client.get_state(conn) is None:
                try:
                    federation_client.enrol(config, conn, identity)
                except Exception:  # noqa: BLE001 - retry next tick
                    return
            try:
                federation_client.send_heartbeat(config, conn, identity)
            except Exception:  # noqa: BLE001
                pass
        elif role == "master":
            federation.sweep_liveness(conn)
