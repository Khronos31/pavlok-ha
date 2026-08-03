"""Optimistic physical-button assignment selects."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity

from .const import BTN_SLOT
from .entity import PavlokEntity
from .manager import PavlokConnection

# 状態値は小文字のまま保ち、表示名は translations 側で与える。
# こうすると見た目を変えても自動化が参照する状態文字列が動かない。
_OPTIONS = [
    "disabled",
    "vibe",
    "beep",
    "zap",
    "timer",
    "stopwatch",
    "sleep_tracking",
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    manager: PavlokConnection = hass.data["pavlok"][entry.entry_id]
    async_add_entities(
        [
            PavlokButtonAssignment(manager, entry, position, press, slot)
            for (position, press), slot in BTN_SLOT.items()
        ]
    )


class PavlokButtonAssignment(PavlokEntity, SelectEntity, RestoreEntity):
    """The firmware cannot read assignments, therefore restore/optimistic state is used."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = _OPTIONS
    _attr_translation_key = "button_assignment"

    def __init__(
        self,
        manager: PavlokConnection,
        entry: ConfigEntry,
        position: str,
        press: str,
        slot: int,
    ) -> None:
        super().__init__(manager, entry, f"button_{position}_{press}")
        self._attr_name = f"Button {position} {press}"
        self._slot = slot
        self._current = "disabled"

    @property
    def current_option(self) -> str:
        return self._current

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (
            previous := await self.async_get_last_state()
        ) and previous.state in _OPTIONS:
            self._current = previous.state

    async def async_select_option(self, option: str) -> None:
        await self.manager.async_assign_button(self._slot, option)
        self._current = option
        self.async_write_ha_state()
