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


def test_doorbell_columns_exist(db_path):
    from server.app.db import Database

    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        devices = {r["name"] for r in conn.execute("PRAGMA table_info(devices)")}
        pushes = {r["name"] for r in conn.execute("PRAGMA table_info(pushes)")}
    # push_key seals the FCM doorbell; challenge_key gates the manifest fetch.
    assert "push_key" in devices
    assert "challenge_key" in pushes


def test_migration_adds_columns_to_a_pre_existing_database(db_path):
    """A database created before the doorbell redesign has no push_key/challenge_key.

    init_schema must upgrade it in place rather than failing, or every existing
    deployment would need manual intervention.
    """
    import sqlite3

    legacy = """
    CREATE TABLE devices (
      device_secret TEXT PRIMARY KEY, device_auth TEXT NOT NULL,
      device_name TEXT NOT NULL UNIQUE, fcm_token TEXT,
      public_key TEXT NOT NULL, registered_at INTEGER NOT NULL,
      last_seen INTEGER NOT NULL
    );
    CREATE TABLE pushes (
      push_id TEXT PRIMARY KEY, target_device TEXT NOT NULL,
      date INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending'
    );
    """
    conn = sqlite3.connect(db_path)
    conn.executescript(legacy)
    conn.commit()
    conn.close()

    from server.app.db import Database

    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        devices = {r["name"] for r in conn.execute("PRAGMA table_info(devices)")}
        pushes = {r["name"] for r in conn.execute("PRAGMA table_info(pushes)")}
        rows = conn.execute("SELECT COUNT(*) AS n FROM devices").fetchone()
    assert "push_key" in devices
    assert "challenge_key" in pushes
    assert rows["n"] == 0


def test_migration_is_idempotent(db_path):
    from server.app.db import Database

    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        db.init_schema(conn)
        devices = {r["name"] for r in conn.execute("PRAGMA table_info(devices)")}
    assert "push_key" in devices
