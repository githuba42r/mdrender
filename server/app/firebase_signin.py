# server/app/firebase_signin.py
"""Whether the Firebase project actually allows phone (SMS) sign-in.

FIREBASE_PROVIDERS records what the operator wants to offer; the Firebase
console is the authority. A method enabled in config but disabled in the
console fails at runtime with OPERATION_NOT_ALLOWED - and drives the login
UI into offering it anyway (the "Email or phone number" field promising an
SMS code that can never arrive).

No public endpoint lists a project's enabled sign-in methods, so we probe:
accounts:sendVerificationCode with an unassignable number and a dummy
reCAPTCHA token. The phone gate is evaluated before number validation and
before any SMS can be dispatched, so the response distinguishes "disabled"
(OPERATION_NOT_ALLOWED / BILLING_NOT_ENABLED) from "enabled" (every other
identitytoolkit error - invalid phone, missing reCAPTCHA, ...) without ever
sending a message.

Cached like releases.py: an answer stands for an hour, a failed probe is
retried in a minute. Unknown (None) fails open - the configured list stands.
"""
import json
import threading
import time
import urllib.error
import urllib.request

_PROBE_URL = ("https://identitytoolkit.googleapis.com/v1/"
              "accounts:sendVerificationCode?key={key}")
_PROBE_NUMBER = "+10000000000"   # E.164-shaped, but unassignable
_ANSWERED_TTL = 3600
_UNANSWERED_TTL = 60
_DISABLED = {"OPERATION_NOT_ALLOWED", "BILLING_NOT_ENABLED"}
_UNANSWERABLE = {"API_KEY_INVALID"}

_lock = threading.Lock()
_cache = {"at": 0.0, "allowed": None}   # allowed: True/False answered, None not


def _interpret(payload) -> bool | None:
    """Map an identitytoolkit response to allowed(True)/disabled(False)/unknown(None)."""
    error = (payload or {}).get("error")
    if not error and payload:
        return True   # a 2xx body: the method exists (never for our dummy token)
    message = str((error or {}).get("message") or "")
    code = message.split(":")[0].strip().upper()
    if code in _DISABLED:
        return False
    if not code or code in _UNANSWERABLE:
        return None
    return True   # any other answer means the phone gate itself passed


def _fetch(api_key: str) -> bool | None:
    body = json.dumps({"phoneNumber": _PROBE_NUMBER,
                       "recaptchaToken": "provider-probe"}).encode()
    req = urllib.request.Request(
        _PROBE_URL.format(key=api_key), data=body, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return _interpret(json.load(resp))
    except urllib.error.HTTPError as exc:   # identitytoolkit reports through the body
        try:
            return _interpret(json.load(exc))
        except Exception:  # noqa: BLE001 - malformed body: unknown
            return None
    except Exception:  # noqa: BLE001 - unreachable/slow: unknown
        return None


def phone_allowed(api_key: str, *, now=None) -> bool | None:
    """True/False when Firebase answered, None when it could not (fail open)."""
    if not api_key:
        return None
    now = now if now is not None else time.time()
    with _lock:
        allowed, at = _cache["allowed"], _cache["at"]
    ttl = _ANSWERED_TTL if allowed is not None else _UNANSWERED_TTL
    if now - at < ttl:
        return allowed
    allowed = _fetch(api_key)
    with _lock:
        _cache.update(at=now, allowed=allowed)
    return allowed
