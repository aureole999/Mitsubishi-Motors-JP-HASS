"""Data models for Mitsubishi Motors Japan."""
from __future__ import annotations

from dataclasses import dataclass
@dataclass(frozen=True, slots=True)
class Vehicle:
    """A vehicle returned by the Japanese Mitsubishi service."""

    vin: str
    internal_vin: str
    model: str | None = None
    model_year: str | None = None
    vehicle_type: str = "phev"
    color: str | None = None
    is_primary: bool = False

    @property
    def name(self) -> str:
        """Return a privacy-conscious device name."""
        base = self.model or "Mitsubishi"
        return f"{base} {self.vin[-6:]}"


@dataclass(frozen=True, slots=True)
class VehicleState:
    """Read-only cached vehicle state."""

    battery_level: float | None = None
    is_charging: bool | None = None
    is_plugged_in: bool | None = None
    charging_ready: bool | None = None
    charge_disabled: bool | None = None
    minutes_to_full_charge: int | None = None
    ac_on: bool | None = None
    target_temperature: float | None = None
    temperature_unit: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class CommandResult:
    """A confirmed remote command result."""

    request_id: str
    polls: int
