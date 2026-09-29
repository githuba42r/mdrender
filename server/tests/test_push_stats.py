# server/tests/test_push_stats.py
"""Admin push totals and per-account breakdown (design §9/§11)."""
import time

from server.app import accounts, push_store
from server.app.db import Database


def _conn(db_path):
    db = Database(db_path)
    conn = db.connect()
    db.init_schema(conn)
    return conn


def test_push_stats_and_by_account(config, db_path):
    conn = _conn(db_path)
    account_id = accounts.create_account(conn, "user@example.com", "longenough1")
    now = int(time.time())

    # A recent push with a pending file.
    push_store.create_push(conn, "p1", "Dev", account_id=account_id)
    conn.execute("UPDATE pushes SET date = ? WHERE push_id = 'p1'", (now,))
    push_store.add_file(conn, file_id="f1", push_id="p1", file_name="a",
                        file_path="", size=1, retrieval_key="k",
                        stored_path=None, created_at=now)

    # An old (40 days) push whose file is acked.
    push_store.create_push(conn, "p2", "Dev", account_id=account_id)
    conn.execute("UPDATE pushes SET date = ? WHERE push_id = 'p2'", (now - 40 * 86400,))
    push_store.add_file(conn, file_id="f2", push_id="p2", file_name="b",
                        file_path="", size=1, retrieval_key="k",
                        stored_path=None, created_at=now - 40 * 86400)
    push_store.mark_acked(conn, "f2", now)
    conn.commit()

    stats = push_store.push_stats(conn, now=now)
    assert stats["hour"] == {"total": 1, "pending": 1}
    assert stats["day"] == {"total": 1, "pending": 1}
    assert stats["month"] == {"total": 1, "pending": 1}  # p2 is outside 30 days
    assert stats["total"] == {"total": 2, "pending": 1}

    by = push_store.pushes_by_account(conn)
    assert by[0]["email"] == "user@example.com"
    assert by[0]["total"] == 2
    assert by[0]["pending"] == 1
    conn.close()
