"""Sensors published by Pavlok notify packets and safe reads."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant

from .entity import PavlokEntity
from .manager import PavlokConnection


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Create all push-cache Pavlok sensor entities."""
    manager: PavlokConnection = hass.data["pavlok"][entry.entry_id]
    async_add_entities(
        [
            PavlokValueSensor(
                manager,
                entry,
                "activity",
                "Activity",
                "activity",
                state_class=SensorStateClass.MEASUREMENT,
                unit="increments/s",
            ),
            PavlokValueSensor(
                manager,
                entry,
                "activity_total",
                "Activity total",
                "activity_total",
                state_class=SensorStateClass.TOTAL_INCREASING,
                category=EntityCategory.DIAGNOSTIC,
            ),
            PavlokValueSensor(
                manager,
                entry,
                "steps",
                "Steps",
                "steps",
                state_class=SensorStateClass.TOTAL_INCREASING,
                unit="steps",
            ),
            PavlokValueSensor(
                manager,
                entry,
                "battery",
                "Battery",
                "battery",
                device_class=SensorDeviceClass.BATTERY,
                state_class=SensorStateClass.MEASUREMENT,
                unit=PERCENTAGE,
            ),
            PavlokRssiSensor(manager, entry),
            PavlokLastSleepSensor(manager, entry),
            PavlokValueSensor(
                manager,
                entry,
                "next_alarm",
                "Next alarm",
                "next_alarm",
                device_class=SensorDeviceClass.TIMESTAMP,
            ),
            PavlokTimerSensor(manager, entry),
            PavlokAlarmsSensor(manager, entry),
        ]
    )


class PavlokValueSensor(PavlokEntity, SensorEntity):
    """A direct scalar from the manager data cache."""

    def __init__(
        self,
        manager: PavlokConnection,
        entry: ConfigEntry,
        key: str,
        name: str,
        data_key: str,
        *,
        device_class=None,
        state_class=None,
        category=None,
        unit=None,
    ) -> None:
        super().__init__(manager, entry, key)
        self._attr_name = name
        self._data_key = data_key
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_entity_category = category
        self._attr_native_unit_of_measurement = unit

    @property
    def native_value(self) -> Any:
        return self.manager.data[self._data_key]


class PavlokRssiSensor(PavlokEntity, SensorEntity):
    """Signal strength as seen by the best Bluetooth proxy, naming that proxy."""

    _attr_name = "RSSI"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "dBm"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, manager: PavlokConnection, entry: ConfigEntry) -> None:
        super().__init__(manager, entry, "rssi")

    @property
    def native_value(self) -> Any:
        return self.manager.data["rssi"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose every proxy's view; with one device this is the whole picture."""
        return {
            "scanner": self.manager.data.get("rssi_scanner"),
            "by_scanner": self.manager.data.get("rssi_by_scanner", {}),
        }


class PavlokLastSleepSensor(PavlokEntity, SensorEntity):
    """Latest parsed sleep interval; past intervals live in recorder statistics."""

    _attr_name = "Last sleep"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, manager: PavlokConnection, entry: ConfigEntry) -> None:
        super().__init__(manager, entry, "last_sleep")

    @property
    def native_value(self) -> datetime | None:
        record = self.manager.data["last_sleep"]
        return record["start"] if record else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        record = self.manager.data["last_sleep"]
        if not record:
            return None
        return {"wake_time": record["end"], "duration_minutes": record["duration"]}


class PavlokTimerSensor(PavlokEntity, SensorEntity):
    """Current elapsed time of the firmware timer or stopwatch."""

    _attr_name = "Timer"
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, manager: PavlokConnection, entry: ConfigEntry) -> None:
        super().__init__(manager, entry, "timer")

    @property
    def native_value(self) -> int:
        return self.manager.data["timer"]

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {"type": self.manager.data["timer_type"]}


class PavlokAlarmsSensor(PavlokEntity, SensorEntity):
    """Small overview of alarms; detailed raw protocol data is intentionally hidden."""

    _attr_name = "Alarms"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager: PavlokConnection, entry: ConfigEntry) -> None:
        super().__init__(manager, entry, "alarms")

    @property
    def native_value(self) -> int:
        return len(self.manager.data["alarms"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"alarms": self.manager.data["alarms"]}
