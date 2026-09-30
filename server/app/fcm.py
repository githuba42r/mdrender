# server/app/fcm.py
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

FCM_ENDPOINT = "https://fcm.googleapis.com/v1"
OAUTH_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


class FcmError(Exception):
    pass


def load_service_account(path: str) -> dict:
    if not path or not os.path.exists(path):
        raise FcmError(f"service account file not found: {path!r}")
    with open(path) as fh:
        return json.load(fh)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class FcmClient:
    def __init__(self, service_account: dict):
        self.sa = service_account
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._private_key = serialization.load_pem_private_key(
            service_account["private_key"].encode(), password=None
        )

    def mint_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_expiry - 60:
            return self._token
        header = {"alg": "RS256", "typ": "JWT", "kid": self.sa["private_key_id"]}
        claims = {
            "iss": self.sa["client_email"],
            "scope": OAUTH_SCOPE,
            "aud": self.sa["token_uri"],
            "iat": int(now),
            "exp": int(now) + 3600,
        }
        signing_input = (_b64url(json.dumps(header).encode()) + "."
                         + _b64url(json.dumps(claims).encode()))
        signature = self._private_key.sign(
            signing_input.encode(), padding.PKCS1v15(), hashes.SHA256()
        )
        self._token = signing_input + "." + _b64url(signature)
        self._token_expiry = now + 3600
        return self._token

    def send(self, data_message: dict, fcm_token: str, *,
             high_priority: bool = False) -> None:
        access_token = self._get_access_token()
        url = f"{FCM_ENDPOINT}/projects/{self.sa['project_id']}/messages:send"
        message = {"token": fcm_token, "data": data_message}
        if high_priority:
            message["android"] = {"priority": "high"}
        body = json.dumps({"message": message}).encode()
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Authorization": f"Bearer {access_token}",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
        except urllib.error.HTTPError as e:
            raise FcmError(f"FCM send failed: HTTP {e.code} {e.read()[:200]}")
        except urllib.error.URLError as e:
            raise FcmError(f"FCM send failed: {e}")

    def _get_access_token(self) -> str:
        jwt = self.mint_token()
        body = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt,
        }).encode()
        req = urllib.request.Request(self.sa["token_uri"], data=body, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise FcmError(f"token mint failed: HTTP {e.code} {e.read()[:200]}")
        except urllib.error.URLError as e:
            raise FcmError(f"token mint failed: {e}")
        return data["access_token"]
