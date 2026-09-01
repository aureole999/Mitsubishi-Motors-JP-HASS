"""Remote climate control for Japanese Mitsubishi vehicles."""
from __future__ import annotations

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature
from homeassistant.components.climate.const import HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import MitsubishiJPCommandUnknown, MitsubishiJPError
from .const import DOMAIN
from .coordinator import MitsubishiJPCoordinator, MitsubishiJPEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        MitsubishiJPClimate(coordinator)
        for coordinator in data["coordinators"].values()
    )


class MitsubishiJPClimate(MitsubishiJPEntity, ClimateEntity):
    """A start/stop entity for the verified Japanese preconditioning flow."""

    _attr_translation_key = "remote_climate"
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT_COOL]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
    _attr_icon = "mdi:car-defrost-front"

    def __init__(self, coordinator: MitsubishiJPCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.vehicle.vin}_remote_climate"

    @property
    def hvac_mode(self) -> HVACMode:
        state = self.vehicle_state
        return HVACMode.HEAT_COOL if state and state.ac_on else HVACMode.OFF

    @property
    def target_temperature(self) -> float | None:
        state = self.vehicle_state
        return state.target_temperature if state else None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
        elif hvac_mode == HVACMode.HEAT_COOL:
            await self.async_turn_on()
        else:
            raise HomeAssistantError("Unsupported climate mode")

    async def async_turn_on(self) -> None:
        try:
            await self.coordinator.client.async_start_climate(self.vehicle)
        except MitsubishiJPCommandUnknown as err:
            raise HomeAssistantError(
                "Climate start outcome is unknown. Check the official app; "
                "Home Assistant did not retry it."
            ) from err
        except MitsubishiJPError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        try:
            await self.coordinator.client.async_stop_climate(self.vehicle)
        except MitsubishiJPCommandUnknown as err:
            raise HomeAssistantError(
                "Climate stop outcome is unknown. Check the official app; "
                "Home Assistant did not retry it."
            ) from err
        except MitsubishiJPError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()
