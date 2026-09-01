"""Remote climate control for Japanese Mitsubishi vehicles."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature
from homeassistant.components.climate.const import HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

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
        self._optimistic_hvac_mode: HVACMode | None = None
        self._optimistic_cancel: Callable[[], None] | None = None

    @property
    def hvac_mode(self) -> HVACMode:
        if self._optimistic_hvac_mode is not None:
            return self._optimistic_hvac_mode
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
        self._set_optimistic_mode(HVACMode.HEAT_COOL)
        try:
            await self.coordinator.client.async_start_climate(self.vehicle)
        except MitsubishiJPCommandUnknown as err:
            await self.coordinator.async_request_refresh()
            raise HomeAssistantError(str(err)) from err
        except MitsubishiJPError as err:
            self._set_optimistic_mode(HVACMode.OFF, timeout=30)
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        previous_mode = self.hvac_mode
        self._set_optimistic_mode(HVACMode.OFF)
        try:
            await self.coordinator.client.async_stop_climate(self.vehicle)
        except MitsubishiJPCommandUnknown as err:
            await self.coordinator.async_request_refresh()
            raise HomeAssistantError(str(err)) from err
        except MitsubishiJPError as err:
            self._set_optimistic_mode(previous_mode, timeout=30)
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()

    def _set_optimistic_mode(
        self, hvac_mode: HVACMode, *, timeout: int = 120
    ) -> None:
        """Publish a requested mode while the cloud state catches up."""
        if self._optimistic_cancel is not None:
            self._optimistic_cancel()
        self._optimistic_hvac_mode = hvac_mode
        self._optimistic_cancel = async_call_later(
            self.hass, timeout, self._clear_optimistic_mode
        )
        self.async_write_ha_state()

    def _clear_optimistic_mode(self, _now: datetime) -> None:
        """Fall back to the most recent server state after a bounded delay."""
        self._optimistic_cancel = None
        self._optimistic_hvac_mode = None
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        """Clear the temporary mode as soon as the server confirms it."""
        state = self.vehicle_state
        expected_on = self._optimistic_hvac_mode == HVACMode.HEAT_COOL
        if (
            self._optimistic_hvac_mode is not None
            and state is not None
            and state.ac_on is expected_on
        ):
            if self._optimistic_cancel is not None:
                self._optimistic_cancel()
            self._optimistic_cancel = None
            self._optimistic_hvac_mode = None
        super()._handle_coordinator_update()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel a pending optimistic-state timer when the entity is removed."""
        if self._optimistic_cancel is not None:
            self._optimistic_cancel()
            self._optimistic_cancel = None
        await super().async_will_remove_from_hass()
