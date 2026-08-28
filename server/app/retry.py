# server/app/retry.py
import base64
import datetime
import json
import time

from server.app import crypto, envelope, fcm as fcm_mod, pairing, push_store
from server.app.store import get_or_create_server_keypair, sweep_stale_devices


def _device(conn, row):
    push = push_store.get_push_by_id(conn, row["push_id"])
    if push is None:
        return None
    return pairing.get_device_by_name(conn, push["target_device"])


def _load_server_priv(conn):
    from cryptography.hazmat.primitives import serialization
    pem, _ = get_or_create_server_keypair(conn)
    return serialization.load_pem_private_key(pem.encode(), password=None)


def _iso(now):
    return datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat()


class RetryWorker:
    def __init__(self, config, db, fcm_client):
        self.config = config
        self.db = db
        self.fcm_client = fcm_client

    def tick(self, now: float) -> list[str]:
        touched = []
        with self.db.connect() as conn:
            _, server_pk_b64 = get_or_create_server_keypair(conn)
            server_priv = _load_server_priv(conn)
            for row in push_store.get_pending_files(conn, now):
                if row["retries"] >= self.config.PUSH_RETRY_COUNT:
                    push_store.mark_exhausted(conn, row["file_id"])
                    touched.append(row["file_id"])
                    continue
                device = _device(conn, row)
                # device resolved via pushes.target_device -> devices.device_name
                if device is None:
                    push_store.mark_exhausted(conn, row["file_id"])
                    touched.append(row["file_id"])
                    continue
                payload = envelope.build_payload(
                    self.config.PUSH_PUBLIC_URL, row["push_id"], _iso(now),
                    total_files=1,
                    files=[envelope.file_entry(row["file_id"], row["file_name"],
                                               row["file_path"], row["retrieval_key"])],
                )
                device_pub = crypto.public_from_spki_der(base64.b64decode(device["public_key"]))
                env = envelope.build_envelope(server_priv, device_pub, payload)
                try:
                    self.fcm_client.send({"p": json.dumps(env)}, device["fcm_token"])
                    push_store.increment_retry(conn, row["file_id"], now,
                                               self.config.PUSH_RETRY_INTERVAL_MINUTES)
                    touched.append(row["file_id"])
                except fcm_mod.FcmError:
                    pass  # retry next tick
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
