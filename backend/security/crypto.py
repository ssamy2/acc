import os
import secrets
import string
from typing import Tuple
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.backends import default_backend

def generate_strong_password(length: int = 32) -> str:
    """
    Generates a cryptographically strong random password.
    Mix of letters, digits, and punctuation.
    """
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def load_public_key(pem_data: bytes):
    return serialization.load_pem_public_key(
        pem_data,
        backend=default_backend()
    )

def encrypt_data(data: str, public_key_pem: bytes) -> bytes:
    """
    Encrypts string data using RSA-2048 Public Key.
    Returns raw bytes.
    """
    public_key = load_public_key(public_key_pem)
    encrypted = public_key.encrypt(
        data.encode('utf-8'),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )
    return encrypted

class KeyManager:
    """
    Utility to manage keys. 
    In Checks: The Worker only needs the PUBLIC key.
    """
    @staticmethod
    def get_public_key_from_file(path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()
