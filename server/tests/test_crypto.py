def test_roundtrip_oaep_and_aes():
    from server.app.crypto import (
        aes_gcm_decrypt, aes_gcm_encrypt, generate_rsa_keypair, oaep_unwrap, oaep_wrap,
    )

    priv, pub = generate_rsa_keypair()
    content_key = b"0123456789abcdef0123456789abcdef"  # 32 bytes
    wrapped = oaep_wrap(pub, content_key)
    assert oaep_unwrap(priv, wrapped) == content_key

    iv = b"\x00" * 12
    ct, tag = aes_gcm_encrypt(content_key, iv, b"hello push")
    assert aes_gcm_decrypt(content_key, iv, ct, tag) == b"hello push"


def test_sign_verify():
    from server.app.crypto import generate_rsa_keypair, sign, verify

    priv, pub = generate_rsa_keypair()
    sig = sign(priv, b"ek||iv||ct")
    assert verify(pub, b"ek||iv||ct", sig)
    assert not verify(pub, b"ek||iv||Cx", sig)
