"""Shared entity base classes for the Pavlok integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import Entity

from .manager import PavlokConnection


class PavlokEntity(Entity):
    """An entity backed by the entry's one persistent BLE connection."""

    _attr_has_entity_name = True

    def __init__(self, manager: PavlokConnection, entry: ConfigEntry, key: str) -> None:
        self.manager = manager
        self._attr_unique_id = f"{entry.unique_id or manager.address}_{key}"

    @property
    def device_info(self):
        """Resolve metadata lazily so GATT device-information reads are reflected."""
        return self.manager.device_info

    @property
    def available(self) -> bool:
        """Physical controls are unavailable while the connection is released."""
        return self.manager.connect_enabled and bool(self.manager.data["connected"])

    async def async_added_to_hass(self) -> None:
        """Subscribe to the manager's push-cache updates."""
        self.async_on_remove(self.manager.async_add_listener(self.async_write_ha_state))
