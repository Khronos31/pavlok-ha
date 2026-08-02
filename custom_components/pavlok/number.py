"""Restored HA-side stimulus settings; these values are never sent as settings."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity

from .const import STIM_BEEP, STIM_VIBE, STIM_ZAP
from .entity import PavlokEntity
from .manager import PavlokConnection


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    manager: PavlokConnection = hass.data["pavlok"][entry.entry_id]
    # Zap も常に登録し、既定で無効にする（button.py と同じ理由）
    async_add_entities(
        [
            PavlokStimulusNumber(manager, entry, kind, field)
            for kind in (STIM_VIBE, STIM_BEEP, STIM_ZAP)
            for field in ("intensity", "count")
        ]
    )


class PavlokStimulusNumber(PavlokEntity, NumberEntity, RestoreEntity):
    """Persist a control preference locally without touching read-only device settings."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(
        self, manager: PavlokConnection, entry: ConfigEntry, kind: str, field: str
    ) -> None:
        super().__init__(manager, entry, f"{kind}_{field}")
        self._kind, self._field = kind, field
        self._attr_name = f"{kind.capitalize()} {field}"
        self._attr_native_min_value = 1 if field == "count" else 0
        self._attr_native_max_value = 127 if field == "count" else 100
        self._attr_native_step = 1
        self._value = (
            1
            if field == "count"
            else (100 if kind == STIM_VIBE else 80 if kind == STIM_BEEP else 50)
        )
        manager.data[f"{kind}_{field}"] = self._value
        if kind == STIM_ZAP:
            # Zap の設定も既定では出さない（button.py と対）
            self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> float:
        return self._value

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if previous := await self.async_get_last_state():
            try:
                self._value = float(previous.state)
                self.manager.data[f"{self._kind}_{self._field}"] = self._value
            except ValueError:
                pass

    async def async_set_native_value(self, value: float) -> None:
        self._value = value
        self.manager.data[f"{self._kind}_{self._field}"] = value
        self.async_write_ha_state()
