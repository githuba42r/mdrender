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
    def __init__(self, config):
        self.config = config
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def check(self, ip: str, password: str) -> tuple[bool, int]:
        now = time.time()
        locked_until = self._locked_until.get(ip, 0.0)
        if now < locked_until:
            return False, int(locked_until - now)
        if password == self.config.SERVER_PASSWORD:
            self._failures.pop(ip, None)
            self._locked_until.pop(ip, None)
            return True, 0
        attempts = self._failures.setdefault(ip, [])
        attempts.append(now)
        attempts[:] = [t for t in attempts if now - t < self.config.LOGIN_LOCKOUT_SECONDS]
        if len(attempts) >= self.config.LOGIN_MAX_ATTEMPTS:
            self._locked_until[ip] = now + self.config.LOGIN_LOCKOUT_SECONDS
            return False, self.config.LOGIN_LOCKOUT_SECONDS
        return False, 0


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
