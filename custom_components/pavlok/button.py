"""Explicit, user-triggered Pavlok stimulus buttons."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import STIM_BEEP, STIM_VIBE, STIM_ZAP
from .entity import PavlokEntity
from .manager import PavlokConnection


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    manager: PavlokConnection = hass.data["pavlok"][entry.entry_id]
    entities = [
        PavlokStimulusButton(manager, entry, STIM_VIBE),
        PavlokStimulusButton(manager, entry, STIM_BEEP),
    ]
    if manager.zap_enabled:
        entities.append(PavlokStimulusButton(manager, entry, STIM_ZAP))
    async_add_entities(entities)


class PavlokStimulusButton(PavlokEntity, ButtonEntity):
    """A button whose settings are HA-side numbers, never device settings."""

    def __init__(
        self, manager: PavlokConnection, entry: ConfigEntry, kind: str
    ) -> None:
        super().__init__(manager, entry, kind)
        self._attr_name = kind.capitalize()
        self._kind = kind

    async def async_press(self) -> None:
        await self.manager.async_stimulate(
            self._kind,
            self.manager.data.get(
                f"{self._kind}_intensity", 100 if self._kind == STIM_VIBE else 80
            ),
            self.manager.data.get(f"{self._kind}_count", 1),
        )
