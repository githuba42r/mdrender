import base64, hashlib, time

from server.app import crypto
from server.app.db import Database
from server.app.store import get_or_create_server_keypair
from server.app.pairing import (
    create_pairing_token, consume_pairing_token, register_device, check_device,
    get_device_by_name, update_device_token, update_device_name,
)


def _sig(priv, secret, name, token, pub_b64):
    data = hashlib.sha256(f"{secret}{name}{token}{pub_b64}".encode()).digest()
    return base64.b64encode(crypto.sign(priv, data)).decode()


def test_register_and_replace_on_name_collision(config, db_path):
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        token = create_pairing_token(conn, 15)
        assert consume_pairing_token(conn, token)
        assert not consume_pairing_token(conn, token)  # single-use

        phone_priv, phone_pub = crypto.generate_rsa_keypair()
        pub_b64 = base64.b64encode(crypto.public_to_spki_der(phone_pub)).decode()
        token2 = create_pairing_token(conn, 15)
        auth, displaced = register_device(
            conn, device_secret="sec-1", device_name="Sunny Falcon",
            fcm_token="tok-1", public_key_b64=pub_b64,
            pairing_token=token2, sig_b64=_sig(phone_priv, "sec-1", "Sunny Falcon", "tok-1", pub_b64),
        )
        assert auth and displaced is None
        assert check_device(conn, "sec-1", auth)

        # New keypair claims the same name -> replaces.
        priv2, pub2 = crypto.generate_rsa_keypair()
        pub2_b64 = base64.b64encode(crypto.public_to_spki_der(pub2)).decode()
        token3 = create_pairing_token(conn, 15)
        auth2, displaced = register_device(
            conn, device_secret="sec-2", device_name="Sunny Falcon",
            fcm_token="tok-2", public_key_b64=pub2_b64,
            pairing_token=token3, sig_b64=_sig(priv2, "sec-2", "Sunny Falcon", "tok-2", pub2_b64),
        )
        assert displaced == "sec-1"
        assert get_device_by_name(conn, "Sunny Falcon")["device_secret"] == "sec-2"

        # Token rotation requires device_auth.
        assert update_device_token(conn, "sec-2", auth2, "tok-3")
        assert get_device_by_name(conn, "Sunny Falcon")["fcm_token"] == "tok-3"
        assert not update_device_token(conn, "sec-2", "wrong-auth", "tok-4")
