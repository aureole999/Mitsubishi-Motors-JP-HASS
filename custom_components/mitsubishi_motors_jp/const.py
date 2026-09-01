"""Constants for Mitsubishi Motors Japan."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "mitsubishi_motors_jp"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_DEVICE_ID = "device_id"
CONF_UPDATE_INTERVAL = "update_interval"

DEFAULT_UPDATE_INTERVAL = 15
MIN_UPDATE_INTERVAL = 5

PLATFORMS: tuple[Platform, ...] = (
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.SENSOR,
)

RUNTIME_DATA = "runtime_data"

