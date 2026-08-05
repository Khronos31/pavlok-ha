"""Time entity for the normal HA-managed wake alarm."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import PavlokEntity
from .manager import PavlokConnection


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    manager: PavlokConnection = hass.data["pavlok"][entry.entry_id]
    async_add_entities([PavlokWakeTime(manager, entry)])


class PavlokWakeTime(PavlokEntity, TimeEntity, RestoreEntity):
    _attr_name = "Alarm time"

    def __init__(self, manager: PavlokConnection, entry: ConfigEntry) -> None:
        super().__init__(manager, entry, "wake_time")
        manager.data.setdefault("wake_time", time(7, 0))

    @property
    def native_value(self) -> time:
        """The device's copy of the reserved alarm, once it has been read."""
        return self.manager.data.get("wake_time") or time(7, 0)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # 実機を読めていればそれが正。復元値で上書きしない。
        if self.manager.data.get("wake_synced"):
            return
        if previous := await self.async_get_last_state():
            try:
                self.manager.data["wake_time"] = time.fromisoformat(previous.state)
            except ValueError:
                pass

    async def async_set_value(self, value: time) -> None:
        await self.manager.async_set_wake(wake_time=value)
        self.async_write_ha_state()
