# server/app/identity.py
"""Identity abstraction.

Every login goes through an ``IdentityProvider`` that maps credentials (or,
later, an IdP token) to a local principal. The **local** provider — a local
`admins` table with PBKDF2 passwords — is always available; a hosted identity
provider (Firebase Auth / Auth0 / Cognito) plugs in behind the same interface.
See docs/superpowers/specs/2026-09-29-federated-push-server-design.md §4a.
"""
import time
import uuid

from server.app.auth import hash_secret, verify_secret


# ---- Local admin store ------------------------------------------------------

def count_admins(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0]


def get_admin_by_username(conn, username):
    return conn.execute("SELECT * FROM admins WHERE username = ?",
                        (username,)).fetchone()


def list_admins(conn):
    return conn.execute(
        "SELECT admin_id, name, username, email, role, created_at, disabled_at,"
        " firebase_uid, firebase_email, firebase_phone"
        " FROM admins ORDER BY created_at").fetchall()


def get_admin_by_firebase_uid(conn, uid):
    if not uid:
        return None
    return conn.execute("SELECT * FROM admins WHERE firebase_uid = ?",
                        (uid,)).fetchone()


def get_admin_by_firebase_email(conn, email):
    if not email:
        return None
    return conn.execute("SELECT * FROM admins WHERE lower(firebase_email) = lower(?)",
                        (email,)).fetchone()


def get_admin_by_firebase_phone(conn, phone):
    if not phone:
        return None
    return conn.execute("SELECT * FROM admins WHERE firebase_phone = ?",
                        (phone,)).fetchone()


def link_firebase(conn, admin_id, *, uid, email=None, phone=None) -> None:
    """Bind an admin to a Firebase uid (and its verified identifiers)."""
    conn.execute(
        "UPDATE admins SET firebase_uid = ?, firebase_email = ?, firebase_phone = ?"
        " WHERE admin_id = ?", (uid, email or None, phone or None, admin_id))
    conn.commit()


def unlink_firebase(conn, admin_id) -> None:
    conn.execute(
        "UPDATE admins SET firebase_uid = NULL, firebase_email = NULL,"
        " firebase_phone = NULL WHERE admin_id = ?", (admin_id,))
    conn.commit()


def get_admin_by_email(conn, email):
    return conn.execute("SELECT * FROM admins WHERE lower(email) = lower(?)",
                        (email,)).fetchone()


def get_admin(conn, admin_id):
    return conn.execute("SELECT * FROM admins WHERE admin_id = ?",
                        (admin_id,)).fetchone()


def create_admin(conn, username, password, *, role="admin", email=None,
                 name=None) -> str:
    admin_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO admins (admin_id, name, username, email, password_hash, role,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (admin_id, name, username, email, hash_secret(password), role,
         int(time.time())),
    )
    conn.commit()
    return admin_id


def update_admin(conn, admin_id, *, name=None, email=None, password=None) -> None:
    """Update an admin's display name / email and, if given, password."""
    if name is not None:
        conn.execute("UPDATE admins SET name = ? WHERE admin_id = ?", (name, admin_id))
    if email is not None:
        conn.execute("UPDATE admins SET email = ? WHERE admin_id = ?", (email, admin_id))
    if password:
        conn.execute("UPDATE admins SET password_hash = ? WHERE admin_id = ?",
                     (hash_secret(password), admin_id))
    conn.commit()


def disable_admin(conn, admin_id) -> None:
    conn.execute("UPDATE admins SET disabled_at = ? WHERE admin_id = ?",
                 (int(time.time()), admin_id))
    conn.commit()


# ---- Providers --------------------------------------------------------------

class IdentityProvider:
    """Interface: verify credentials/tokens, return a principal or None."""

    kind = "base"

    def authenticate(self, conn, username, password):
        raise NotImplementedError


class LocalIdentityProvider(IdentityProvider):
    """Username + password against the local `admins` table."""

    kind = "local"

    def authenticate(self, conn, username, password):
        if not username or not password:
            return None
        # The login field accepts either the username or the admin's email.
        row = get_admin_by_username(conn, username) or get_admin_by_email(conn, username)
        if row is None or row["disabled_at"] is not None:
            return None
        if not row["password_hash"] or not verify_secret(password, row["password_hash"]):
            return None
        return {"type": "admin", "id": row["admin_id"],
                "username": row["username"], "role": row["role"]}


def get_identity_provider(config) -> IdentityProvider:
    """Return the configured provider.

    The local provider is always available. A hosted IdP (Firebase Auth, the
    recommended option, or Auth0/Cognito) is selected here once configured.
    """
    return LocalIdentityProvider()
