"""Binary sensors for Mitsubishi Motors Japan."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import MitsubishiJPCoordinator, MitsubishiJPEntity
from .models import VehicleState


@dataclass(frozen=True, kw_only=True)
class MitsubishiJPBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[VehicleState], bool | None]


BINARY_SENSORS = (
    MitsubishiJPBinarySensorDescription(
        key="is_charging",
        translation_key="is_charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda state: state.is_charging,
    ),
    MitsubishiJPBinarySensorDescription(
        key="is_plugged_in",
        translation_key="is_plugged_in",
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=lambda state: state.is_plugged_in,
    ),
    MitsubishiJPBinarySensorDescription(
        key="climate_active",
        translation_key="climate_active",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda state: state.ac_on,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        MitsubishiJPBinarySensor(coordinator, description)
        for coordinator in data["coordinators"].values()
        for description in BINARY_SENSORS
    )


class MitsubishiJPBinarySensor(MitsubishiJPEntity, BinarySensorEntity):
    entity_description: MitsubishiJPBinarySensorDescription

    def __init__(
        self,
        coordinator: MitsubishiJPCoordinator,
        description: MitsubishiJPBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{self.vehicle.vin}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        state = self.vehicle_state
        return self.entity_description.value_fn(state) if state else None

