"""Async client for the Japanese Mitsubishi Motors cloud service."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from .models import CommandResult, Vehicle, VehicleState
from .protocol import (
    IDM_BASE_URL,
    IDM_CLIENT_ID,
    IDM_CLIENT_SECRET,
    KINTARO_APP_CODE,
    KINTARO_BASE_URL,
    KINTARO_INIT_KEY,
    KINTARO_INIT_PATH,
    KINTARO_INIT_SIGN_KEY,
    KINTARO_PACKAGE,
    idm_decrypt,
    idm_encrypt,
    kintaro_decrypt,
    kintaro_encrypt,
    kintaro_signature,
)

TokenCallback = Callable[[str], Awaitable[None]]


class MitsubishiJPError(Exception):
    """Base exception. Messages must never contain response bodies or secrets."""


class MitsubishiJPAuthError(MitsubishiJPError):
    """Authentication failed."""


class MitsubishiJPConnectionError(MitsubishiJPError):
    """The service could not be reached safely."""


class MitsubishiJPCommandError(MitsubishiJPError):
    """The vehicle confirmed that a command failed."""


class MitsubishiJPCommandUnknown(MitsubishiJPError):
    """A command may have reached the vehicle, so it must not be retried."""


class _KintaroSessionExpired(MitsubishiJPError):
    """Kintaro session needs initialization."""


class _KintaroRejected(MitsubishiJPError):
    """Kintaro explicitly rejected a request before accepting it."""

    def __init__(self, code: int | str) -> None:
        super().__init__(f"Kintaro request failed (code {code})")
        self.code = code


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_text(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on", "ready"}:
            return True
        if lowered in {"false", "0", "no", "off", "not_ready"}:
            return False
    return None


class MitsubishiJPClient:
    """Client for the GOA IDM and Japanese Kintaro APIs."""

    def __init__(
        self,
        session: ClientSession,
        username: str,
        password: str,
        device_id: str,
        refresh_token: str | None = None,
        token_callback: TokenCallback | None = None,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._device_id = device_id
        self._refresh_token = refresh_token
        self._token_callback = token_callback
        self._access_token: str | None = None
        self._access_expires_at = 0.0
        self._correlation_id: str | None = None
        self._session_key: bytes | None = None
        self._session_sign_key: str | None = None
        self._auth_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()

    @property
    def refresh_token(self) -> str | None:
        """Return the current rotating refresh token."""
        return self._refresh_token

    async def async_authenticate(self) -> None:
        """Authenticate once, preferring the saved refresh token."""
        async with self._auth_lock:
            if self._access_token and time.monotonic() < self._access_expires_at - 300:
                return
            if self._refresh_token:
                try:
                    await self._async_refresh_access_token()
                except MitsubishiJPAuthError:
                    self._refresh_token = None
                    await self._async_login()
            else:
                await self._async_login()

    async def _async_ensure_access_token(self) -> None:
        if not self._access_token or time.monotonic() >= self._access_expires_at - 300:
            await self.async_authenticate()

    def _idm_headers(self) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        nonce = str(uuid.uuid4())
        signature = hashlib.md5(
            (IDM_CLIENT_ID + nonce + timestamp).encode(), usedforsecurity=False
        ).hexdigest()
        return {
            "User-Agent": "MitsubishiOneApp/6 CFNetwork Darwin",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
            "X-App-Name": "MitsubishiOneApp",
            "X-Timezone": "9",
            "X-Encrypt-Payload": "true",
            "X-Device": "iPhone",
            "X-Device-ID": self._device_id,
            "X-OS-Version": "18.0",
            "X-Client-ID": IDM_CLIENT_ID,
            "X-App-Unique-ID": KINTARO_PACKAGE,
            "X-Country": "JP",
            "X-App-Version": "3.0.0",
            "X-Client-Secret": IDM_CLIENT_SECRET,
            "X-Locale": "ja-JP",
            "X-OS-Type": "IOS",
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "X-Signature": signature,
        }

    async def _async_idm_call(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = await self._async_request(
            "POST",
            IDM_BASE_URL + path,
            headers=self._idm_headers(),
            json_body={"payload": idm_encrypt(body)},
        )
        try:
            value = idm_decrypt(response["payload"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as err:
            raise MitsubishiJPConnectionError(
                "IDM returned an invalid encrypted response"
            ) from err
        if str(value.get("code")) != "200" or not isinstance(value.get("data"), dict):
            if path in {"/api/v1/login", "/api/v1/refreshToken"}:
                raise MitsubishiJPAuthError("Mitsubishi Motors authentication was refused")
            raise MitsubishiJPConnectionError("IDM initialization was refused")
        return value["data"]

    async def _async_login(self) -> None:
        init = await self._async_idm_call("/api/v1/init", {})
        try:
            public_key = serialization.load_der_public_key(
                base64.b64decode(init["publicKey"], validate=True)
            )
            key_index = init["keyIndex"]
            if not isinstance(public_key, RSAPublicKey) or not isinstance(key_index, str):
                raise ValueError("invalid RSA settings")
            password_ciphertext = public_key.encrypt(
                self._password.encode(), rsa_padding.PKCS1v15()
            )
        except (KeyError, TypeError, ValueError) as err:
            raise MitsubishiJPConnectionError("IDM login settings were invalid") from err
        data = await self._async_idm_call(
            "/api/v1/login",
            {
                "username": self._username,
                "password": key_index + "@" + base64.b64encode(password_ciphertext).decode(),
            },
        )
        await self._async_store_tokens(data)

    async def _async_refresh_access_token(self) -> None:
        if not self._refresh_token:
            raise MitsubishiJPAuthError("No refresh token is available")
        data = await self._async_idm_call(
            "/api/v1/refreshToken", {"refreshToken": self._refresh_token}
        )
        await self._async_store_tokens(data)

    async def _async_store_tokens(self, data: dict[str, Any]) -> None:
        access = data.get("accessToken")
        refresh = data.get("refreshToken")
        expires = _number(data.get("expiresIn"))
        if (
            not isinstance(access, str)
            or not access
            or not isinstance(refresh, str)
            or not refresh
        ):
            raise MitsubishiJPAuthError("Authentication returned incomplete tokens")
        if expires is None or not 60 <= expires <= 86400:
            raise MitsubishiJPAuthError("Authentication returned an invalid token lifetime")
        self._access_token = access
        self._refresh_token = refresh
        self._access_expires_at = time.monotonic() + expires
        if self._token_callback:
            await self._token_callback(refresh)

    async def async_initialize(self) -> None:
        """Initialize authentication and the encrypted Kintaro session."""
        await self.async_authenticate()
        await self._async_initialize_kintaro()

    async def _async_initialize_kintaro(self) -> None:
        async with self._session_lock:
            if self._session_key and self._session_sign_key and self._correlation_id:
                return
            await self._async_ensure_access_token()
            correlation_id = str(uuid.uuid4())
            ciphertext = kintaro_encrypt("{}", KINTARO_INIT_KEY)
            headers = self._kintaro_headers(
                ciphertext,
                KINTARO_INIT_SIGN_KEY,
                correlation_id=correlation_id,
            )
            envelope = await self._async_request(
                "POST",
                KINTARO_BASE_URL + KINTARO_INIT_PATH,
                headers=headers,
                raw_body=ciphertext,
            )
            if envelope.get("state") != "S" or not isinstance(envelope.get("payload"), str):
                raise MitsubishiJPConnectionError("Kintaro initialization was refused")
            try:
                data = kintaro_decrypt(envelope["payload"], KINTARO_INIT_KEY)
                key = data["encKey"].encode()
                sign_key = data["signKey"]
                if len(key) != 16 or not isinstance(sign_key, str) or not sign_key:
                    raise ValueError("invalid session keys")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as err:
                raise MitsubishiJPConnectionError("Kintaro session response was invalid") from err
            self._correlation_id = correlation_id
            self._session_key = key
            self._session_sign_key = sign_key

    def _kintaro_headers(
        self,
        ciphertext: str,
        sign_key: str,
        *,
        correlation_id: str | None = None,
        vehicle: Vehicle | None = None,
    ) -> dict[str, str]:
        if not self._access_token:
            raise MitsubishiJPAuthError("No access token is available")
        timestamp = str(int(time.time() * 1000))
        headers = {
            "User-Agent": (
                "MitsubishiOneApp/3.0.0 "
                "(com.mitsubishi-motors.mitsubishimotors; build:6; iOS)"
            ),
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Content-Type": "text/plain",
            "Knt-Access-Token": self._access_token,
            "Knt-Correlation-Id": correlation_id or self._correlation_id or "",
            "Knt-Req-Id": str(uuid.uuid4()),
            "Knt-Timestamp": timestamp,
            "Knt-Sign": kintaro_signature(ciphertext, timestamp, sign_key),
            "Knt-App-Key": KINTARO_APP_CODE,
            "Knt-App-Unique-Id": KINTARO_PACKAGE,
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
                    "Knt-Vehicleyear": vehicle.model_year or "",
                    "Knt-Vehicletype": vehicle.vehicle_type,
                    "Knt-Isxbadgeflag": "false",
                    "Knt-Vehiclemodel": vehicle.model or "",
                    "Knt-Vehiclecolor": vehicle.color or "",
                    "Knt-Primaryflag": str(vehicle.is_primary).lower(),
                }
            )
        return headers

    async def _async_ensure_kintaro(self) -> None:
        await self._async_ensure_access_token()
        if not self._session_key or not self._session_sign_key or not self._correlation_id:
            await self._async_initialize_kintaro()

    async def _async_kintaro_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        vehicle: Vehicle | None = None,
        retry_session: bool = True,
    ) -> dict[str, Any]:
        await self._async_ensure_kintaro()
        assert self._session_key is not None and self._session_sign_key is not None
        plaintext = "&".join(f"{key}={value}" for key, value in (params or {}).items())
        ciphertext = kintaro_encrypt(plaintext, self._session_key) if params else ""
        url = KINTARO_BASE_URL + path
        if params:
            url += "?" + urlencode({"params": ciphertext})
        try:
            envelope = await self._async_request(
                "GET",
                url,
                headers=self._kintaro_headers(
                    ciphertext, self._session_sign_key, vehicle=vehicle
                ),
            )
            return self._decode_kintaro(envelope)
        except _KintaroSessionExpired:
            if not retry_session:
                raise MitsubishiJPConnectionError("Kintaro session expired") from None
            self._session_key = self._session_sign_key = self._correlation_id = None
            await self._async_initialize_kintaro()
            return await self._async_kintaro_get(
                path, params, vehicle=vehicle, retry_session=False
            )

    async def _async_kintaro_post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        vehicle: Vehicle | None = None,
        is_control: bool = False,
        retry_session: bool = True,
    ) -> dict[str, Any]:
        await self._async_ensure_kintaro()
        assert self._session_key is not None and self._session_sign_key is not None
        plaintext = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        ciphertext = kintaro_encrypt(plaintext, self._session_key)
        try:
            envelope = await self._async_request(
                "POST",
                KINTARO_BASE_URL + path,
                headers=self._kintaro_headers(
                    ciphertext, self._session_sign_key, vehicle=vehicle
                ),
                raw_body=ciphertext,
            )
            return self._decode_kintaro(envelope)
        except _KintaroSessionExpired:
            if is_control or not retry_session:
                error = (
                    MitsubishiJPCommandError(
                        "Remote command was rejected because the service session "
                        "expired; it was not retried"
                    )
                    if is_control
                    else MitsubishiJPConnectionError("Kintaro session expired")
                )
                raise error from None
            self._session_key = self._session_sign_key = self._correlation_id = None
            await self._async_initialize_kintaro()
            return await self._async_kintaro_post(
                path, body, vehicle=vehicle, retry_session=False
            )
        except _KintaroRejected as err:
            if is_control:
                raise MitsubishiJPCommandError(
                    f"Remote command was rejected by the service (code {err.code}); "
                    "it was not retried"
                ) from err
            raise
        except (MitsubishiJPConnectionError, asyncio.TimeoutError) as err:
            if is_control:
                raise MitsubishiJPCommandUnknown(
                    "Remote command outcome is unknown; it was not retried. "
                    "Check the official app"
                ) from err
            raise

    def _decode_kintaro(self, envelope: dict[str, Any]) -> dict[str, Any]:
        if envelope.get("state") != "S" or not isinstance(
            envelope.get("payload"), str
        ):
            code = envelope.get("errorCode")
            if code == 600001:
                raise _KintaroSessionExpired
            safe_code = code if isinstance(code, int) and 0 <= code <= 999999 else "unknown"
            raise _KintaroRejected(safe_code)
        assert self._session_key is not None
        try:
            return kintaro_decrypt(envelope["payload"], self._session_key)
        except (TypeError, ValueError, json.JSONDecodeError) as err:
            raise MitsubishiJPConnectionError(
                "Kintaro returned an invalid encrypted response"
            ) from err

    async def _async_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None = None,
        raw_body: str | None = None,
    ) -> dict[str, Any]:
        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                json=json_body,
                data=raw_body,
                allow_redirects=False,
                timeout=ClientTimeout(total=30),
            ) as response:
                return await self._read_json_response(response)
        except asyncio.TimeoutError:
            raise
        except (ClientError, OSError) as err:
            raise MitsubishiJPConnectionError("Mitsubishi Motors service is unavailable") from err

    @staticmethod
    async def _read_json_response(response: ClientResponse) -> dict[str, Any]:
        if response.status != 200:
            raise MitsubishiJPConnectionError(
                f"Mitsubishi Motors service returned HTTP {response.status}"
            )
        try:
            raw = await response.content.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError("response too large")
            value = json.loads(raw)
        except (ClientError, UnicodeError, json.JSONDecodeError, ValueError) as err:
            raise MitsubishiJPConnectionError("Mitsubishi Motors returned invalid JSON") from err
        if not isinstance(value, dict):
            raise MitsubishiJPConnectionError("Mitsubishi Motors returned an invalid response")
        return value

    async def async_get_vehicles(self) -> list[Vehicle]:
        """Return vehicles attached to the account."""
        data = await self._async_kintaro_get("/prod/vehicle/getVehicleList/v1")
        internal = {
            item.get("vin"): item.get("internalVin")
            for item in data.get("vinList", [])
            if isinstance(item, dict)
        }
        vehicles: list[Vehicle] = []
        for item in data.get("vehicles", []):
            if not isinstance(item, dict):
                continue
            vin = item.get("vin")
            internal_vin = internal.get(vin)
            if (
                not isinstance(vin, str)
                or not vin
                or not isinstance(internal_vin, str)
            ):
                continue
            model = (
                item.get("model") if isinstance(item.get("model"), dict) else {}
            )
            color = (
                item.get("exteriorColor")
                if isinstance(item.get("exteriorColor"), dict)
                else {}
            )
            vehicles.append(
                Vehicle(
                    vin=vin,
                    internal_vin=internal_vin,
                    model=(
                        model.get("family")
                        if isinstance(model.get("family"), str)
                        else None
                    ),
                    model_year=(
                        model.get("modelYear")
                        if isinstance(model.get("modelYear"), str)
                        else None
                    ),
                    color=(
                        color.get("code")
                        if isinstance(color.get("code"), str)
                        else None
                    ),
                    is_primary=item.get("isPrimary") is True,
                )
            )
        return vehicles

    @staticmethod
    def _selector(vehicle: Vehicle) -> dict[str, str]:
        return {"vin": vehicle.vin, "internalVin": vehicle.internal_vin}

    async def async_get_vehicle_state(self, vehicle: Vehicle) -> VehicleState:
        """Read cached status without waking the vehicle."""
        selector = self._selector(vehicle)
        charge, climate = await asyncio.gather(
            self._async_kintaro_get(
                "/prod/status/getChargeDetails/v1", selector, vehicle=vehicle
            ),
            self._async_kintaro_get(
                "/prod/status/getClimateDetails/v1", selector, vehicle=vehicle
            ),
        )
        battery = _number(charge.get("hvBatteryLife"))
        charge_time = _number(charge.get("hvTimeToFullCharge"))
        temperature = _number(climate.get("targetTemperature"))
        timestamps = [
            value
            for value in (charge.get("timestamp"), climate.get("timestamp"))
            if isinstance(value, str)
        ]
        return VehicleState(
            battery_level=battery if battery is None or 0 <= battery <= 100 else None,
            is_charging=_bool_text(charge.get("isCharging")),
            is_plugged_in=_bool_text(charge.get("isPluggedIn")),
            charging_ready=_bool_text(charge.get("hvChargingReady")),
            charge_disabled=_bool_text(charge.get("isDisableStart")),
            minutes_to_full_charge=(
                int(charge_time)
                if charge_time is not None and charge_time >= 0
                else None
            ),
            ac_on=_bool_text(climate.get("isACOn")),
            target_temperature=temperature,
            temperature_unit=(
                climate.get("temperatureUnit")
                if climate.get("temperatureUnit") in {"C", "F"}
                else None
            ),
            updated_at=max(timestamps) if timestamps else None,
        )

    async def _async_poll_request(
        self,
        response: dict[str, Any],
        vehicle: Vehicle,
        *,
        default_interval: int,
        max_wait: float | None = None,
        pending_is_ok: bool = False,
        command_was_sent: bool = False,
    ) -> CommandResult | None:
        request_id = response.get("requestId")
        if not isinstance(request_id, str) or not request_id:
            raise MitsubishiJPCommandUnknown("Command returned no request ID; outcome is unknown")
        duration = _number(response.get("duration")) or 80
        interval = _number(response.get("pollingInterval")) or default_interval
        if not 2 <= interval <= 10:
            interval = default_interval
        if not 5 <= duration <= 120:
            duration = 80
        limit = min(24, max(1, math.ceil(duration / interval)))
        started = time.monotonic()
        polls = 0
        for _ in range(limit):
            if max_wait is not None:
                remaining = max_wait - (time.monotonic() - started)
                if remaining <= 0:
                    break
                await asyncio.sleep(min(interval, remaining))
            else:
                await asyncio.sleep(interval)
            try:
                result = await self._async_kintaro_post(
                    "/prod/remote/getBatchRequestStatus/v1",
                    {"requestIdList": [{"requestId": request_id}]},
                    vehicle=vehicle,
                )
            except (MitsubishiJPError, asyncio.TimeoutError) as err:
                if command_was_sent:
                    raise MitsubishiJPCommandUnknown(
                        "Command result could not be checked; the command was not "
                        "retried. Check the official app"
                    ) from err
                raise
            polls += 1
            for item in result.get("requestStatusList", []):
                if not isinstance(item, dict) or item.get("requestId") != request_id:
                    continue
                if item.get("status") == 2:
                    return CommandResult(request_id=request_id, polls=polls)
                if item.get("status") != 1:
                    raise MitsubishiJPCommandError("Vehicle reported that the command failed")
        if pending_is_ok:
            return None
        raise MitsubishiJPCommandUnknown(
            "Command result timed out; the command was not retried. Check the "
            "official app"
        )

    async def async_start_climate(self, vehicle: Vehicle) -> CommandResult:
        """Wake the vehicle and start climate once at the verified 25 °C setting."""
        async with self._command_lock:
            await self._async_ensure_kintaro()
            selector = self._selector(vehicle)
            refresh = await self._async_kintaro_post(
                "/prod/status/refreshVSR/v1",
                {**selector, "refreshType": 0},
                vehicle=vehicle,
            )
            wake = await self._async_kintaro_post(
                "/prod/vehicle/wakeUpVehicle/v1", selector, vehicle=vehicle
            )
            wake_wait = _number(wake.get("wakeUpDuration")) or 60
            if not 15 <= wake_wait <= 90:
                wake_wait = 60
            refresh_result = await self._async_poll_request(
                refresh,
                vehicle,
                default_interval=2,
                max_wait=wake_wait,
                pending_is_ok=True,
            )
            if refresh_result is None:
                raise MitsubishiJPCommandError(
                    "Vehicle did not finish waking before the timeout; climate "
                    "START was not sent"
                )
            try:
                response = await self._async_kintaro_post(
                    "/prod/remote/startClimate/v1",
                    {
                        "temperatureUnit": "C",
                        "vin": vehicle.vin,
                        "action": "double_start",
                        "targetTemperature": "25.0",
                        "startFlag": 0,
                        "hvacSettings": {},
                        "internalVin": vehicle.internal_vin,
                    },
                    vehicle=vehicle,
                    is_control=True,
                )
            except MitsubishiJPCommandUnknown:
                raise
            result = await self._async_poll_request(
                response, vehicle, default_interval=5, command_was_sent=True
            )
            assert result is not None
            return result

    async def async_stop_climate(self, vehicle: Vehicle) -> CommandResult:
        """Stop climate once and wait for its own confirmed result."""
        async with self._command_lock:
            try:
                response = await self._async_kintaro_post(
                    "/prod/remote/stopClimate/v1",
                    {
                        "cancelFlag": 0,
                        "vin": vehicle.vin,
                        "internalVin": vehicle.internal_vin,
                    },
                    vehicle=vehicle,
                    is_control=True,
                )
            except MitsubishiJPCommandUnknown:
                raise
            result = await self._async_poll_request(
                response, vehicle, default_interval=5, command_was_sent=True
            )
            assert result is not None
            return result
