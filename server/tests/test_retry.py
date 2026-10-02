import base64, hashlib, time

from server.app import crypto
from server.app.db import Database
from server.app.pairing import create_pairing_token, register_device
from server.app.push_store import add_file, create_push
from server.app.retry import RetryWorker
from server.app.trigger import open_trigger


class FakeFcm:
    def __init__(self):
        self.sent = []

    def send(self, data_message, fcm_token, **kwargs):
        self.sent.append((data_message, fcm_token, kwargs))


def _register(conn, name="Sunny Falcon", token="tok-1", push_key=b"\x05" * 32):
    """Register through the real pairing path so the test cannot drift from it."""
    priv, pub = crypto.generate_rsa_keypair()
    pub_b64 = base64.b64encode(crypto.public_to_spki_der(pub)).decode()
    key_b64 = base64.b64encode(push_key).decode()
    digest = hashlib.sha256(
        f"sec-1{name}{token}{pub_b64}{key_b64}".encode()
    ).digest()
    sig = base64.b64encode(crypto.sign(priv, digest)).decode()
    device_auth, _ = register_device(
        conn, device_secret="sec-1", device_name=name, fcm_token=token,
        public_key_b64=pub_b64, push_key_b64=key_b64,
        pairing_token=create_pairing_token(conn, 15), sig_b64=sig,
    )
    assert device_auth
    return push_key


def test_retry_exhausts_after_count(config, db_path):
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        create_push(conn, "p1", "Sunny Falcon")
        add_file(conn, file_id="f1", push_id="p1", file_name="a.md", file_path="",
                 size=3, retrieval_key="k1", stored_path="p1/f1/a.md",
                 created_at=time.time() - 3600)
    worker = RetryWorker(config, db, fcm_client=FakeFcm())
    for _ in range(config.PUSH_RETRY_COUNT):
        worker.tick(time.time())
    with db.connect() as conn:
        status = conn.execute("SELECT status FROM push_files WHERE file_id = 'f1'").fetchone()
    assert status["status"] == "exhausted"


def test_retry_rings_one_doorbell_per_push_not_per_file(config, db_path):
    """Three pending files in one push must produce one FCM message, not three."""
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        push_key = _register(conn)
        create_push(conn, "p1", "Sunny Falcon", challenge_key="ck-1")
        for i in range(3):
            add_file(conn, file_id=f"f{i}", push_id="p1", file_name=f"{i}.md",
                     file_path="", size=3, retrieval_key=f"k{i}",
                     stored_path="p1/f%d/%d.md" % (i, i),
                     created_at=time.time() - 3600)

    fcm = FakeFcm()
    touched = RetryWorker(config, db, fcm_client=fcm).tick(time.time())
    assert len(fcm.sent) == 1
    # Every file's retry counter still advances, so none is left to look pending.
    assert len(touched) == 3
    with db.connect() as conn:
        rows = conn.execute("SELECT retries FROM push_files").fetchall()
    assert {r["retries"] for r in rows} == {1}

    # The message opens as a doorbell for that push.
    doorbell = open_trigger(push_key, {"i": fcm.sent[0][0]["i"],
                                       "c": fcm.sent[0][0]["p"]})
    assert doorbell["push_id"] == "p1"
    assert doorbell["challenge_key"] == "ck-1"


def test_retry_rerings_the_same_doorbell_byte_for_byte(config, db_path):
    """A re-ring must be recognisable, and the manifest it leads to is rebuilt
    from live state, so an acked file can never be offered twice."""
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        _register(conn)
        create_push(conn, "p1", "Sunny Falcon", challenge_key="ck-1")
        add_file(conn, file_id="f1", push_id="p1", file_name="a.md", file_path="",
                 size=3, retrieval_key="k1", stored_path="p1/f1/a.md",
                 created_at=time.time() - 3600)

    fcm = FakeFcm()
    worker = RetryWorker(config, db, fcm_client=fcm)
    now = time.time()
    worker.tick(now)
    from server.app.push_store import reset_push_retries

    with db.connect() as conn:
        reset_push_retries(conn, "p1", now)
    worker.tick(now + 1)
    assert len(fcm.sent) == 2
    assert fcm.sent[0][0] == fcm.sent[1][0]


def test_retry_does_nothing_without_an_fcm_client_or_a_master(config, db_path):
    """No FCM key and no master to relay through => there is nothing to send with."""

    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        create_push(conn, "p1", "Sunny Falcon", challenge_key="ck-1")
        add_file(conn, file_id="f1", push_id="p1", file_name="a.md", file_path="",
                 size=3, retrieval_key="k1", stored_path="p1/f1/a.md",
                 created_at=time.time() - 3600)
    assert RetryWorker(config, db, fcm_client=None).tick(time.time()) == []
    with db.connect() as conn:
        retries = conn.execute("SELECT retries FROM push_files").fetchone()["retries"]
    assert retries == 0


def test_retry_exhausts_when_the_device_is_gone(config, db_path):
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        create_push(conn, "p1", "Ghost", challenge_key="ck-1")
        add_file(conn, file_id="f1", push_id="p1", file_name="a.md", file_path="",
                 size=3, retrieval_key="k1", stored_path="p1/f1/a.md",
                 created_at=time.time() - 3600)
    RetryWorker(config, db, fcm_client=FakeFcm()).tick(time.time())
    with db.connect() as conn:
        status = conn.execute("SELECT status FROM push_files").fetchone()["status"]
    assert status == "exhausted"
