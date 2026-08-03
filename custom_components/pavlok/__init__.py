"""Home Assistant integration for Pavlok 3 BLE wearables."""

from __future__ import annotations

import logging
from typing import Final

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, STIM_BEEP, STIM_VIBE, STIM_ZAP
from .manager import PavlokConnection

_LOGGER = logging.getLogger(__name__)

PLATFORMS: Final = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.EVENT,
    Platform.TIME,
]

SERVICE_STIMULATE: Final = "stimulate"
SERVICE_SET_ALARM: Final = "set_alarm"
SERVICE_DELETE_ALARM: Final = "delete_alarm"
SERVICE_TIMER: Final = "timer"
SERVICE_SYNC_HISTORY: Final = "sync_history"


def _manager_from_call(hass: HomeAssistant, call: ServiceCall) -> PavlokConnection:
    """Select a configured device, or the service's explicitly selected entry."""
    entry_id = call.data.get("entry_id")
    managers: dict[str, PavlokConnection] = hass.data.get(DOMAIN, {})
    if entry_id:
        try:
            return managers[entry_id]
        except KeyError as err:
            raise HomeAssistantError("Unknown Pavlok config entry") from err
    if len(managers) == 1:
        return next(iter(managers.values()))
    raise HomeAssistantError("Specify entry_id when more than one Pavlok is configured")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Pavlok config entry and its persistent BLE manager."""
    hass.data.setdefault(DOMAIN, {})
    manager = PavlokConnection(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = manager
    await manager.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.services.has_service(DOMAIN, SERVICE_STIMULATE):
        _register_services(hass)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entities when options change, notably the zap opt-in."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload platforms and stop the BLE reconnection task."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        manager: PavlokConnection = hass.data[DOMAIN].pop(entry.entry_id)
        await manager.async_stop()
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
    return unloaded


def _register_services(hass: HomeAssistant) -> None:
    """Register domain services once; all operations stay local to BLE."""

    async def stimulate(call: ServiceCall) -> None:
        manager = _manager_from_call(hass, call)
        kind = call.data["type"]
        await manager.async_stimulate(kind, call.data["intensity"], call.data["count"])

    async def set_alarm(call: ServiceCall) -> None:
        await _manager_from_call(hass, call).async_set_alarm(dict(call.data))

    async def delete_alarm(call: ServiceCall) -> None:
        await _manager_from_call(hass, call).async_delete_alarm(call.data["id"])

    async def timer(call: ServiceCall) -> None:
        await _manager_from_call(hass, call).async_timer(
            call.data["action"],
            call.data["type"],
            seconds=call.data.get("seconds"),
            stimulus=call.data["stimulus"],
            interval=call.data["interval"],
            repeat=call.data["interval_mode"] == "repeat",
        )

    async def sync_history(call: ServiceCall) -> None:
        await _manager_from_call(hass, call).async_sync_history()

    common = {vol.Optional("entry_id"): cv.string}
    hass.services.async_register(
        DOMAIN,
        SERVICE_STIMULATE,
        stimulate,
        schema=vol.Schema(
            common
            | {
                vol.Required("type"): vol.In([STIM_VIBE, STIM_BEEP, STIM_ZAP]),
                vol.Required("intensity"): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=100)
                ),
                vol.Required("count"): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=127)
                ),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_ALARM,
        set_alarm,
        schema=vol.Schema(
            common
            | {
                vol.Required("time"): cv.time,
                vol.Optional("days", default=[]): [cv.string],
                vol.Optional("name", default="Alarm"): cv.string,
                vol.Optional("enabled", default=True): cv.boolean,
                vol.Optional("vibe", default=False): cv.boolean,
                vol.Optional("vibe_intensity", default=100): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=100)
                ),
                vol.Optional("vibe_count", default=1): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=127)
                ),
                vol.Optional("beep", default=False): cv.boolean,
                vol.Optional("beep_intensity", default=80): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=100)
                ),
                vol.Optional("beep_count", default=1): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=127)
                ),
                vol.Optional("zap", default=False): cv.boolean,
                vol.Optional("zap_intensity", default=50): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=100)
                ),
                vol.Optional("zap_count", default=1): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=127)
                ),
                vol.Optional("unlock", default="none"): vol.In(
                    ["none", "jacks", "qr", "puzzle"]
                ),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_ALARM,
        delete_alarm,
        schema=vol.Schema(common | {vol.Required("id"): cv.string}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_TIMER,
        timer,
        schema=vol.Schema(
            common
            | {
                vol.Required("action"): vol.In(["start", "pause", "resume", "reset"]),
                vol.Optional("type", default="timer"): vol.In(["timer", "stopwatch"]),
                # start のときだけ必要。他の操作はオペコードのみで完結する。
                vol.Optional("seconds"): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=86399)
                ),
                vol.Optional("stimulus", default=STIM_VIBE): vol.In(
                    [STIM_VIBE, STIM_BEEP, STIM_ZAP]
                ),
                vol.Optional("interval", default=0): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=255)
                ),
                # repeat は間隔ごとに刺激を繰り返し、リセットするまで止まらない。
                vol.Optional("interval_mode", default="once"): vol.In(
                    ["once", "repeat"]
                ),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SYNC_HISTORY, sync_history, schema=vol.Schema(common)
    )
