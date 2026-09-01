"""Sensors for Mitsubishi Motors Japan."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import MitsubishiJPCoordinator, MitsubishiJPEntity
from .models import VehicleState


@dataclass(frozen=True, kw_only=True)
class MitsubishiJPSensorDescription(SensorEntityDescription):
    value_fn: Callable[[VehicleState], object | None]


SENSORS = (
    MitsubishiJPSensorDescription(
        key="battery_level",
        translation_key="battery_level",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.battery_level,
    ),
    MitsubishiJPSensorDescription(
        key="charge_remaining_time",
        translation_key="charge_remaining_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda state: state.minutes_to_full_charge,
    ),
    MitsubishiJPSensorDescription(
        key="climate_target_temperature",
        translation_key="climate_target_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.target_temperature,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        MitsubishiJPSensor(coordinator, description)
        for coordinator in data["coordinators"].values()
        for description in SENSORS
    )


class MitsubishiJPSensor(MitsubishiJPEntity, SensorEntity):
    entity_description: MitsubishiJPSensorDescription

    def __init__(
        self,
        coordinator: MitsubishiJPCoordinator,
        description: MitsubishiJPSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{self.vehicle.vin}_{description.key}"

    @property
    def native_value(self) -> object | None:
        state = self.vehicle_state
        return self.entity_description.value_fn(state) if state else None
