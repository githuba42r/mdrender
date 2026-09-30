# server/app/db.py
import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS server_keys (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  private_key_pem TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS server_identity (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  server_id TEXT NOT NULL,
  private_key_pem TEXT NOT NULL,
  public_key TEXT NOT NULL,
  hostname TEXT,
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
  account_id TEXT,
  created_at INTEGER NOT NULL,
  revoked_at INTEGER
);
CREATE TABLE IF NOT EXISTS devices (
  device_secret TEXT PRIMARY KEY,
  device_auth TEXT NOT NULL,
  device_name TEXT NOT NULL UNIQUE,
  device_model TEXT,
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
CREATE TABLE IF NOT EXISTS federated_servers (
  server_id TEXT PRIMARY KEY,
  hostname TEXT NOT NULL,
  base_url TEXT NOT NULL,
  public_key TEXT NOT NULL,
  secret_hash TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at INTEGER NOT NULL,
  last_seen INTEGER,
  down_since INTEGER,
  last_probe INTEGER,
  probe_failures INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS federated_nonces (
  server_id TEXT NOT NULL,
  nonce TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  PRIMARY KEY (server_id, nonce)
);
CREATE TABLE IF NOT EXISTS federated_outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  server_id TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  next_retry_at INTEGER,
  acked_at INTEGER
);
CREATE TABLE IF NOT EXISTS accounts (
  account_id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  phone TEXT,
  firebase_uid TEXT,
  password_hash TEXT,
  host TEXT NOT NULL DEFAULT 'master',
  status TEXT NOT NULL DEFAULT 'active',
  balance INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  last_login_at INTEGER
);
CREATE TABLE IF NOT EXISTS account_devices (
  server_id TEXT NOT NULL,
  account_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  fcm_token TEXT NOT NULL,
  name TEXT,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (server_id, account_id, device_id)
);
CREATE TABLE IF NOT EXISTS federation_client (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  master_url TEXT NOT NULL,
  server_secret TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  last_heartbeat INTEGER
);
CREATE TABLE IF NOT EXISTS account_files (
  file_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  size INTEGER NOT NULL,
  stored_path TEXT NOT NULL,
  alg TEXT,
  nonce TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS account_quotas (
  account_id TEXT PRIMARY KEY,
  max_bytes INTEGER,
  max_files INTEGER,
  max_age_hours INTEGER
);
CREATE TABLE IF NOT EXISTS account_keys (
  account_id TEXT PRIMARY KEY,
  public_key TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  retired_at INTEGER
);
CREATE TABLE IF NOT EXISTS device_content_pubkeys (
  device_id TEXT PRIMARY KEY,
  public_key TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS device_content_keys (
  device_id TEXT PRIMARY KEY,
  sealed_cek TEXT NOT NULL,
  alg TEXT NOT NULL DEFAULT 'rsa-oaep-sha256',
  created_at INTEGER NOT NULL,
  retired_at INTEGER
);
CREATE TABLE IF NOT EXISTS server_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS email_domain_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  domain TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS bans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  value TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'global',
  reason TEXT,
  created_at INTEGER NOT NULL,
  expires_at INTEGER
);
CREATE TABLE IF NOT EXISTS billing_plans (
  plan_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  scope TEXT NOT NULL,
  price_cents INTEGER NOT NULL DEFAULT 0,
  currency TEXT NOT NULL DEFAULT 'AUD',
  interval TEXT NOT NULL DEFAULT 'month',
  included_bytes INTEGER NOT NULL DEFAULT 0,
  included_messages INTEGER NOT NULL DEFAULT 0,
  storage_cents_per_mb INTEGER NOT NULL DEFAULT 0,
  message_cents_per_1000 INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS billing_groups (
  group_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  plan_id TEXT,
  is_default INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS account_groups (
  account_type TEXT NOT NULL,
  account_id TEXT NOT NULL,
  group_id TEXT NOT NULL,
  PRIMARY KEY (account_type, account_id)
);
CREATE TABLE IF NOT EXISTS account_plans (
  account_type TEXT NOT NULL,
  account_id TEXT NOT NULL,
  plan_id TEXT NOT NULL,
  PRIMARY KEY (account_type, account_id)
);
CREATE TABLE IF NOT EXISTS billing_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_type TEXT NOT NULL,
  account_id TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  reason TEXT,
  provider_ref TEXT,
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
            "devices": {
                "push_key": "TEXT NOT NULL DEFAULT ''",
                "account_id": "TEXT",
                "approved_at": "INTEGER",
                "content_pubkey": "TEXT",
                "content_proof": "TEXT",
                "device_model": "TEXT",
            },
            "pushes": {
                "challenge_key": "TEXT NOT NULL DEFAULT ''",
                "target_folder": "TEXT NOT NULL DEFAULT ''",
                "conflict": "TEXT NOT NULL DEFAULT 'rename'",
                "account_id": "TEXT",
            },
            "federated_servers": {
                "probe_failures": "INTEGER NOT NULL DEFAULT 0",
            },
            "sessions": {
                "principal_type": "TEXT NOT NULL DEFAULT 'admin'",
                "principal_id": "TEXT",
            },
            "pairing_tokens": {
                "account_id": "TEXT",
            },
            "admins": {
                "name": "TEXT",
                "firebase_uid": "TEXT",
                "firebase_email": "TEXT",
                "firebase_phone": "TEXT",
            },
            "billing_plans": {
                "storage_cents_per_mb": "INTEGER NOT NULL DEFAULT 0",
                "message_cents_per_1000": "INTEGER NOT NULL DEFAULT 0",
            },
            "accounts": {
                "messages_sent": "INTEGER NOT NULL DEFAULT 0",
                "name": "TEXT",
                "phone": "TEXT",
                "firebase_uid": "TEXT",
                "last_login_at": "INTEGER",
            },
            "clients": {
                "account_id": "TEXT",
            },
        }
        for table, columns in wanted.items():
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for column, decl in columns.items():
                if column not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
