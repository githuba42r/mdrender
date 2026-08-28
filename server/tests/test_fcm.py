import base64
import io
import json
import urllib.error
import urllib.request

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from server.app.crypto import generate_rsa_keypair
from server.app.crypto import private_to_pem
from server.app.fcm import FcmClient, FcmError

_priv, _ = generate_rsa_keypair()
SA = {
    "project_id": "mdrender-push",
    "client_email": "fcm-pusher@mdrender-push.iam.gserviceaccount.com",
    "private_key": private_to_pem(_priv).decode(),
    "private_key_id": "kid-123",
    "token_uri": "https://oauth2.googleapis.com/token",
}


def test_mint_token_well_formed():
    client = FcmClient(SA)
    token = client.mint_token()
    header_b64, claims_b64, sig_b64 = token.split(".")
    header = json.loads(base64.urlsafe_b64decode(header_b64 + "=="))
    claims = json.loads(base64.urlsafe_b64decode(claims_b64 + "=="))
    assert header["kid"] == "kid-123" and header["alg"] == "RS256"
    assert claims["iss"] == SA["client_email"]
    assert claims["scope"] == "https://www.googleapis.com/auth/firebase.messaging"
    assert claims["exp"] - claims["iat"] == 3600

    signing_input = f"{header_b64}.{claims_b64}".encode()
    sig = base64.urlsafe_b64decode(sig_b64 + "==")
    _priv.public_key().verify(sig, signing_input, padding.PKCS1v15(), hashes.SHA256())


def test_send_posts_to_fcm_endpoint(monkeypatch):
    client = FcmClient(SA)
    captured = {}
    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"{}"
    def fake_urlopen(req, timeout):
        captured["req"] = req
        return _FakeResp()
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(client, "_get_access_token", lambda: "ACCESS_TOKEN")
    client.send({"title": "hi"}, "fcm-token-1")
    req = captured["req"]
    assert req.full_url == "https://fcm.googleapis.com/v1/projects/mdrender-push/messages:send"
    assert req.get_method() == "POST"
    assert req.get_header("Authorization") == "Bearer ACCESS_TOKEN"
    assert req.get_header("Content-type") == "application/json"
    assert json.loads(req.data) == {"message": {"token": "fcm-token-1", "data": {"title": "hi"}}}

def test_send_raises_fcm_error_on_http_error(monkeypatch):
    client = FcmClient(SA)
    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(b"err"))
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(client, "_get_access_token", lambda: "ACCESS_TOKEN")
    with pytest.raises(FcmError):
        client.send({"title": "hi"}, "fcm-token-1")
