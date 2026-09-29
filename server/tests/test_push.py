from server.app.db import Database
from server.app import push_store
from server.app.push_store import (
    add_file, create_push, get_push_files, get_pending_files,
    get_unacked_files, get_push_by_id, list_pushes, mark_acked,
)
from server.app.trigger import build_manifest


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


def test_push_records_target_folder_and_conflict(config, db_path):
    """The device files the push where the sender asked, and with the conflict
    rule the sender chose, so both must survive to the manifest."""
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        create_push(conn, "push-1", "Sunny Falcon", challenge_key="ck-1",
                    target_folder="Story/cloud-send-images", conflict="replace")
        row = get_push_by_id(conn, "push-1")
        assert row["target_folder"] == "Story/cloud-send-images"
        assert row["conflict"] == "replace"

        add_file(conn, file_id="f1", push_id="push-1", file_name="a.jpg",
                 file_path="", size=1, retrieval_key="k1", stored_path=None,
                 created_at=1000)
        manifest = build_manifest("push-1", "2026-09-29T00:00:00+00:00",
                                  get_unacked_files(conn, "push-1"),
                                  row["target_folder"], row["conflict"])
        assert manifest["target_folder"] == "Story/cloud-send-images"
        assert manifest["conflict"] == "replace"


def test_unknown_conflict_falls_back_to_rename(config, db_path):
    """An unrecognised value must never resolve to something destructive, so
    it lands on RENAME and keeps both copies rather than overwriting."""
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        create_push(conn, "push-1", "Sunny Falcon", challenge_key="ck-1",
                    conflict="obliterate")
        assert get_push_by_id(conn, "push-1")["conflict"] == "rename"


def test_push_defaults_to_app_folder_and_rename(config, db_path):
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        create_push(conn, "push-1", "Sunny Falcon", challenge_key="ck-1")
        row = get_push_by_id(conn, "push-1")
        assert row["target_folder"] == ""
        assert row["conflict"] == "rename"


def test_list_pushes_reports_names_folder_and_real_status(config, db_path):
    """The admin row shows the file names, destination, and a status derived
    from the files. The old pushes.status column was never written, so it said
    "pending" even for a fully acknowledged push."""
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        create_push(conn, "push-1", "Sunny Falcon", challenge_key="ck-1",
                    target_folder="Story/x", conflict="skip")
        for fid, name in (("f1", "a.jpg"), ("f2", "b.jpg")):
            add_file(conn, file_id=fid, push_id="push-1", file_name=name,
                     file_path="", size=1, retrieval_key="k" + fid,
                     stored_path=None, created_at=1000)

        row = list_pushes(conn)[0]
        assert row["target_device"] == "Sunny Falcon"
        assert row["target_folder"] == "Story/x"
        assert row["conflict"] == "skip"
        assert row["file_count"] == 2
        assert "a.jpg" in row["file_names"] and "b.jpg" in row["file_names"]
        assert row["status"] == "pending"

        mark_acked(conn, "f1", 2000)
        assert list_pushes(conn)[0]["status"] == "pending"

        mark_acked(conn, "f2", 2000)
        assert list_pushes(conn)[0]["status"] == "acked"


def test_delete_push_removes_the_push_and_every_file(config, db_path):
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        create_push(conn, "push-1", "Sunny Falcon")
        add_file(conn, file_id="f1", push_id="push-1", file_name="a.md", file_path="",
                 size=10, retrieval_key="k1", stored_path="push-1/f1/a.md", created_at=1000)
        add_file(conn, file_id="f2", push_id="push-1", file_name="b.md", file_path="",
                 size=10, retrieval_key="k2", stored_path="push-1/f2/b.md", created_at=1000)
        # A second push must be left alone.
        create_push(conn, "push-2", "Sunny Falcon")
        add_file(conn, file_id="f3", push_id="push-2", file_name="c.md", file_path="",
                 size=10, retrieval_key="k3", stored_path="push-2/f3/c.md", created_at=1000)

        assert push_store.delete_push(conn, "push-1") is True
        assert get_push_by_id(conn, "push-1") is None
        assert get_push_files(conn, "push-1") == []
        # Same device, different push: deleting one push must not touch the other.
        assert get_push_by_id(conn, "push-2") is not None
        assert len(get_push_files(conn, "push-2")) == 1


def test_delete_push_on_an_unknown_id_returns_false(config, db_path):
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)

        # False is what stops the caller deleting an arbitrary directory.
        assert push_store.delete_push(conn, "does-not-exist") is False
