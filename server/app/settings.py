# server/app/settings.py
"""Runtime server options editable from the admin Settings page (design §8).

Kept separate from environment configuration: the env decides whether a feature
exists at all (e.g. `FEDERATION_ENABLED`); these settings decide whether an
available feature is currently *on*.
"""


def get(conn, key, default=None):
    row = conn.execute("SELECT value FROM server_settings WHERE key = ?",
                       (key,)).fetchone()
    return row["value"] if row else default


def set_value(conn, key, value) -> None:
    conn.execute(
        "INSERT INTO server_settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)))
    conn.commit()


def _bool(conn, key, default=True) -> bool:
    value = get(conn, key)
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def accept_new_slaves(conn) -> bool:
    """Whether the master currently accepts new slave enrolments."""
    return _bool(conn, "accept_new_slaves", True)


def signup_enabled(conn) -> bool:
    """Whether new account signup is open on the login/signup pages."""
    return _bool(conn, "signup_enabled", True)
