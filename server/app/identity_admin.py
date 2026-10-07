# server/app/identity_admin.py
"""Firebase Auth Admin writes, so an account profile edit reaches the IdP too.

Uses the Identity Platform Admin REST API with a service-account key
(`FIREBASE_SERVICE_ACCOUNT`, falling back to `FCM_SERVER_KEY`). The caller's
access token needs the `firebaseauth.users.update` permission (Firebase
Authentication Admin, `roles/firebaseauth.admin`). Never raises — returns
(payload, error).
"""
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

SCOPE = "https://www.googleapis.com/auth/identitytoolkit"
ACCOUNTS = "https://identitytoolkit.googleapis.com/v1/projects/{project}/accounts"
# signUp is the one call that is *not* project-scoped: the
# .../projects/{project}/accounts:signUp path 404s, and the API key (which
# belongs to this project) selects the project on the global endpoint.
SIGNUP = "https://identitytoolkit.googleapis.com/v1/accounts:signUp"
_token_cache = {"token": None, "expires": 0.0}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _service_account(config):
    path = (getattr(config, "FIREBASE_SERVICE_ACCOUNT", "")
            or getattr(config, "FCM_SERVER_KEY", ""))
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def available(config) -> bool:
    return bool(getattr(config, "FIREBASE_PROJECT_ID", "")
                and _service_account(config))


def _access_token(config, now=None):
    now = now or time.time()
    if _token_cache["token"] and now < _token_cache["expires"]:
        return _token_cache["token"]
    sa = _service_account(config)
    if sa is None:
        return None
    issued = int(now)
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64(json.dumps({
        "iss": sa["client_email"], "scope": SCOPE,
        "aud": "https://oauth2.googleapis.com/token",
        "iat": issued, "exp": issued + 3600}).encode())
    signing = f"{header}.{claims}".encode()
    key = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    signature = key.sign(signing, padding.PKCS1v15(), hashes.SHA256())
    assertion = f"{header}.{claims}.{_b64(signature)}"
    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion}).encode()
    try:
        with urllib.request.urlopen("https://oauth2.googleapis.com/token",
                                    data=body, timeout=10) as resp:
            token = json.load(resp)
    except Exception:  # noqa: BLE001 - network/credential failures are reported to caller
        return None
    _token_cache["token"] = token.get("access_token")
    _token_cache["expires"] = now + int(token.get("expires_in", 3600)) - 60
    return _token_cache["token"]


def _post(config, url, payload):
    token = _access_token(config)
    if token is None:
        return None, "Firebase Admin credentials unavailable"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp), None
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc).get("error", {}).get("message")
        except Exception:  # noqa: BLE001
            detail = None
        return None, detail or f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def _project(config) -> str:
    return getattr(config, "FIREBASE_PROJECT_ID", "")


def get_user(config, uid):
    """Look up an Identity Platform user by uid. -> (user|None, error)."""
    if not _project(config):
        return None, "identity provider not configured"
    payload, error = _post(config, f"{ACCOUNTS.format(project=_project(config))}:lookup",
                           {"localId": [uid]})
    if error:
        return None, error
    users = (payload or {}).get("users") or []
    return (users[0] if users else None), None


def get_user_by_email(config, email):
    """Look up an Identity Platform user by email. -> (user|None, error).

    An email with no user is not an error: the caller decides whether to
    create one.
    """
    if not _project(config):
        return None, "identity provider not configured"
    payload, error = _post(config, f"{ACCOUNTS.format(project=_project(config))}:lookup",
                           {"email": email})
    if error:
        return None, error
    users = (payload or {}).get("users") or []
    return (users[0] if users else None), None


def create_user(config, *, email, display_name=None):
    """Create an Identity Platform user (the Admin SDK's createUser path).

    No password is minted: the account signs in by email link, a social
    provider, or a password reset, so nothing secret is stored or shown.
    -> (uid, error)
    """
    if not _project(config):
        return None, "identity provider not configured"
    key = getattr(config, "FIREBASE_API_KEY", "")
    if not key:
        return None, "FIREBASE_API_KEY not configured"
    payload = {"email": email, "returnSecureToken": False}
    if display_name:
        payload["displayName"] = display_name
    result, error = _post(config, f"{SIGNUP}?key={key}", payload)
    if error:
        return None, error
    uid = (result or {}).get("localId")
    if not uid:
        return None, "provider returned no user id"
    return uid, None


def update_user(config, uid, **fields):
    """Update the user's fields (email, phoneNumber, displayName, password, …).

    Only the fields passed are changed; `deleteAttribute` is honoured. -> (result, error).
    """
    if not _project(config):
        return None, "identity provider not configured"
    payload = {"localId": uid}
    payload.update({key: value for key, value in fields.items() if value is not None})
    if len(payload) == 1:
        return {"localId": uid}, None
    return _post(config, f"{ACCOUNTS.format(project=_project(config))}:update", payload)
