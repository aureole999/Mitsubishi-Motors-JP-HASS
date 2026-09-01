"""Data coordinator and shared entities for Mitsubishi Motors Japan."""
from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import MitsubishiJPAuthError, MitsubishiJPClient, MitsubishiJPError
from .const import DOMAIN
from .models import Vehicle, VehicleState

_LOGGER = logging.getLogger(__name__)


class MitsubishiJPCoordinator(DataUpdateCoordinator[VehicleState]):
    """Poll cached state for one vehicle without waking it."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: MitsubishiJPClient,
        vehicle: Vehicle,
        update_interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN}_{vehicle.vin[-6:]}",
            update_interval=update_interval,
        )
        self.client = client
        self.vehicle = vehicle

    async def _async_update_data(self) -> VehicleState:
        try:
            return await self.client.async_get_vehicle_state(self.vehicle)
        except MitsubishiJPAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MitsubishiJPError as err:
            raise UpdateFailed(str(err)) from err


class MitsubishiJPEntity(CoordinatorEntity[MitsubishiJPCoordinator]):
    """Base entity for a Japanese Mitsubishi vehicle."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MitsubishiJPCoordinator) -> None:
        super().__init__(coordinator)
        vehicle = coordinator.vehicle
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, vehicle.vin)},
            manufacturer="Mitsubishi Motors",
            model=vehicle.model or "Vehicle",
            name=vehicle.name,
        )

    @property
    def vehicle(self) -> Vehicle:
        return self.coordinator.vehicle

    @property
    def vehicle_state(self) -> VehicleState | None:
        return self.coordinator.data
