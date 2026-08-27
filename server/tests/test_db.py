def test_schema_creates_tables(db_path):
    from server.app.db import Database

    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r["name"] for r in rows}
    assert {"server_keys", "pairing_tokens", "clients", "devices",
            "pushes", "push_files"}.issubset(names)
