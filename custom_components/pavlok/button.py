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
    # Zap のエンティティも**常に登録する**が、既定では無効にしておく
    # （HAの作法。デバイスページに「無効なエンティティ」として現れ、
    #  ユーザーが明示的に有効化できる。条件付きで生成しないと、設定を
    #  切り替えるたびにエンティティが消えて履歴や自動化の参照が壊れる）
    async_add_entities(
        [
            PavlokStimulusButton(manager, entry, STIM_VIBE),
            PavlokStimulusButton(manager, entry, STIM_BEEP),
            PavlokStimulusButton(manager, entry, STIM_ZAP),
        ]
    )


class PavlokStimulusButton(PavlokEntity, ButtonEntity):
    """A button whose settings are HA-side numbers, never device settings."""

    def __init__(
        self, manager: PavlokConnection, entry: ConfigEntry, kind: str
    ) -> None:
        super().__init__(manager, entry, kind)
        self._attr_name = kind.capitalize()
        self._kind = kind
        if kind == STIM_ZAP:
            # 体に電気刺激を送るので、利用者が明示的に有効化するまで出さない
            self._attr_entity_registry_enabled_default = False

    async def async_press(self) -> None:
        await self.manager.async_stimulate(
            self._kind,
            self.manager.data.get(
                f"{self._kind}_intensity", 100 if self._kind == STIM_VIBE else 80
            ),
            self.manager.data.get(f"{self._kind}_count", 1),
        )
