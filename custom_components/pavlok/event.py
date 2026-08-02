"""Physical Pavlok button press events."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .entity import PavlokEntity
from .manager import PavlokConnection

_EVENT_TYPES = [
    "top_short",
    "top_long",
    "mid_short",
    "mid_long",
    "bottom_short",
    "bottom_long",
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    manager: PavlokConnection = hass.data["pavlok"][entry.entry_id]
    async_add_entities([PavlokButtonEvent(manager, entry)])


class PavlokButtonEvent(PavlokEntity, EventEntity):
    _attr_name = "Button"
    _attr_event_types = _EVENT_TYPES

    def __init__(self, manager: PavlokConnection, entry: ConfigEntry) -> None:
        super().__init__(manager, entry, "button")
        self._seen: str | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.manager.async_add_listener(self._handle_update))

    def _handle_update(self) -> None:
        event = self.manager.data.get("button_event")
        if event and event != self._seen:
            self._seen = event
            self._trigger_event(event)
            self.async_write_ha_state()
