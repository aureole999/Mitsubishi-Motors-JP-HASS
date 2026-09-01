"""Offline API state and command-safety tests."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).parents[1]
PACKAGE = ROOT / "custom_components" / "mitsubishi_motors_jp"
PACKAGE_NAME = "mitsubishi_motors_jp"


def _load(name: str):
    if PACKAGE_NAME not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            PACKAGE_NAME,
            PACKAGE / "__init__.py",
            submodule_search_locations=[str(PACKAGE)],
        )
        package = importlib.util.module_from_spec(spec)
        sys.modules[PACKAGE_NAME] = package
        package.__path__ = [str(PACKAGE)]
    full = f"{PACKAGE_NAME}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, PACKAGE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


_load("protocol")
models = _load("models")

# Home Assistant includes aiohttp. The standalone test environment may not, so
# provide only the import surface needed by these fully offline unit tests.
if "aiohttp" not in sys.modules:
    aiohttp = types.ModuleType("aiohttp")
    aiohttp.ClientError = type("ClientError", (Exception,), {})
    aiohttp.ClientResponse = type("ClientResponse", (), {})
    aiohttp.ClientSession = type("ClientSession", (), {})
    aiohttp.ClientTimeout = type(
        "ClientTimeout", (), {"__init__": lambda self, **kwargs: None}
    )
    sys.modules["aiohttp"] = aiohttp
api = _load("api")


class APIHelperTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = api.MitsubishiJPClient(
            session=object(),
            username="user@example.invalid",
            password="unused",
            device_id="00000000-0000-0000-0000-000000000000",
        )
        self.vehicle = models.Vehicle("0123456789ABCDEFG", "internal")

    async def test_poll_uses_only_matching_request_id(self):
        self.client._async_kintaro_post = AsyncMock(
            return_value={
                "requestStatusList": [
                    {"requestId": "other", "status": 2},
                    {"requestId": "wanted", "status": 2},
                ]
            }
        )
        with patch.object(api.asyncio, "sleep", AsyncMock()):
            result = await self.client._async_poll_request(
                {"requestId": "wanted", "duration": 5, "pollingInterval": 2},
                self.vehicle,
                default_interval=2,
            )
        self.assertEqual(result.request_id, "wanted")
        self.assertEqual(result.polls, 1)

    async def test_poll_timeout_is_unknown(self):
        self.client._async_kintaro_post = AsyncMock(
            return_value={"requestStatusList": [{"requestId": "wanted", "status": 1}]}
        )
        with patch.object(api.asyncio, "sleep", AsyncMock()):
            with self.assertRaises(api.MitsubishiJPCommandUnknown):
                await self.client._async_poll_request(
                    {"requestId": "wanted", "duration": 5, "pollingInterval": 5},
                    self.vehicle,
                    default_interval=5,
                )

    async def test_control_session_error_is_rejected_and_not_retried(self):
        self.client._session_key = b"1234567890123456"
        self.client._session_sign_key = "sign"
        self.client._correlation_id = "correlation"
        self.client._access_token = "access"
        self.client._access_expires_at = api.time.monotonic() + 3600
        self.client._async_request = AsyncMock(
            return_value={"state": "F", "errorCode": 600001}
        )
        self.client._async_initialize_kintaro = AsyncMock()
        with self.assertRaises(api.MitsubishiJPCommandError):
            await self.client._async_kintaro_post(
                "/prod/remote/startClimate/v1",
                {"action": "double_start"},
                vehicle=self.vehicle,
                is_control=True,
            )
        self.assertEqual(self.client._async_request.await_count, 1)
        self.client._async_initialize_kintaro.assert_not_awaited()

    async def test_explicit_control_rejection_is_not_reported_as_unknown(self):
        self.client._session_key = b"1234567890123456"
        self.client._session_sign_key = "sign"
        self.client._correlation_id = "correlation"
        self.client._access_token = "access"
        self.client._access_expires_at = api.time.monotonic() + 3600
        self.client._async_request = AsyncMock(
            return_value={"state": "F", "errorCode": 123456}
        )
        with self.assertRaisesRegex(api.MitsubishiJPCommandError, "123456"):
            await self.client._async_kintaro_post(
                "/prod/remote/startClimate/v1",
                {"action": "double_start"},
                vehicle=self.vehicle,
                is_control=True,
            )
        self.assertEqual(self.client._async_request.await_count, 1)

    async def test_control_network_failure_is_unknown_and_not_retried(self):
        self.client._session_key = b"1234567890123456"
        self.client._session_sign_key = "sign"
        self.client._correlation_id = "correlation"
        self.client._access_token = "access"
        self.client._access_expires_at = api.time.monotonic() + 3600
        self.client._async_request = AsyncMock(
            side_effect=api.MitsubishiJPConnectionError("network unavailable")
        )
        with self.assertRaisesRegex(
            api.MitsubishiJPCommandUnknown, "Check the official app"
        ):
            await self.client._async_kintaro_post(
                "/prod/remote/startClimate/v1",
                {"action": "double_start"},
                vehicle=self.vehicle,
                is_control=True,
            )
        self.assertEqual(self.client._async_request.await_count, 1)

    async def test_start_is_not_sent_if_vehicle_never_finishes_waking(self):
        self.client._async_ensure_kintaro = AsyncMock()
        self.client._async_kintaro_post = AsyncMock(
            side_effect=[
                {"requestId": "refresh", "duration": 80, "pollingInterval": 2},
                {"wakeUpDuration": 60},
            ]
        )
        self.client._async_poll_request = AsyncMock(return_value=None)
        with self.assertRaisesRegex(
            api.MitsubishiJPCommandError, "START was not sent"
        ):
            await self.client.async_start_climate(self.vehicle)
        self.assertEqual(self.client._async_kintaro_post.await_count, 2)
        paths = [call.args[0] for call in self.client._async_kintaro_post.await_args_list]
        self.assertNotIn("/prod/remote/startClimate/v1", paths)

    async def test_refresh_token_callback_receives_rotated_token(self):
        callback = AsyncMock()
        self.client._token_callback = callback
        await self.client._async_store_tokens(
            {"accessToken": "access", "refreshToken": "rotated", "expiresIn": 7200}
        )
        self.assertEqual(self.client.refresh_token, "rotated")
        callback.assert_awaited_once_with("rotated")


if __name__ == "__main__":
    unittest.main()
