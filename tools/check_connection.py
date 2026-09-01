#!/usr/bin/env python3
"""Read-only connection check for Mitsubishi Motors Japan.

This script performs one login and reads the vehicle list plus cached charge and
climate state. It never wakes a vehicle or sends a remote command. Credentials
and tokens remain in process memory and are never printed or saved.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey


class CheckError(Exception):
    """A credential-free diagnostic error."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        raise CheckError("Redirect refused; credentials were not forwarded")


def load_protocol():
    path = (
        Path(__file__).parents[1]
        / "custom_components"
        / "mitsubishi_motors_jp"
        / "protocol.py"
    )
    spec = importlib.util.spec_from_file_location("mitsubishi_jp_protocol", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


protocol = load_protocol()
ALLOWED = {
    ("idm.prod.goa.mitsubishi-motors.com", "/api/v1/init"),
    ("idm.prod.goa.mitsubishi-motors.com", "/api/v1/login"),
    ("kintaro.prod.goa.mitsubishi-motors.com", protocol.KINTARO_INIT_PATH),
    (
        "kintaro.prod.goa.mitsubishi-motors.com",
        "/prod/vehicle/getVehicleList/v1",
    ),
    (
        "kintaro.prod.goa.mitsubishi-motors.com",
        "/prod/status/getChargeDetails/v1",
    ),
    (
        "kintaro.prod.goa.mitsubishi-motors.com",
        "/prod/status/getClimateDetails/v1",
    ),
}


class Transport:
    def __init__(self) -> None:
        self.opener = build_opener(NoRedirects())
        self.count = 0

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict | str | None = None,
    ) -> dict:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or (parsed.hostname, parsed.path) not in ALLOWED:
            raise CheckError("Request is outside the read-only allowlist")
        if isinstance(body, dict):
            raw = json.dumps(body, separators=(",", ":")).encode()
        elif isinstance(body, str):
            raw = body.encode()
        else:
            raw = None
        self.count += 1
        try:
            with self.opener.open(
                Request(url, data=raw, headers=headers, method=method), timeout=30
            ) as response:
                if response.status != 200:
                    raise CheckError(f"Service returned HTTP {response.status}")
                payload = response.read(1_048_577)
        except HTTPError as err:
            raise CheckError(f"Service returned HTTP {int(err.code)}") from None
        except (URLError, TimeoutError, OSError):
            raise CheckError("Network or TLS request failed") from None
        if len(payload) > 1_048_576:
            raise CheckError("Service response was unexpectedly large")
        try:
            value = json.loads(payload)
        except (UnicodeError, ValueError):
            raise CheckError("Service returned invalid JSON") from None
        if not isinstance(value, dict):
            raise CheckError("Service returned an invalid response")
        return value


def idm_headers(device_id: str) -> dict[str, str]:
    timestamp = str(int(time.time() * 1000))
    nonce = str(uuid.uuid4())
    signature = hashlib.md5(
        (protocol.IDM_CLIENT_ID + nonce + timestamp).encode(),
        usedforsecurity=False,
    ).hexdigest()
    return {
        "User-Agent": "MitsubishiOneApp/6 CFNetwork Darwin",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Content-Type": "application/json",
        "X-App-Name": "MitsubishiOneApp",
        "X-Timezone": "9",
        "X-Encrypt-Payload": "true",
        "X-Device": "iPhone",
        "X-Device-ID": device_id,
        "X-OS-Version": "18.0",
        "X-Client-ID": protocol.IDM_CLIENT_ID,
        "X-App-Unique-ID": protocol.KINTARO_PACKAGE,
        "X-Country": "JP",
        "X-App-Version": "3.0.0",
        "X-Client-Secret": protocol.IDM_CLIENT_SECRET,
        "X-Locale": "ja-JP",
        "X-OS-Type": "IOS",
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
    }


def idm_call(
    transport: Transport, device_id: str, path: str, body: dict
) -> dict:
    envelope = transport.request(
        "POST",
        protocol.IDM_BASE_URL + path,
        idm_headers(device_id),
        {"payload": protocol.idm_encrypt(body)},
    )
    try:
        result = protocol.idm_decrypt(envelope["payload"])
    except (KeyError, TypeError, ValueError):
        raise CheckError("IDM returned an invalid encrypted response") from None
    if str(result.get("code")) != "200" or not isinstance(result.get("data"), dict):
        raise CheckError("IDM request was refused")
    return result["data"]


def login(transport: Transport, username: str, password: str, device_id: str) -> str:
    init = idm_call(transport, device_id, "/api/v1/init", {})
    try:
        public_key = serialization.load_der_public_key(
            base64.b64decode(init["publicKey"], validate=True)
        )
        key_index = init["keyIndex"]
        if not isinstance(public_key, RSAPublicKey) or not isinstance(key_index, str):
            raise ValueError
        encrypted = public_key.encrypt(password.encode(), rsa_padding.PKCS1v15())
    except (KeyError, TypeError, ValueError):
        raise CheckError("IDM returned invalid login settings") from None
    data = idm_call(
        transport,
        device_id,
        "/api/v1/login",
        {
            "username": username,
            "password": key_index + "@" + base64.b64encode(encrypted).decode(),
        },
    )
    access = data.get("accessToken")
    if not isinstance(access, str) or not access:
        raise CheckError("Login returned no access token")
    return access


class Kintaro:
    def __init__(self, transport: Transport, access_token: str) -> None:
        self.transport = transport
        self.access_token = access_token
        self.correlation_id = str(uuid.uuid4())
        self.key: bytes | None = None
        self.sign_key: str | None = None

    def headers(
        self,
        ciphertext: str,
        sign_key: str,
        vehicle: dict[str, str] | None = None,
    ) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        headers = {
            "User-Agent": (
                "MitsubishiOneApp/3.0.0 "
                "(com.mitsubishi-motors.mitsubishimotors; build:6; iOS)"
            ),
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Content-Type": "text/plain",
            "Knt-Access-Token": self.access_token,
            "Knt-Correlation-Id": self.correlation_id,
            "Knt-Req-Id": str(uuid.uuid4()),
            "Knt-Timestamp": timestamp,
            "Knt-Sign": protocol.kintaro_signature(
                ciphertext, timestamp, sign_key
            ),
            "Knt-App-Key": protocol.KINTARO_APP_CODE,
            "Knt-App-Unique-Id": protocol.KINTARO_PACKAGE,
            "Knt-Region": "JP",
            "Knt-App-Os": "IOS",
            "Knt-Locale": "ja-JP",
            "Knt-Iso-Locale": "ja-JP",
            "Knt-Timezone": "Asia/Tokyo",
            "Knt-App-Version": "3.0.0",
            "Knt-App-Guest-Mode": "false",
            "Knt-User-Country": "JP",
            "Knt-Language": "ja",
            "Knt-Device-Os-Version": "18.0",
            "Knt-Device-Model-Code": "iPhone",
        }
        if vehicle:
            headers.update(
                {
                    "Knt-Vehicleyear": vehicle.get("year", ""),
                    "Knt-Vehicletype": "phev",
                    "Knt-Isxbadgeflag": "false",
                    "Knt-Vehiclemodel": vehicle.get("model", ""),
                    "Knt-Vehiclecolor": vehicle.get("color", ""),
                    "Knt-Primaryflag": vehicle.get("primary", "false"),
                }
            )
        return headers

    def initialize(self) -> None:
        ciphertext = protocol.kintaro_encrypt("{}", protocol.KINTARO_INIT_KEY)
        envelope = self.transport.request(
            "POST",
            protocol.KINTARO_BASE_URL + protocol.KINTARO_INIT_PATH,
            self.headers(ciphertext, protocol.KINTARO_INIT_SIGN_KEY),
            ciphertext,
        )
        try:
            data = protocol.kintaro_decrypt(
                envelope["payload"], protocol.KINTARO_INIT_KEY
            )
            key = data["encKey"].encode()
            sign_key = data["signKey"]
            if envelope.get("state") != "S" or len(key) != 16:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise CheckError("Kintaro initialization failed") from None
        self.key = key
        self.sign_key = sign_key

    def get(
        self,
        path: str,
        params: dict[str, str] | None = None,
        vehicle: dict[str, str] | None = None,
    ) -> dict:
        if self.key is None or self.sign_key is None:
            raise CheckError("Kintaro is not initialized")
        plaintext = "&".join(f"{key}={value}" for key, value in (params or {}).items())
        ciphertext = protocol.kintaro_encrypt(plaintext, self.key) if params else ""
        url = protocol.KINTARO_BASE_URL + path
        if params:
            url += "?" + urlencode({"params": ciphertext})
        envelope = self.transport.request(
            "GET", url, self.headers(ciphertext, self.sign_key, vehicle)
        )
        if envelope.get("state") != "S" or not isinstance(envelope.get("payload"), str):
            code = envelope.get("errorCode")
            safe_code = code if isinstance(code, int) else "unknown"
            raise CheckError(f"Kintaro request failed (code {safe_code})")
        try:
            return protocol.kintaro_decrypt(envelope["payload"], self.key)
        except (TypeError, ValueError):
            raise CheckError("Kintaro returned an invalid encrypted response") from None


def vehicle_selector(
    data: dict,
) -> tuple[dict[str, str], dict[str, str], int]:
    details = {
        item.get("vin"): item
        for item in data.get("vehicles", [])
        if isinstance(item, dict) and isinstance(item.get("vin"), str)
    }
    found = []
    for item in data.get("vinList", []):
        if not isinstance(item, dict):
            continue
        vin, internal = item.get("vin"), item.get("internalVin")
        if isinstance(vin, str) and vin and isinstance(internal, str) and internal:
            detail = details.get(vin, {})
            model = detail.get("model") if isinstance(detail.get("model"), dict) else {}
            color = (
                detail.get("exteriorColor")
                if isinstance(detail.get("exteriorColor"), dict)
                else {}
            )
            headers = {
                "year": model.get("modelYear", ""),
                "model": model.get("family", ""),
                "color": color.get("code", ""),
                "primary": str(detail.get("isPrimary") is True).lower(),
            }
            found.append(
                ({"vin": vin, "internalVin": internal}, headers)
            )
    if not found:
        raise CheckError("No compatible vehicle was returned")
    return found[0][0], found[0][1], len(found)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print("Read-only check: no wake-up or vehicle command will be sent.")
    print("Credentials stay in process memory. Do not paste them into chat.")
    try:
        username = getpass.getpass("Mitsubishi Motors email (hidden): ")
        password = getpass.getpass("Password (hidden): ")
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled. No credentials saved.", file=sys.stderr)
        return 130
    transport = Transport()
    try:
        access = login(transport, username, password, str(uuid.uuid4()))
        print("IDM login: success")
        client = Kintaro(transport, access)
        client.initialize()
        print("Kintaro initialization: success")
        selector, vehicle_headers, vehicle_count = vehicle_selector(
            client.get("/prod/vehicle/getVehicleList/v1")
        )
        charge = client.get(
            "/prod/status/getChargeDetails/v1", selector, vehicle_headers
        )
        climate = client.get(
            "/prod/status/getClimateDetails/v1", selector, vehicle_headers
        )
        print(
            json.dumps(
                {
                    "success": True,
                    "vehicles_found": vehicle_count,
                    "charge_state_received": "isCharging" in charge,
                    "climate_state_received": "isACOn" in climate,
                    "network_requests_sent": transport.count,
                    "vehicle_commands_sent": 0,
                    "credentials_or_tokens_saved": False,
                },
                indent=2,
            )
        )
        return 0
    except CheckError as err:
        print(str(err), file=sys.stderr)
        print(
            json.dumps(
                {
                    "success": False,
                    "network_requests_sent": transport.count,
                    "vehicle_commands_sent": 0,
                    "credentials_or_tokens_saved": False,
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1
    finally:
        username = ""
        password = ""


if __name__ == "__main__":
    raise SystemExit(main())
