"""Mitsubishi Motors Japan integration."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MitsubishiJPAuthError, MitsubishiJPClient, MitsubishiJPError
from .const import (
    CONF_DEVICE_ID,
    CONF_REFRESH_TOKEN,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import MitsubishiJPCoordinator


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload only when polling options changed, not when a token rotates."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is None:
        return
    wanted = int(entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))
    if runtime.get("update_interval") != wanted:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Japanese Mitsubishi Motors integration."""

    async def save_refresh_token(refresh_token: str) -> None:
        if entry.data.get(CONF_REFRESH_TOKEN) == refresh_token:
            return
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_REFRESH_TOKEN: refresh_token}
        )

    client = MitsubishiJPClient(
        async_get_clientsession(hass),
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        device_id=entry.data[CONF_DEVICE_ID],
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        token_callback=save_refresh_token,
    )
    try:
        await client.async_initialize()
        vehicles = await client.async_get_vehicles()
    except MitsubishiJPAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except MitsubishiJPError as err:
        raise ConfigEntryNotReady(str(err)) from err
    if not vehicles:
        raise ConfigEntryNotReady("No Japanese Mitsubishi vehicles were returned")

    minutes = int(entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))
    coordinators: dict[str, MitsubishiJPCoordinator] = {}
    for vehicle in vehicles:
        coordinator = MitsubishiJPCoordinator(
            hass, client, vehicle, timedelta(minutes=minutes)
        )
        try:
            await coordinator.async_config_entry_first_refresh()
        except MitsubishiJPAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        coordinators[vehicle.vin] = coordinator

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "vehicles": vehicles,
        "coordinators": coordinators,
        "update_interval": minutes,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
