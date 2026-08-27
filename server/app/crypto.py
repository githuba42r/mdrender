# server/app/crypto.py
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

GCM_TAG_LENGTH = 16


def generate_rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    return private_key, private_key.public_key()


def private_to_pem(key):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def public_to_spki_der(key):
    return key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def public_from_spki_der(der: bytes):
    return serialization.load_der_public_key(der)


def public_from_spki_pem(pem: bytes):
    return serialization.load_pem_public_key(pem)


def oaep_wrap(public_key, data: bytes) -> bytes:
    return public_key.encrypt(data, padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None,
    ))


def oaep_unwrap(private_key, data: bytes) -> bytes:
    return private_key.decrypt(data, padding.OAEP(
        mgf=padding.MGF1(hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None,
    ))


def aes_gcm_encrypt(key: bytes, iv: bytes, plaintext: bytes):
    encryptor = Cipher(algorithms.AES(key), modes.GCM(iv)).encryptor()
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()
    return ciphertext, encryptor.tag


def aes_gcm_decrypt(key: bytes, iv: bytes, ciphertext: bytes, tag: bytes):
    decryptor = Cipher(algorithms.AES(key), modes.GCM(iv, tag)).decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def sign(private_key, data: bytes) -> bytes:
    return private_key.sign(data, padding.PKCS1v15(), hashes.SHA256())


def verify(public_key, data: bytes, sig: bytes) -> bool:
    try:
        public_key.verify(sig, data, padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False
