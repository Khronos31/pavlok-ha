"""Connection ownership and normal wake-alarm switches."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .entity import PavlokEntity
from .manager import PavlokConnection


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    manager: PavlokConnection = hass.data["pavlok"][entry.entry_id]
    async_add_entities(
        [
            PavlokConnectionSwitch(manager, entry),
            PavlokWakeSwitch(manager, entry),
            PavlokSleepAutoSwitch(manager, entry),
            PavlokSleepRecordingSwitch(manager, entry),
        ]
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


class PavlokSleepAutoSwitch(PavlokEntity, SwitchEntity):
    """The device's automatic sleep detection.

    The bedtime window shown in the official app never reaches the device, so this
    is the whole of what the hardware knows: detect sleep, or don't.
    """

    _attr_name = "Sleep detection"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, manager: PavlokConnection, entry: ConfigEntry) -> None:
        super().__init__(manager, entry, "sleep_auto")

    @property
    def is_on(self) -> bool:
        return bool(self.manager.data.get("sleep_auto", False))

    async def async_turn_on(self, **kwargs) -> None:
        await self.manager.async_set_sleep_auto(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.manager.async_set_sleep_auto(False)


class PavlokSleepRecordingSwitch(PavlokEntity, SwitchEntity):
    """Whether a sleep session is being recorded right now.

    Equivalent to the long press that starts tracking on the device, which is easy
    to lose track of because the wristband only answers with a chirp.
    """

    _attr_name = "Sleep recording"

    def __init__(self, manager: PavlokConnection, entry: ConfigEntry) -> None:
        super().__init__(manager, entry, "sleep_recording")

    @property
    def is_on(self) -> bool:
        return bool(self.manager.data.get("sleep_tracking", False))

    async def async_turn_on(self, **kwargs) -> None:
        await self.manager.async_set_sleep_recording(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.manager.async_set_sleep_recording(False)
