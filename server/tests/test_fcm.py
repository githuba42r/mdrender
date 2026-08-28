import base64
import json

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
