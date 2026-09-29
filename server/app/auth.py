# server/app/auth.py
import hashlib
import hmac
import time
import uuid

SESSION_TTL_SECONDS = 15 * 60  # ENROL_SESSION_TTL_MINUTES


def hash_secret(secret: str) -> str:
    salt = uuid.uuid4().hex
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt.encode(), 100_000)
    return f"{salt}${digest.hex()}"


def verify_secret(secret: str, stored: str) -> bool:
    try:
        salt, digest_hex = stored.split("$", 1)
        digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt.encode(), 100_000)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except ValueError:
        return False


class LoginGate:
    """Brute-force lockout, keyed by an arbitrary identity string.

    Since logins are now per-user, the caller keys this by ``username@ip`` (or
    any identity), so one user's failures do not lock out another. Credential
    verification lives in the identity provider, not here.
    """

    def __init__(self, config):
        self.config = config
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def is_locked(self, key: str) -> int:
        """Seconds until `key` may retry, or 0 if not locked."""
        remaining = self._locked_until.get(key, 0.0) - time.time()
        return int(remaining) if remaining > 0 else 0

    def record_failure(self, key: str) -> int:
        """Count a failed attempt; returns the new lockout seconds if tripped."""
        now = time.time()
        attempts = self._failures.setdefault(key, [])
        attempts.append(now)
        attempts[:] = [t for t in attempts
                       if now - t < self.config.LOGIN_LOCKOUT_SECONDS]
        if len(attempts) >= self.config.LOGIN_MAX_ATTEMPTS:
            self._locked_until[key] = now + self.config.LOGIN_LOCKOUT_SECONDS
            return self.config.LOGIN_LOCKOUT_SECONDS
        return 0

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)


def _sig(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_session(session_secret: str, config) -> str:
    payload = f"{uuid.uuid4().hex}:{int(time.time())}"
    return f"{payload}:{_sig(session_secret, payload)}"


def verify_session(session_secret: str, token: str, config) -> bool:
    parts = token.split(":")
    if len(parts) != 3:
        return False
    payload, created_s, given_sig = parts
    try:
        created = int(created_s)
    except ValueError:
        return False
    if time.time() - created > SESSION_TTL_SECONDS:
        return False
    expected = _sig(session_secret, f"{payload}:{created_s}")
    if not hmac.compare_digest(expected, given_sig):
        return False
    return True


# ---- Server-side session records -------------------------------------------
#
# The HMAC above proves a cookie was minted by this server; the table below is
# what makes a session *retained*: it survives a restart, can be listed and
# revoked, and expires against a server-held deadline rather than whatever the
# cookie claims. Only the SHA-256 of the token is stored, so a leaked database
# cannot be replayed as a live session.


def session_token_hash(session_secret: str, token: str) -> str:
    return hmac.new(session_secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def create_session(conn, session_secret: str, config) -> str:
    """Mint a signed session token and record it so it can be validated later."""
    token = make_session(session_secret, config)
    now = int(time.time())
    conn.execute(
        "INSERT INTO sessions (token_hash, created_at, expires_at) VALUES (?, ?, ?)",
        (session_token_hash(session_secret, token), now, now + SESSION_TTL_SECONDS),
    )
    conn.commit()
    return token


def session_is_valid(conn, session_secret: str, token: str | None, config) -> bool:
    """True only when the cookie is correctly signed *and* still has a live row.

    Both checks matter: the signature alone would accept a token that was
    logged out, and the row alone would accept anything an attacker guessed.
    """
    if not token or not verify_session(session_secret, token, config):
        return False
    row = conn.execute(
        "SELECT expires_at FROM sessions WHERE token_hash = ?",
        (session_token_hash(session_secret, token),),
    ).fetchone()
    if row is None:
        return False
    if time.time() > row["expires_at"]:
        # Expired rows are pruned lazily; nothing reads them again.
        delete_session(conn, session_secret, token)
        return False
    return True


def delete_session(conn, session_secret: str, token: str | None) -> None:
    if not token:
        return
    conn.execute("DELETE FROM sessions WHERE token_hash = ?",
                 (session_token_hash(session_secret, token),))
    conn.commit()


def purge_expired_sessions(conn, now: float | None = None) -> None:
    now = now or time.time()
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (int(now),))
    conn.commit()


ACCESS_TOKENS: dict[str, tuple[str, float]] = {}  # token -> (client_id, expires_at)


def issue_access_token(config, client_id: str) -> str:
    token = uuid.uuid4().hex
    ACCESS_TOKENS[token] = (client_id, time.time() + config.ACCESS_TOKEN_TTL_SECONDS)
    return token


def validate_access_token(config, token: str) -> str | None:
    entry = ACCESS_TOKENS.get(token)
    if entry is None:
        return None
    client_id, expires = entry
    if time.time() > expires:
        ACCESS_TOKENS.pop(token, None)
        return None
    return client_id


def revoke_access_tokens(client_id: str) -> int:
    """Drop every live access token minted for *client_id*.

    Revoking a client must take effect immediately, not whenever its last
    issued token happens to expire (up to ACCESS_TOKEN_TTL_SECONDS later).
    Returns how many tokens were dropped.
    """
    stale = [t for t, (cid, _) in ACCESS_TOKENS.items() if cid == client_id]
    for t in stale:
        ACCESS_TOKENS.pop(t, None)
    return len(stale)
