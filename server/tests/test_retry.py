import base64, hashlib, time

from server.app.db import Database
from server.app.push_store import add_file, create_push, get_push_files
from server.app.retry import RetryWorker, SweepWorker


class FakeFcm:
    def __init__(self):
        self.sent = []

    def send(self, data_message, fcm_token):
        self.sent.append((data_message, fcm_token))


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
