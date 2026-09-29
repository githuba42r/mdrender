import base64, hashlib

from server.app import crypto
from server.app.db import Database
from server.app.pairing import (
    create_pairing_token, consume_pairing_token, register_device, check_device,
    get_device_by_name, update_device_push_key, update_device_token,
)

PUSH_KEY = base64.b64encode(b"\x01" * 32).decode()


def _sig(priv, secret, name, token, pub_b64, push_key=PUSH_KEY):
    data = hashlib.sha256(
        f"{secret}{name}{token}{pub_b64}{push_key}".encode()
    ).digest()
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
            fcm_token="tok-1", public_key_b64=pub_b64, push_key_b64=PUSH_KEY,
            pairing_token=token2,
            sig_b64=_sig(phone_priv, "sec-1", "Sunny Falcon", "tok-1", pub_b64),
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
            push_key_b64=base64.b64encode(b"\x02" * 32).decode(),
            pairing_token=token3,
            sig_b64=_sig(priv2, "sec-2", "Sunny Falcon", "tok-2", pub2_b64,
                         base64.b64encode(b"\x02" * 32).decode()),
        )
        assert displaced == "sec-1"
        assert get_device_by_name(conn, "Sunny Falcon")["device_secret"] == "sec-2"

        # Token rotation requires device_auth.
        assert update_device_token(conn, "sec-2", auth2, "tok-3")
        assert get_device_by_name(conn, "Sunny Falcon")["fcm_token"] == "tok-3"
        assert not update_device_token(conn, "sec-2", "wrong-auth", "tok-4")


def test_registration_stores_the_negotiated_push_key(config, db_path):
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        priv, pub = crypto.generate_rsa_keypair()
        pub_b64 = base64.b64encode(crypto.public_to_spki_der(pub)).decode()
        token = create_pairing_token(conn, 15)
        auth, _ = register_device(
            conn, device_secret="sec-1", device_name="Falcon",
            fcm_token="tok-1", public_key_b64=pub_b64, push_key_b64=PUSH_KEY,
            pairing_token=token,
            sig_b64=_sig(priv, "sec-1", "Falcon", "tok-1", pub_b64),
        )
        assert auth
        assert get_device_by_name(conn, "Falcon")["push_key"] == PUSH_KEY


def test_signature_covers_the_push_key(config, db_path):
    """A registration that signs one push_key but declares another must fail.

    Otherwise an attacker who can intercept the request could substitute their
    own doorbell key and read every subsequent trigger.
    """
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        priv, pub = crypto.generate_rsa_keypair()
        pub_b64 = base64.b64encode(crypto.public_to_spki_der(pub)).decode()
        token = create_pairing_token(conn, 15)
        auth, _ = register_device(
            conn, device_secret="sec-1", device_name="Falcon",
            fcm_token="tok-1", public_key_b64=pub_b64,
            push_key_b64=base64.b64encode(b"\x09" * 32).decode(),  # substituted
            pairing_token=token,
            sig_b64=_sig(priv, "sec-1", "Falcon", "tok-1", pub_b64),  # signed PUSH_KEY
        )
        assert auth is None
        assert get_device_by_name(conn, "Falcon") is None


def test_registration_requires_a_push_key(config, db_path):
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        priv, pub = crypto.generate_rsa_keypair()
        pub_b64 = base64.b64encode(crypto.public_to_spki_der(pub)).decode()
        token = create_pairing_token(conn, 15)
        auth, _ = register_device(
            conn, device_secret="sec-1", device_name="Falcon",
            fcm_token="tok-1", public_key_b64=pub_b64, push_key_b64="",
            pairing_token=token,
            sig_b64=_sig(priv, "sec-1", "Falcon", "tok-1", pub_b64, push_key=""),
        )
        assert auth is None


def test_push_key_rotation_requires_device_auth(config, db_path):
    db = Database(db_path)
    with db.connect() as conn:
        db.init_schema(conn)
        priv, pub = crypto.generate_rsa_keypair()
        pub_b64 = base64.b64encode(crypto.public_to_spki_der(pub)).decode()
        token = create_pairing_token(conn, 15)
        auth, _ = register_device(
            conn, device_secret="sec-1", device_name="Falcon",
            fcm_token="tok-1", public_key_b64=pub_b64, push_key_b64=PUSH_KEY,
            pairing_token=token,
            sig_b64=_sig(priv, "sec-1", "Falcon", "tok-1", pub_b64),
        )
        rotated = base64.b64encode(b"\x07" * 32).decode()
        assert not update_device_push_key(conn, "sec-1", "wrong-auth", rotated)
        assert get_device_by_name(conn, "Falcon")["push_key"] == PUSH_KEY
        assert update_device_push_key(conn, "sec-1", auth, rotated)
        assert get_device_by_name(conn, "Falcon")["push_key"] == rotated
