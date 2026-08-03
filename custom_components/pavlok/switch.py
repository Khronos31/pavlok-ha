"""Connection ownership and normal wake-alarm switches."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .entity import PavlokEntity
from .manager import PavlokConnection


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    manager: PavlokConnection = hass.data["pavlok"][entry.entry_id]
    async_add_entities(
        [PavlokConnectionSwitch(manager, entry), PavlokWakeSwitch(manager, entry)]
    )


class PavlokConnectionSwitch(PavlokEntity, SwitchEntity):
    """The device's main entity: everything else depends on this connection.

    ``_attr_name = None`` makes the displayed name the device name itself, which is
    how Home Assistant identifies a device's primary entity and lists it above the
    other controls.
    """

    _attr_name = None

    def __init__(self, manager: PavlokConnection, entry: ConfigEntry) -> None:
        super().__init__(manager, entry, "connection")

    @property
    def is_on(self) -> bool:
        return self.manager.connect_enabled

    @property
    def available(self) -> bool:
        return True

    async def async_turn_on(self, **kwargs) -> None:
        await self.manager.async_set_connect_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.manager.async_set_connect_enabled(False)


class PavlokWakeSwitch(PavlokEntity, SwitchEntity):
    """Enable/disable the single alarm this integration reserves for itself."""

    _attr_name = "Alarm"

    def __init__(self, manager: PavlokConnection, entry: ConfigEntry) -> None:
        super().__init__(manager, entry, "wake")

    @property
    def is_on(self) -> bool:
        return bool(self.manager.data.get("wake_enabled", False))

    async def async_turn_on(self, **kwargs) -> None:
        await self.manager.async_set_wake(enabled=True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.manager.async_set_wake(enabled=False)
