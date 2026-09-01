"""Cryptographic primitives and public app protocol constants.

The service credentials below are application identifiers extracted from the
official public Mitsubishi Motors iOS application. They are not user account
credentials. Never log payloads: encrypted bodies contain user and vehicle data.
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

# Static SDK transport constant used by the public app.
IDM_SDK_AES_KEY = b"F)J@NcRfUjXn2r4u7x!A%D*G-KaPdSgV"

IDM_BASE_URL = "https://idm.prod.goa.mitsubishi-motors.com"
KINTARO_BASE_URL = "https://kintaro.prod.goa.mitsubishi-motors.com"
KINTARO_INIT_PATH = "/prod/service/checkVersion"

IDM_CLIENT_ID = 'Rn6WysiaR7xDrz39If3d3foQsuHLAjiw'
IDM_CLIENT_SECRET = 'mZ6nfF2gsdpY8cqBJYKAQt0tKNexOBItEvNLkHclF2Sn3Egf3FwtYnCdSldLT8XE'
KINTARO_APP_CODE = '202411201314218756108'
KINTARO_PACKAGE = 'com.mitsubishi-motors.mitsubishimotors'
KINTARO_INDICES = (7, 23, 2, 24, 13, 21, 20, 21, 6, 3)
KINTARO_IV = b'6e197dc0aa6e9e30'
KINTARO_INIT_KEY = b'b254c98f8ac8d861'
KINTARO_INIT_SIGN_KEY = '13858b254c98f8ac8d86198f8ac8d88'


def _pad(value: bytes) -> bytes:
    padder = PKCS7(128).padder()
    return padder.update(value) + padder.finalize()


def _unpad(value: bytes) -> bytes:
    unpadder = PKCS7(128).unpadder()
    return unpadder.update(value) + unpadder.finalize()


def idm_encrypt(value: dict[str, Any]) -> str:
    """Encrypt an IDM JSON request."""
    raw = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
    encryptor = Cipher(algorithms.AES(IDM_SDK_AES_KEY), modes.ECB()).encryptor()
    return base64.b64encode(encryptor.update(_pad(raw)) + encryptor.finalize()).decode()


def idm_decrypt(payload: str) -> dict[str, Any]:
    """Decrypt an IDM JSON response."""
    decryptor = Cipher(algorithms.AES(IDM_SDK_AES_KEY), modes.ECB()).decryptor()
    raw = decryptor.update(base64.b64decode(payload, validate=True)) + decryptor.finalize()
    value = json.loads(_unpad(raw))
    if not isinstance(value, dict):
        raise ValueError("IDM payload is not an object")
    return value


def kintaro_encrypt(plaintext: str, key: bytes) -> str:
    """Encrypt a Kintaro request."""
    encryptor = Cipher(algorithms.AES(key), modes.CBC(KINTARO_IV)).encryptor()
    return base64.b64encode(
        encryptor.update(_pad(plaintext.encode())) + encryptor.finalize()
    ).decode()


def kintaro_decrypt(ciphertext: str, key: bytes) -> dict[str, Any]:
    """Decrypt a Kintaro JSON response."""
    decryptor = Cipher(algorithms.AES(key), modes.CBC(KINTARO_IV)).decryptor()
    raw = decryptor.update(base64.b64decode(ciphertext, validate=True)) + decryptor.finalize()
    value = json.loads(_unpad(raw))
    if not isinstance(value, dict):
        raise ValueError("Kintaro payload is not an object")
    return value


def kintaro_signature(ciphertext: str, timestamp: str, sign_key: str) -> str:
    """Build the request signature used by the Japanese Kintaro service."""
    value = (
        ciphertext
        + timestamp
        + timestamp[KINTARO_INDICES[8] :]
        + timestamp[KINTARO_INDICES[9] :]
        + sign_key
    )
    return hashlib.sha256(value.encode()).hexdigest().upper()

