from server.app.db import Database
from server.app.push_store import (
    add_file, create_push, get_push_files, get_pending_files,
    get_unacked_files, get_push_by_id, list_pushes, mark_acked,
)


def test_push_file_lifecycle(config, db_path):
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        create_push(conn, "push-1", "Sunny Falcon", challenge_key="ck-1")
        add_file(conn, file_id="f1", push_id="push-1", file_name="notes.md",
                 file_path="Docs", size=100, retrieval_key="k1",
                 stored_path="push-1/f1/notes.md", created_at=1000)
        assert len(get_push_files(conn, "push-1")) == 1
        assert len(get_pending_files(conn, 2000)) == 1
        assert mark_acked(conn, "f1", 2000)
        assert len(get_pending_files(conn, 2000)) == 0
        pushes = list_pushes(conn)
        assert pushes[0]["file_count"] == 1 and pushes[0]["acked_count"] == 1


def test_create_push_records_the_challenge_key(config, db_path):
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        create_push(conn, "push-1", "Sunny Falcon", challenge_key="ck-secret")
        assert get_push_by_id(conn, "push-1")["challenge_key"] == "ck-secret"
        assert get_push_by_id(conn, "nope") is None


def test_manifest_source_is_unacked_files_only(config, db_path):
    """The manifest is built from live state, so a re-ringed doorbell can never
    hand the phone a file it already acknowledged."""
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        create_push(conn, "push-1", "Sunny Falcon", challenge_key="ck-1")
        for i in (1, 2, 3):
            add_file(conn, file_id=f"f{i}", push_id="push-1",
                     file_name=f"{i}.md", file_path="", size=10 * i,
                     retrieval_key=f"k{i}", stored_path=None, created_at=1000 + i)
        assert [r["file_id"] for r in get_unacked_files(conn, "push-1")] == [
            "f1", "f2", "f3"
        ]
        mark_acked(conn, "f2", 2000)
        assert [r["file_id"] for r in get_unacked_files(conn, "push-1")] == [
            "f1", "f3"
        ]
        # Other pushes are not included.
        create_push(conn, "push-2", "Sunny Falcon", challenge_key="ck-2")
        add_file(conn, file_id="f9", push_id="push-2", file_name="9.md",
                 file_path="", size=9, retrieval_key="k9", stored_path=None,
                 created_at=1000)
        assert "f9" not in [r["file_id"] for r in get_unacked_files(conn, "push-1")]


def test_exhausted_files_leave_the_manifest(config, db_path):
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        create_push(conn, "push-1", "Sunny Falcon", challenge_key="ck-1")
        add_file(conn, file_id="f1", push_id="push-1", file_name="a.md",
                 file_path="", size=1, retrieval_key="k1", stored_path=None,
                 created_at=1000)
        from server.app.push_store import mark_exhausted

        mark_exhausted(conn, "f1")
        assert get_unacked_files(conn, "push-1") == []
