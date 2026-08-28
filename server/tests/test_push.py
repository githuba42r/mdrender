def test_push_file_lifecycle(config, db_path):
    from server.app.db import Database
    from server.app.push_store import (
        add_file, create_push, get_push_files, get_pending_files,
        mark_acked, purge_expired_bytes, list_pushes,
    )

    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        create_push(conn, "push-1", "Sunny Falcon")
        add_file(conn, file_id="f1", push_id="push-1", file_name="notes.md",
                 file_path="Docs", size=100, retrieval_key="k1",
                 stored_path="push-1/f1/notes.md", created_at=1000)
        assert len(get_push_files(conn, "push-1")) == 1
        assert len(get_pending_files(conn, 2000)) == 1
        assert mark_acked(conn, "f1", 2000)
        assert len(get_pending_files(conn, 2000)) == 0
        pushes = list_pushes(conn)
        assert pushes[0]["file_count"] == 1 and pushes[0]["acked_count"] == 1
