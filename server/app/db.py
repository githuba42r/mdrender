# server/app/db.py
import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS server_keys (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  private_key_pem TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pairing_tokens (
  token TEXT PRIMARY KEY,
  expires_at INTEGER NOT NULL,
  used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS admins (
  admin_id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  email TEXT,
  password_hash TEXT,
  role TEXT NOT NULL DEFAULT 'admin',
  created_at INTEGER NOT NULL,
  disabled_at INTEGER
);
CREATE TABLE IF NOT EXISTS users (
  user_id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT,
  verified_at INTEGER,
  created_at INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS clients (
  client_id TEXT PRIMARY KEY,
  client_secret_hash TEXT NOT NULL,
  name TEXT NOT NULL,
  scopes TEXT NOT NULL DEFAULT 'push',
  created_at INTEGER NOT NULL,
  revoked_at INTEGER
);
CREATE TABLE IF NOT EXISTS devices (
  device_secret TEXT PRIMARY KEY,
  device_auth TEXT NOT NULL,
  device_name TEXT NOT NULL UNIQUE,
  fcm_token TEXT,
  public_key TEXT NOT NULL,
  push_key TEXT NOT NULL,
  registered_at INTEGER NOT NULL,
  last_seen INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pushes (
  push_id TEXT PRIMARY KEY,
  target_device TEXT NOT NULL,
  challenge_key TEXT NOT NULL,
  date INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  target_folder TEXT NOT NULL DEFAULT '',
  conflict TEXT NOT NULL DEFAULT 'rename'
);
CREATE TABLE IF NOT EXISTS push_files (
  file_id TEXT PRIMARY KEY,
  push_id TEXT NOT NULL,
  file_name TEXT NOT NULL,
  file_path TEXT NOT NULL DEFAULT '',
  size INTEGER NOT NULL,
  retrieval_key TEXT NOT NULL,
  stored_path TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  retries INTEGER NOT NULL DEFAULT 0,
  next_retry_at INTEGER,
  acked_at INTEGER,
  created_at INTEGER NOT NULL
);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(SCHEMA)
        self._migrate(conn)
        conn.commit()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Idempotently add columns introduced after a database was first created.

        SQLite has no ALTER TABLE ... IF NOT EXISTS, so compare against the live
        table and issue plain ALTERs for anything missing.
        """
        wanted = {
            "devices": {"push_key": "TEXT NOT NULL DEFAULT ''"},
            "pushes": {
                "challenge_key": "TEXT NOT NULL DEFAULT ''",
                "target_folder": "TEXT NOT NULL DEFAULT ''",
                "conflict": "TEXT NOT NULL DEFAULT 'rename'",
            },
        }
        for table, columns in wanted.items():
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for column, decl in columns.items():
                if column not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
