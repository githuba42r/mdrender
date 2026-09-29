# server/app/oidc.py
"""Verify hosted identity-provider tokens (Firebase ID tokens; design §4a/D11).

No third-party SDK: fetch Google's `securetoken` signing certificates and verify
the RS256 JWT with `cryptography`. Never raises — returns claims or None.
"""
import base64
import json
import time
import urllib.request

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key

GOOGLE_CERTS_URL = ("https://www.googleapis.com/robot/v1/metadata/x509/"
                    "securetoken@system.gserviceaccount.com")
_certs_cache = {"expires": 0.0, "certs": {}}


def _b64url(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def fetch_certs(timeout: int = 10) -> dict:
    with urllib.request.urlopen(GOOGLE_CERTS_URL, timeout=timeout) as resp:
        return json.loads(resp.read())


def get_certs(config, *, certs: dict | None = None, now: float | None = None) -> dict:
    if certs is not None:
        return certs
    now = now or time.time()
    if _certs_cache["certs"] and now < _certs_cache["expires"]:
        return _certs_cache["certs"]
    fetched = fetch_certs()
    _certs_cache["certs"] = fetched
    _certs_cache["expires"] = now + 3600
    return fetched


def _public_key(pem: str):
    # Google serves x509 certificates; tests may pass a plain public-key PEM.
    try:
        return x509.load_pem_x509_certificate(pem.encode()).public_key()
    except Exception:  # noqa: BLE001
        return load_pem_public_key(pem.encode())


def verify_firebase_id_token(config, token, *, certs: dict | None = None,
                             now: float | None = None) -> dict | None:
    """Return the verified claims, or None if the token is not valid."""
    project = getattr(config, "FIREBASE_PROJECT_ID", "")
    if not project or not token:
        return None
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
        header = json.loads(_b64url(header_b64))
        payload = json.loads(_b64url(payload_b64))
    except Exception:  # noqa: BLE001
        return None
    if header.get("alg") != "RS256" or not header.get("kid"):
        return None
    try:
        cert_pem = get_certs(config, certs=certs).get(header["kid"])
    except Exception:  # noqa: BLE001
        return None
    if not cert_pem:
        return None
    try:
        _public_key(cert_pem).verify(
            _b64url(sig_b64), f"{header_b64}.{payload_b64}".encode(),
            padding.PKCS1v15(), hashes.SHA256())
    except Exception:  # noqa: BLE001
        return None
    now = now or time.time()
    if payload.get("aud") != project:
        return None
    if payload.get("iss") != f"https://securetoken.google.com/{project}":
        return None
    if not payload.get("sub") or payload.get("exp", 0) < now:
        return None
    return payload
