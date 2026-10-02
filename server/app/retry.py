# server/app/retry.py
import base64
import time

from server.app import (encryption, federation_client, pairing, push_store,
                        trigger)
from server.app.deployment import detect_role, get_or_create_identity
from server.app.store import sweep_stale_devices


def _device(conn, row):
    push = push_store.get_push_by_id(conn, row["push_id"])
    if push is None:
        return None
    return pairing.get_device_by_name(conn, push["target_device"])


def _ring(conn, config, fcm_client, device, push_row, identity=None) -> None:
    """Send the push's doorbell. The IV is derived from push_id, so a re-ring is
    byte-identical to the first send.

    A slave owns no FCM project, so it relays the sealed trigger to the master
    (design §7B) instead of sending it itself.
    """
    sealed = trigger.seal_trigger(
        base64.b64decode(device["push_key"]), config.PUSH_PUBLIC_URL,
        push_row["push_id"], push_row["challenge_key"],
    )
    if fcm_client is not None:
        # High priority: the app must be able to start its download service
        # from the background (Android 12+ denies normal-priority starts).
        fcm_client.send({"p": sealed["c"], "i": sealed["i"]}, device["fcm_token"],
                        high_priority=True)
        return
    federation_client.ring_via_master(config, conn, identity, device, sealed)


class RetryWorker:
    """Re-rings the doorbell for pushes that still have unacked files.

    One trigger per push, not one per file: the phone re-requests the manifest,
    which is rebuilt from current DB state and therefore never names a file that
    has already been acked. Every pending file still gets its retry counter
    advanced, so a single push cannot be re-ringed once per file.
    """

    def __init__(self, config, db, fcm_client):
        self.config = config
        self.db = db
        self.fcm_client = fcm_client

    def tick(self, now: float) -> list[str]:
        touched = []
        rung: set[str] = set()
        with self.db.connect() as conn:
            identity = None
            if self.fcm_client is None:
                # No FCM key means either a slave (relay through the master,
                # §7B) or a server that cannot send at all. Without the relay
                # branch a slave would never re-ring, so any push whose first
                # ring failed stayed pending until someone noticed.
                if (detect_role(self.config) != "slave"
                        or federation_client.get_state(conn) is None):
                    return []
                identity = get_or_create_identity(conn)
            for row in push_store.get_pending_files(conn, now):
                if row["retries"] >= self.config.PUSH_RETRY_COUNT:
                    push_store.mark_exhausted(conn, row["file_id"])
                    touched.append(row["file_id"])
                    continue
                if row["push_id"] in rung:
                    # Already re-rung for this push on this tick.
                    push_store.increment_retry(
                        conn, row["file_id"], now,
                        self.config.PUSH_RETRY_INTERVAL_MINUTES,
                    )
                    touched.append(row["file_id"])
                    continue
                device = _device(conn, row)
                if device is None or not device["fcm_token"] or not device["push_key"]:
                    push_store.mark_exhausted(conn, row["file_id"])
                    touched.append(row["file_id"])
                    continue
                if (encryption.requires_encryption(self.config)
                        and encryption.get_sealed_cek(
                            conn, device["device_secret"]) is None):
                    # Un-negotiated push (§7b): nothing the app could open it
                    # with, so ring nothing — exhaust instead of doorbelling
                    # forever. New pushes cannot reach here: /api/push
                    # refuses them up front with 428.
                    push_store.mark_exhausted(conn, row["file_id"])
                    touched.append(row["file_id"])
                    continue
                push_row = push_store.get_push_by_id(conn, row["push_id"])
                try:
                    _ring(conn, self.config, self.fcm_client, device, push_row,
                          identity)
                    rung.add(row["push_id"])
                    push_store.increment_retry(
                        conn, row["file_id"], now,
                        self.config.PUSH_RETRY_INTERVAL_MINUTES,
                    )
                    touched.append(row["file_id"])
                except Exception:  # noqa: BLE001 - FCM/relay failure: retry next tick
                    pass
        return touched


class SweepWorker:
    def __init__(self, config, db):
        self.config = config
        self.db = db

    def tick(self) -> None:
        with self.db.connect() as conn:
            sweep_stale_devices(conn, self.config.DEVICE_TTL_DAYS)
            push_store.purge_expired_bytes(conn, self.config.PUSH_FILE_TTL_HOURS, time.time())


def run_forever(config, db, fcm_client):
    retry = RetryWorker(config, db, fcm_client)
    sweep = SweepWorker(config, db)
    while True:
        try:
            retry.tick(time.time())
            sweep.tick()
        except KeyboardInterrupt:
            break
        except SystemExit:
            break
        time.sleep(60)
