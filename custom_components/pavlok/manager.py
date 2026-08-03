"""Persistent BLE connection and protocol operations for a Pavlok entry.

This module deliberately owns all GATT I/O.  Entities only observe its state,
which keeps a disconnect/reconnect from creating competing Bleak clients.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
from time import monotonic
from typing import Any, Final

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak_retry_connector import (
    BLEAK_RETRY_EXCEPTIONS,
    BleakClientWithServiceCache,
    establish_connection,
)
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothScanningMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .const import (
    ACTRL_COMMIT,
    ACTRL_READ,
    ACTRL_WRITE_BEGIN,
    ALARM_ENABLED_BIT,
    ALARM_PROFILE,
    AO_UNLOCK_JACKS,
    AO_UNLOCK_NONE,
    AO_UNLOCK_PUZZLE,
    AO_UNLOCK_QR,
    BATTERY_CHR,
    BTN_READ,
    BTN_READ_REPLY,
    TIMER_CONFIG_REPLY,
    TIMER_READ,
    CHR_ACTRL,
    CHR_ALARM_TIME,
    CHR_AWRITE,
    CHR_BEEP,
    CHR_CMD7,
    CHR_DAQ,
    CHR_EVENTS,
    CHR_FILES,
    CHR_SLEEP_AUTO,
    CHR_STATUS,
    CHR_TIME,
    CHR_VIBE,
    CHR_ZAP,
    DAQ_START,
    DAQ_STOP,
    DEVICE_INFO_SVC,
    DOMAIN,
    FILES_TYPE_COARSE,
    SLEEP_AUTO_OFF,
    SLEEP_AUTO_ON,
    STIM_BEEP,
    STIM_CODE,
    STIM_VIBE,
    STIM_ZAP,
    TIMER_KIND_STOPWATCH,
    TIMER_KIND_TIMER,
    TIMER_ONCE,
    TIMER_OP_FRAME,
    TIMER_OP_KIND,
    TIMER_OP_PAUSE,
    TIMER_OP_RESET,
    TIMER_OP_RESUME,
    TIMER_OP_START,
    TIMER_REPEAT,
    btn_action_bytes,
    files_command,
    find_sleep_records,
    iter_blocks,
    parse_activity,
    parse_btn_action,
    parse_button,
    parse_timer_config,
    parse_file_header,
    parse_sleep_state,
    parse_steps,
    parse_timer,
    truncate_block,
    seal_alarm_message,
    stimulus_bytes,
    time_bytes,
    tlv,
    varlen_encode,
)

_LOGGER = logging.getLogger(__name__)


def _actrl(mode: int) -> bytes:
    """A-Ctrl フレーム: <モード> 00 <プロファイル名>。

    モードとプロファイル名の間の 0x00 は必須。これを落とすとデバイスは要求を
    黙って無視し、読み出しの応答が永久に返らない（2026-08-03 実測）。
    """
    return bytes([mode, 0x00]) + ALARM_PROFILE


_RECONNECT_MAX_SECONDS: Final = 60
_FILE_HEADER_BYTES: Final = 14
# 履歴は10万バイト超あり、BLE越しでは数分かかる。宣言サイズを待つのが本筋で、
# この値は「応答が完全に止まった」ときの打ち切りにすぎない。
_HISTORY_IDLE_SECONDS: Final = 10
_BLOCK_TIMEOUT: Final = 60
_BLOCK_ATTEMPTS: Final = 3
# 接続は電波が弱いと張り直しを繰り返す。取得は1分ほどBLEを占有するので、
# 接続を機に走らせる分には下限を設ける（記録終了を機にする分は制限しない）。
_HISTORY_MIN_INTERVAL: Final = 1800
_STANDARD_DEVICE_INFO: Final = {
    "00002a29-0000-1000-8000-00805f9b34fb": "manufacturer",
    "00002a24-0000-1000-8000-00805f9b34fb": "model",
    "00002a26-0000-1000-8000-00805f9b34fb": "sw_version",
    "00002a27-0000-1000-8000-00805f9b34fb": "hw_version",
    "00002a25-0000-1000-8000-00805f9b34fb": "serial_number",
}


class PavlokConnection:
    """Own a single Pavlok BLE link and publish a small in-memory state cache."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.address: str = entry.data["address"]
        self.name: str = entry.data.get("name", "Pavlok")
        self.data: dict[str, Any] = {
            "connected": False,
            "activity": None,
            "activity_total": None,
            "steps": None,
            "battery": None,
            "rssi": None,
            "rssi_scanner": None,
            "rssi_by_scanner": {},
            "timer": 0,
            "timer_type": "idle",
            "timer_running": False,
            "timer_finishes_at": None,
            "timer_config": {},
            "stopwatch_config": {},
            "sleep_tracking": False,
            "sleep_auto": False,
            "last_sleep": None,
            "next_alarm": None,
            "alarms": [],
            "buttons": {},
            "device_info": {},
        }
        self._client: BleakClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._listeners: set[Callable[[], None]] = set()
        self._unsub_bluetooth: CALLBACK_TYPE | None = None
        self._connect_enabled = bool(entry.options.get("keep_connected", True))
        self._last_activity: tuple[int, float] | None = None
        self._history_data = bytearray()
        self._history_done: asyncio.Event | None = None
        self._history_expect = 0
        self._history_idle: asyncio.TimerHandle | None = None
        self._alarm_data = bytearray()
        self._alarm_done: asyncio.Event | None = None
        self._alarm_setter: asyncio.TimerHandle | None = None
        self._known_alarm_records: dict[str, bytes] = {}
        self._last_history_sync: float | None = None

    @property
    def connect_enabled(self) -> bool:
        return self._connect_enabled

    @property
    def device_info(self) -> dr.DeviceInfo:
        """Return the HA device identity; values appear after the first connection."""
        info = self.data["device_info"]
        return dr.DeviceInfo(
            identifiers={(DOMAIN, self.address)},
            name=self.name,
            manufacturer=info.get("manufacturer", "Pavlok"),
            model=info.get("model", "Pavlok 3 / Shock Clock 3"),
            sw_version=info.get("sw_version"),
            hw_version=info.get("hw_version"),
            serial_number=info.get("serial_number"),
            connections={(dr.CONNECTION_BLUETOOTH, self.address)},
        )

    async def async_start(self) -> None:
        """Begin watching advertisements and, when enabled, connecting."""
        self._unsub_bluetooth = bluetooth.async_register_callback(
            self.hass,
            self._async_bluetooth_update,
            BluetoothCallbackMatcher(address=self.address),
            BluetoothScanningMode.PASSIVE,
        )
        self._async_bluetooth_update()
        if self._connect_enabled:
            self._task = self._async_start_connection_loop()

    async def async_stop(self) -> None:
        """Cancel reconnecting, unsubscribe scanner updates, and release the radio."""
        if self._unsub_bluetooth:
            self._unsub_bluetooth()
            self._unsub_bluetooth = None
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        await self._async_disconnect()

    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Register an entity state listener."""
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def _publish(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    def _async_bluetooth_update(self, *_: Any) -> None:
        """Refresh diagnostic RSSI from the proxy that currently sees the device best.

        ``async_last_service_info`` returns whichever proxy advertised most recently,
        so with several proxies the value jumps between them and says nothing about
        the link in use.  Home Assistant picks the strongest proxy when connecting,
        so the best RSSI is the meaningful one to publish.
        """
        devices = bluetooth.async_scanner_devices_by_address(
            self.hass, self.address, True
        )
        if devices:
            by_scanner = {
                device.scanner.name: device.advertisement.rssi for device in devices
            }
            best = max(devices, key=lambda device: device.advertisement.rssi)
            self.data["rssi"] = best.advertisement.rssi
            self.data["rssi_scanner"] = best.scanner.name
            self.data["rssi_by_scanner"] = by_scanner
            self._publish()
            return
        info = bluetooth.async_last_service_info(self.hass, self.address, True)
        if info:
            self.data["rssi"] = info.rssi
            self._publish()

    async def async_set_connect_enabled(self, enabled: bool) -> None:
        """Toggle ownership of the BLE connection without modifying entry options."""
        if self._connect_enabled == enabled:
            return
        self._connect_enabled = enabled
        if not enabled:
            if self._task:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
                self._task = None
            await self._async_disconnect()
        elif not self._task:
            self._task = self._async_start_connection_loop()
        self._publish()

    def _async_start_connection_loop(self) -> asyncio.Task[None]:
        """Run the reconnect loop as a background task.

        A plain ``async_create_task`` is awaited during startup, so an unreachable
        device would hold Home Assistant's bootstrap open for as long as it keeps
        retrying.  Background tasks do not block startup and are cancelled with the
        config entry.
        """
        return self.entry.async_create_background_task(
            self.hass,
            self._connection_loop(),
            name=f"pavlok {self.address} connection",
        )

    async def _connection_loop(self) -> None:
        """Maintain a link while requested, with a bounded exponential backoff."""
        delay = 1
        while self._connect_enabled:
            try:
                await self._async_connect()
                delay = 1
                while (
                    self._connect_enabled and self._client and self._client.is_connected
                ):
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except BLEAK_RETRY_EXCEPTIONS as err:
                _LOGGER.debug("Pavlok %s connection failed: %s", self.address, err)
            except Exception:  # a backend can raise errors not in connector's tuple
                _LOGGER.debug("Unexpected Pavlok connection failure", exc_info=True)
            finally:
                await self._async_disconnect()
            if self._connect_enabled:
                await asyncio.sleep(delay)
                delay = min(delay * 2, _RECONNECT_MAX_SECONDS)

    async def _async_connect(self) -> None:
        """Resolve HA's current proxy device and subscribe to local-push values."""
        device: BLEDevice | None = bluetooth.async_ble_device_from_address(
            self.hass, self.address, True
        )
        if device is None:
            raise HomeAssistantError("Pavlok is not currently visible to Bluetooth")
        self._client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            self.name,
            disconnected_callback=self._disconnected,
            max_attempts=1,
            use_services_cache=True,
        )
        for characteristic, callback in (
            (CHR_EVENTS, self._event_notify),
            (CHR_STATUS, self._status_notify),
            (BATTERY_CHR, self._battery_notify),
            # 読み出し要求(0x06)の応答は A-Ctrl 自身の通知として20バイトずつ返る
            # (btsnoopで確認、2026-08-02)。A-Write/A-Notify ではない——そちらを購読して
            # いた間、応答は永久に届かず一覧が常に空だった。
            (CHR_ACTRL, self._alarm_notify),
            # 書き込み手順は A-Write のCCCD有効化を含む。応答の収集先ではないので
            # 読み出しバッファを汚さないよう別コールバックにする。
            (CHR_AWRITE, self._alarm_write_ack),
            (CHR_FILES, self._history_notify),
            (CHR_CMD7, self._command_notify),
            (CHR_ALARM_TIME, self._alarm_time_notify),
        ):
            try:
                await self._client.start_notify(characteristic, callback)
            except Exception:  # noqa: BLE001 - backend exception types vary by proxy
                _LOGGER.debug(
                    "Pavlok characteristic %s has no notify support", characteristic
                )
        await self._async_read_initial_values()
        # This is a benign clock write, never a stimulus, alarm or button-setting write.
        try:
            await self.async_sync_time()
        except Exception:
            _LOGGER.debug("Pavlok clock sync unavailable", exc_info=True)
        self.data["connected"] = True
        self._publish()
        try:
            await self.async_refresh_alarms()
        except HomeAssistantError:
            _LOGGER.debug("Pavlok alarm list unavailable after connection")
        try:
            await self.async_refresh_buttons()
            await self.async_refresh_timer_config()
        except Exception:  # noqa: BLE001 - never let a read stop the connection
            _LOGGER.debug("Pavlok stored settings unavailable", exc_info=True)
        self._async_sync_history_soon(force=False)

    def _disconnected(self, _: BleakClient) -> None:
        self.hass.loop.call_soon_threadsafe(self._mark_disconnected)

    def _mark_disconnected(self) -> None:
        if self.data["connected"]:
            self.data["connected"] = False
            self._publish()

    async def _async_disconnect(self) -> None:
        client, self._client = self._client, None
        self._mark_disconnected()
        if client and client.is_connected:
            try:
                await client.disconnect()
            except Exception:
                _LOGGER.debug("Pavlok disconnect failed", exc_info=True)

    async def _async_read_initial_values(self) -> None:
        """Read safe status/device metadata; failures do not tear down a good link."""
        for characteristic, handler in (
            (CHR_STATUS, self._status_notify),
            (BATTERY_CHR, self._battery_notify),
            (CHR_ALARM_TIME, self._alarm_time_notify),
        ):
            try:
                handler(
                    characteristic,
                    await self._require_client().read_gatt_char(characteristic),
                )
            except Exception:  # noqa: BLE001 - each characteristic is optional
                _LOGGER.debug("Initial Pavlok read unavailable for %s", characteristic)
        try:
            service = self._require_client().services.get_service(DEVICE_INFO_SVC)
            if service:
                for characteristic in service.characteristics:
                    key = _STANDARD_DEVICE_INFO.get(str(characteristic.uuid).lower())
                    if key:
                        self.data["device_info"][key] = (
                            bytes(
                                await self._require_client().read_gatt_char(
                                    characteristic.uuid
                                )
                            )
                            .decode(errors="replace")
                            .strip("\x00")
                        )
        except Exception:
            _LOGGER.debug(
                "Pavlok device information service unavailable", exc_info=True
            )

    def _require_client(self) -> BleakClient:
        if not self._client or not self._client.is_connected:
            raise HomeAssistantError("Pavlok is disconnected")
        return self._client

    async def _write(self, characteristic: str, payload: bytes) -> None:
        """Perform a GATT write; ACK means transport only, never semantic acceptance."""
        await self._require_client().write_gatt_char(
            characteristic, payload, response=True
        )

    async def async_sync_time(self) -> None:
        """Synchronise local clock after each connection (without triggering an alarm)."""
        local = dt_util.now()
        offset = local.utcoffset()
        minutes = int(offset.total_seconds() // 60) if offset else 0
        await self._write(CHR_TIME, time_bytes(local.replace(tzinfo=None), minutes))

    async def async_stimulate(self, kind: str, intensity: int, count: int) -> None:
        """Fire a requested stimulus. This is only called by an explicit HA action."""
        characteristic = {STIM_VIBE: CHR_VIBE, STIM_BEEP: CHR_BEEP, STIM_ZAP: CHR_ZAP}[
            kind
        ]
        await self._write(characteristic, stimulus_bytes(kind, intensity, count))

    async def async_assign_button(self, slot: int, action: str) -> None:
        """Write an optimistic physical-button assignment."""
        action_bytes = btn_action_bytes(action)
        await self._write(CHR_CMD7, bytes([0x02, slot]) + action_bytes)

    async def async_set_sleep_auto(self, enabled: bool) -> None:
        """Turn the device's automatic sleep detection on or off.

        No schedule is involved: three captures of the app saving a bedtime window
        showed only this two byte write, so the times stay in the app.
        """
        await self._write(CHR_SLEEP_AUTO, SLEEP_AUTO_ON if enabled else SLEEP_AUTO_OFF)

    async def async_set_sleep_recording(self, enabled: bool) -> None:
        """Start or stop a sleep recording, the same as a long press on the device."""
        await self._write(CHR_DAQ, DAQ_START if enabled else DAQ_STOP)

    async def async_timer(
        self,
        action: str,
        kind: str = "timer",
        *,
        seconds: int | None = None,
        stimulus: str = STIM_VIBE,
        interval: int = 0,
        repeat: bool = False,
    ) -> None:
        """Drive the device-side timer or stopwatch.

        Saving and starting are separate commands on the device: the app writes the
        setup command alone when the user only saves, and follows it with the start
        opcode about 100 ms later when the user starts (btsnoop, 2026-08-03).
        ``start`` therefore saves first only when given a duration, so it can also
        start whatever is already stored.
        """
        if action in ("save", "start") and seconds is not None:
            await self._async_write_timer_setup(
                kind, seconds, stimulus, interval, repeat
            )
            # 残り時間の算出に使うので、書いた直後に読み直して実機と合わせる。
            await self.async_refresh_timer_config()
        elif action == "save":
            raise HomeAssistantError("seconds is required to save a timer")
        if action == "save":
            return
        operation = {
            "start": TIMER_OP_START,
            "pause": TIMER_OP_PAUSE,
            "resume": TIMER_OP_RESUME,
            "reset": TIMER_OP_RESET,
        }[action]
        await self._write(
            CHR_CMD7,
    CHR_DAQ, TIMER_OP_FRAME + bytes([operation, TIMER_OP_KIND[kind]])
        )

    async def _async_write_timer_setup(
        self, kind: str, seconds: int, stimulus: str, interval: int, repeat: bool
    ) -> None:
        """Configure the device-side timer.

        Verified byte-for-byte against three captured app writes::

            22 0a00 12 f5 02 0100   f004 03 2a 05 80   ストップウォッチ・ザップ・5秒
            22 0a00 13 f5 02 013c   f004 01 2a 0a 80   1分タイマー・振動・10秒
            22 0b00 13 f5 03 0181d8 f004 01 2a 0a 80   10分タイマー・振動・10秒

        Two details are easy to get wrong: the duration is ``01`` followed by the
        variable-length integer and the length byte counts that ``01``, and the
        trailing flag sits outside the u16 body length.
        """
        duration = b"\x01" + varlen_encode(seconds)
        kind_code = TIMER_KIND_STOPWATCH if kind == "stopwatch" else TIMER_KIND_TIMER
        body = (
            bytes([kind_code, 0xF5, len(duration)])
            + duration
            + bytes([0xF0, 0x04, STIM_CODE[stimulus], 0x2A, interval])
        )
        await self._write(
            CHR_CMD7,
    CHR_DAQ,
            bytes([0x22])
            + struct.pack("<H", len(body))
            + body
            + bytes([TIMER_REPEAT if repeat else TIMER_ONCE]),
        )

    def _event_notify(self, _: Any, payload: bytearray) -> None:
        self.hass.loop.call_soon_threadsafe(self._process_event, bytes(payload))

    def _process_event(self, payload: bytes) -> None:
        activity = parse_activity(payload)
        if activity is not None:
            now = monotonic()
            if self._last_activity:
                previous, stamp = self._last_activity
                elapsed = now - stamp
                self.data["activity"] = (
                    max(0, activity - previous) / elapsed if elapsed else 0
                )
            self._last_activity = (activity, now)
            self.data["activity_total"] = activity
        steps = parse_steps(payload)
        if steps is not None:
            self.data["steps"] = steps
        button = parse_button(payload)
        if button:
            masks = {0x10: "top", 0x20: "mid", 0x40: "bottom"}
            presses = {1: "short", 3: "long"}
            if button[0] in masks and button[1] in presses:
                self.data["button_event"] = f"{masks[button[0]]}_{presses[button[1]]}"
        timer = parse_timer(payload)
        if timer:
            kind, elapsed, running = timer
            self.data["timer_type"] = kind
            self.data["timer"] = elapsed
            self.data["timer_running"] = running
            self.data["timer_finishes_at"] = self._timer_finish(kind, elapsed, running)
        sleep = parse_sleep_state(payload)
        if sleep is not None:
            was_recording = self.data["sleep_tracking"]
            self.data["sleep_auto"], self.data["sleep_tracking"] = sleep
            if was_recording and not self.data["sleep_tracking"]:
                # 記録が終わった直後がレコードの書かれた直後。ここで引くのが最短。
                self._async_sync_history_soon(force=True)
        self._publish()

    def _timer_finish(self, kind: str, elapsed: int, running: bool) -> datetime | None:
        """When a running countdown will reach zero, using the stored duration.

        A stopwatch counts up with no end, and the events only arrive every few
        seconds, so publishing an end time lets the frontend show a live countdown
        without polling the device.
        """
        seconds = self.data["timer_config"].get("seconds")
        if kind != "timer" or not running or not seconds or elapsed > seconds:
            return None
        return dt_util.utcnow() + timedelta(seconds=seconds - elapsed)

    def _status_notify(self, _: Any, payload: bytearray) -> None:
        if len(payload) >= 3:
            self.hass.loop.call_soon_threadsafe(self._set_battery, int(payload[2]))

    def _battery_notify(self, _: Any, payload: bytearray) -> None:
        if payload:
            self.hass.loop.call_soon_threadsafe(self._set_battery, int(payload[0]))

    def _set_battery(self, battery: int) -> None:
        self.data["battery"] = max(0, min(100, battery))
        self._publish()

    def _alarm_time_notify(self, _: Any, payload: bytearray) -> None:
        """Decode the firmware's next-alarm clock.

        The payload carries a time of day only, not a date::

            00 28 19 00 01 02 00
            秒 分 時          ID      (BCD, same encoding as the 0x1005 clock)

        An all-zero payload means no alarm is pending.  The date has to be derived
        from the alarm's own weekday mask, which is why the trailing alarm id
        matters: it selects the record to consult.
        """
        raw = bytes(payload)
        if len(raw) < 3 or not any(raw):
            self.hass.loop.call_soon_threadsafe(self._set_value, "next_alarm", None)
            return
        try:
            second, minute, hour = ((b >> 4) * 10 + (b & 0x0F) for b in raw[:3])
            alarm_time = dt_time(hour, minute, second)
        except ValueError:
            _LOGGER.debug("Unknown Pavlok next-alarm payload: %s", raw.hex())
            return
        days = self._alarm_weekday_mask(raw[5]) if len(raw) >= 6 else 0
        now = dt_util.now()
        for ahead in range(8):
            candidate = datetime.combine(
                now.date(), alarm_time, tzinfo=now.tzinfo
            ) + timedelta(days=ahead)
            if candidate <= now:
                continue
            # マスクが取れなければ曜日で絞らない（最初に来る時刻を採る）
            if days and not days & (1 << ((candidate.weekday() + 1) % 7)):
                continue
            self.hass.loop.call_soon_threadsafe(
                self._set_value, "next_alarm", candidate
            )
            return
        _LOGGER.debug("No upcoming Pavlok alarm matched payload: %s", raw.hex())

    def _alarm_weekday_mask(self, alarm_id: int) -> int:
        """Return the stored weekday bits for an alarm id, or 0 when unknown."""
        raw = self._known_alarm_records.get(str(alarm_id))
        if raw is None or not (roots := self._split_tlvs(raw)):
            return 0
        for name, field, _ in self._split_tlvs(roots[0][1]):
            if name == "TM" and len(field) >= 4:
                return field[3] & ~ALARM_ENABLED_BIT & 0x7F
        return 0

    def _set_value(self, key: str, value: Any) -> None:
        self.data[key] = value
        self._publish()

    def _command_notify(self, _: Any, payload: bytearray) -> None:
        """Route the command channel's replies by their leading opcode."""
        raw = bytes(payload)
        if not raw:
            return
        if raw[0] == BTN_READ_REPLY and len(raw) >= 3:
            option = parse_btn_action(raw[2:])
            if option:
                self.hass.loop.call_soon_threadsafe(self._set_button, raw[1], option)
            return
        if raw[0] == TIMER_CONFIG_REPLY and (config := parse_timer_config(raw)):
            self.hass.loop.call_soon_threadsafe(self._set_timer_config, config)

    def _set_timer_config(self, config: dict[str, Any]) -> None:
        key = "stopwatch_config" if config["kind"] == "stopwatch" else "timer_config"
        self.data[key] = config
        self._publish()

    async def async_refresh_timer_config(self) -> None:
        """Ask the device for its stored timer and stopwatch setups.

        Nothing comes back while a slot is empty, so an absent reply is not an
        error.  The stored duration is what makes a remaining time computable.
        """
        for command in TIMER_READ.values():
            await self._write(CHR_CMD7, command)

    def _set_button(self, slot: int, option: str) -> None:
        self.data["buttons"][slot] = option
        self._publish()

    async def async_refresh_buttons(self) -> None:
        """Ask the device for its six button assignments.

        The replies arrive as notifications on the command channel, which is why
        the assignments used to look unreadable: nothing comes back from a GATT
        read, and the official app never issues one either.
        """
        await self._write(CHR_CMD7, BTN_READ)

    def _alarm_write_ack(self, _: Any, payload: bytearray) -> None:
        """A-Write reports 00000000 on accept and 02000000 on reject."""
        _LOGGER.debug("Pavlok alarm write acknowledgement: %s", payload.hex())

    def _alarm_notify(self, _: Any, payload: bytearray) -> None:
        if self._alarm_done:
            self._alarm_data.extend(payload)
            self.hass.loop.call_soon_threadsafe(self._schedule_alarm_complete)

    def _schedule_alarm_complete(self) -> None:
        """Finish a variable-length alarm transfer after a short quiet period."""
        if self._alarm_setter:
            self._alarm_setter.cancel()
        if self._alarm_done:
            self._alarm_setter = self.hass.loop.call_later(0.2, self._alarm_done.set)

    def _history_notify(self, _: Any, payload: bytearray) -> None:
        if self._history_done is None:
            return
        self._history_data.extend(payload)
        if (
            not self._history_expect
            and len(self._history_data) >= _FILE_HEADER_BYTES
        ):
            # 応答自身が総バイト数を宣言するので、そこから完了条件を決める。
            total = parse_file_header(bytes(self._history_data))[3]
            self._history_expect = _FILE_HEADER_BYTES + total
        if self._history_expect and len(self._history_data) >= self._history_expect:
            self.hass.loop.call_soon_threadsafe(self._history_done.set)
            return
        self.hass.loop.call_soon_threadsafe(self._schedule_history_idle)

    def _schedule_history_idle(self) -> None:
        """Stop waiting once the stream has gone quiet for a while."""
        if self._history_idle:
            self._history_idle.cancel()
        if self._history_done:
            self._history_idle = self.hass.loop.call_later(
                _HISTORY_IDLE_SECONDS, self._history_done.set
            )

    async def _async_read_file(
        self, command: bytes, timeout: float, expect: int = 0
    ) -> bytes:
        """Issue one Files request and collect the notified response.

        ``expect`` of zero means the size is taken from the response's own header.
        """
        self._history_data = bytearray()
        self._history_expect = expect
        self._history_done = asyncio.Event()
        try:
            await self._write(CHR_FILES, command)
            await asyncio.wait_for(self._history_done.wait(), timeout=timeout)
        except TimeoutError as err:
            raise HomeAssistantError("Timed out reading Pavlok history") from err
        finally:
            self._history_done = None
            if self._history_idle:
                self._history_idle.cancel()
                self._history_idle = None
        return bytes(self._history_data)

    async def _async_read_alarm_payload(self) -> bytes:
        """Read the complete alarm document before any read-modify-write operation."""
        self._alarm_data = bytearray()
        self._alarm_done = asyncio.Event()
        try:
            await self._write(CHR_ACTRL, _actrl(ACTRL_READ))
            await asyncio.wait_for(self._alarm_done.wait(), timeout=5)
            payload = bytes(self._alarm_data)
            if not payload:
                # 空の応答から「登録0件」と「読み出し失敗」は区別できない。書き込みは
                # read-modify-write なので、ここで空を通すと既存のアラームを消してしまう。
                raise HomeAssistantError(
                    "Pavlok returned no alarm document; refusing to overwrite alarms"
                )
            return payload
        except TimeoutError as err:
            raise HomeAssistantError(
                "Timed out reading existing Pavlok alarms; refusing to overwrite them"
            ) from err
        finally:
            self._alarm_done = None
            if self._alarm_setter:
                self._alarm_setter.cancel()
                self._alarm_setter = None

    @staticmethod
    def _split_tlvs(data: bytes) -> list[tuple[str, bytes, bytes]]:
        result: list[tuple[str, bytes, bytes]] = []
        cursor = 0
        while cursor + 4 <= len(data):
            tag = data[cursor : cursor + 2].decode("ascii", errors="ignore")
            length = struct.unpack_from("<H", data, cursor + 2)[0]
            end = cursor + 4 + length
            if len(tag) != 2 or end > len(data):
                break
            result.append((tag, data[cursor + 4 : end], data[cursor:end]))
            cursor = end
        return result

    def _parse_alarm_document(self, document: bytes) -> list[tuple[str, bytes]]:
        """Keep exact existing HA records so a service never drops unknown fields."""
        roots = self._split_tlvs(document)
        body = (
            roots[0][1][2:]
            if roots and roots[0][0] == "AH" and len(roots[0][1]) >= 2
            else document
        )
        records: list[tuple[str, bytes]] = []
        for index, (tag, value, raw) in enumerate(self._split_tlvs(body)):
            if tag != "HA":
                continue
            fields = self._split_tlvs(value)
            # ID は2バイトのリトルエンディアン整数（正本の実測: ID len=2 0100）。
            # ASCII文字列として扱うと '\x02\x00' のような生バイトが表に出る。
            alarm_id = next(
                (
                    str(struct.unpack_from("<H", field, 0)[0])
                    if len(field) == 2
                    else field.decode(errors="replace")
                    for name, field, _ in fields
                    if name == "ID"
                ),
                str(index),
            )
            records.append((alarm_id, raw))
        return records

    def _build_alarm_record(self, values: dict[str, Any], alarm_id: str) -> bytes:
        """Encode a Pavlok HA alarm record using the documented TLV primitives."""
        alarm_time: dt_time = values["time"]
        day_bits = 0
        for day in values.get("days", []):
            day_bits |= {
                "sun": 1,
                "mon": 2,
                "tue": 4,
                "wed": 8,
                "thu": 16,
                "fri": 32,
                "sat": 64,
            }.get(day.lower()[:3], 0)
        tm = bytes(
            [
                alarm_time.minute,
                alarm_time.hour,
                0,
                day_bits | (ALARM_ENABLED_BIT if values.get("enabled", True) else 0),
            ]
        )
        unlock = {
            "none": AO_UNLOCK_NONE,
            "jacks": AO_UNLOCK_JACKS,
            "qr": AO_UNLOCK_QR,
            "puzzle": AO_UNLOCK_PUZZLE,
        }[values.get("unlock", "none")]
        body = (
            tlv("ID", struct.pack("<H", int(alarm_id)))
            + tlv("NM", values.get("name", "Alarm").encode()[:64])
            + tlv("TM", tm)
            + tlv("AO", bytes([unlock]))
        )
        for enabled, kind in (
            (values.get("vibe"), STIM_VIBE),
            (values.get("beep"), STIM_BEEP),
            (values.get("zap"), STIM_ZAP),
        ):
            if enabled:
                tag = {STIM_VIBE: "VI", STIM_BEEP: "BE", STIM_ZAP: "ZA"}[kind]
                body += tlv(
                    tag,
                    stimulus_bytes(
                        kind, values[f"{kind}_intensity"], values[f"{kind}_count"]
                    ),
                )
        return tlv("HA", body)

    async def _async_write_alarm_records(
        self, records: list[tuple[str, bytes]]
    ) -> None:
        message = seal_alarm_message(b"".join(raw for _, raw in records))
        await self._write(CHR_ACTRL, _actrl(ACTRL_WRITE_BEGIN))
        for offset in range(0, len(message), 20):
            await self._write(CHR_AWRITE, message[offset : offset + 20])
        await self._write(CHR_ACTRL, _actrl(ACTRL_COMMIT))
        self._known_alarm_records = dict(records)
        self.data["alarms"] = [{"id": alarm_id} for alarm_id, _ in records]
        self._publish()

    async def async_set_alarm(self, values: dict[str, Any]) -> None:
        """Append one alarm after successfully reading and retaining existing entries."""
        records = self._parse_alarm_document(await self._async_read_alarm_payload())
        alarm_id = str(
            max((int(item[0]) for item in records if item[0].isdigit()), default=-1) + 1
        )
        records.append((alarm_id, self._build_alarm_record(values, alarm_id)))
        await self._async_write_alarm_records(records)

    async def async_refresh_alarms(self) -> None:
        """Read and expose on-device alarm IDs without rewriting anything."""
        records = self._parse_alarm_document(await self._async_read_alarm_payload())
        self._known_alarm_records = dict(records)
        self.data["alarms"] = [{"id": alarm_id} for alarm_id, _ in records]
        self._publish()

    async def async_delete_alarm(self, alarm_id: str) -> None:
        """Delete one record while retaining all remaining raw device records."""
        records = self._parse_alarm_document(await self._async_read_alarm_payload())
        remaining = [
            (record_id, raw) for record_id, raw in records if record_id != str(alarm_id)
        ]
        if len(remaining) == len(records):
            raise HomeAssistantError(f"Alarm {alarm_id} does not exist")
        await self._async_write_alarm_records(remaining)

    async def async_set_wake(
        self, *, wake_time: dt_time | None = None, enabled: bool | None = None
    ) -> None:
        """Replace the one alarm reserved for the time/switch wake controls."""
        if wake_time is not None:
            self.data["wake_time"] = wake_time
        if enabled is not None:
            self.data["wake_enabled"] = enabled
        wake_time = self.data.get("wake_time", dt_time(7, 0))
        wake_enabled = bool(self.data.get("wake_enabled", False))
        records = self._parse_alarm_document(await self._async_read_alarm_payload())
        reserved_id: str | None = None
        retained: list[tuple[str, bytes]] = []
        for record_id, raw in records:
            fields = self._split_tlvs(self._split_tlvs(raw)[0][1])
            name = next(
                (
                    value.decode(errors="replace")
                    for tag, value, _ in fields
                    if tag == "NM"
                ),
                "",
            )
            if name == "Home Assistant Wake":
                reserved_id = record_id
            else:
                retained.append((record_id, raw))
        alarm_id = reserved_id or str(
            max((int(item[0]) for item in records if item[0].isdigit()), default=-1) + 1
        )
        retained.append(
            (
                alarm_id,
                self._build_alarm_record(
                    {
                        "time": wake_time,
                        "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                        "name": "Home Assistant Wake",
                        "enabled": wake_enabled,
                        "vibe": True,
                        "vibe_intensity": int(self.data.get("vibe_intensity", 100)),
                        "vibe_count": int(self.data.get("vibe_count", 1)),
                        "beep": False,
                        "zap": False,
                        "unlock": "none",
                    },
                    alarm_id,
                ),
            )
        )
        await self._async_write_alarm_records(retained)
        self._publish()

    def _async_sync_history_soon(self, *, force: bool) -> None:
        """Kick off a history read in the background.

        Reading holds the link for about a minute, so a reconnect loop must not be
        able to start one every time it succeeds.
        """
        now = monotonic()
        if (
            not force
            and self._last_history_sync is not None
            and now - self._last_history_sync < _HISTORY_MIN_INTERVAL
        ):
            return
        self._last_history_sync = now
        self.entry.async_create_background_task(
            self.hass,
            self._async_sync_history_quietly(),
            name=f"pavlok {self.address} history",
        )

    async def _async_sync_history_quietly(self) -> None:
        try:
            await self.async_sync_history()
        except Exception:  # noqa: BLE001 - a background read must not surface errors
            _LOGGER.debug("Pavlok background history sync failed", exc_info=True)

    async def _async_read_block(self, index: int) -> bytes | None:
        """Fetch one history block, retrying because a lost notification is fatal.

        A dropped notification leaves a hole that pushes everything after it out of
        alignment, so a whole-file transfer loses its newest blocks -- exactly the
        ones worth reading.  One block at a time keeps a retry cheap.
        """
        best = b""
        for _ in range(_BLOCK_ATTEMPTS):
            try:
                raw = await self._async_read_file(
                    files_command(FILES_TYPE_COARSE, index, 1), _BLOCK_TIMEOUT
                )
            except HomeAssistantError:
                continue
            if len(raw) <= _FILE_HEADER_BYTES + 12:
                continue
            total = parse_file_header(raw)[3]
            block = raw[_FILE_HEADER_BYTES : _FILE_HEADER_BYTES + total]
            if len(block) >= total:
                return block
            if len(block) > len(best):
                best = block
        # 完全に届かなくても、届いた分の記録は読める。宣言長を実際の長さに直して
        # おかないと、後続ブロックの走査位置がずれる。
        return truncate_block(best) if best else None

    async def async_sync_history(self) -> None:
        """Fetch coarse history block by block and expose the newest sleep session.

        The device serves files the way the official app asks for them: a 14 byte
        header announcing how many blocks exist, then the blocks themselves.
        """
        header = await self._async_read_file(
            files_command(FILES_TYPE_COARSE, 0, 0), 30, expect=_FILE_HEADER_BYTES
        )
        if len(header) < _FILE_HEADER_BYTES:
            raise HomeAssistantError("Pavlok did not return a history header")
        _, first_index, block_count, _ = parse_file_header(header)
        chunks: list[bytes] = []
        missing: list[int] = []
        for index in range(first_index, first_index + block_count):
            block = await self._async_read_block(index)
            if block is None:
                missing.append(index)
            else:
                chunks.append(block)
        if missing:
            _LOGGER.debug("Pavlok history blocks unavailable: %s", missing)
        data = header + b"".join(chunks)
        blocks = list(iter_blocks(data))
        records = [
            (start, duration)
            for _, _, _, _, body in blocks
            for start, duration, _ in find_sleep_records(body)
        ]
        _LOGGER.debug(
            "Pavlok history: %d of %d blocks, %d bytes, %d sleep records",
            len(blocks),
            block_count,
            len(data),
            len(records),
        )
        if not records:
            return
        start, duration = max(records)
        self.data["last_sleep"] = {
            "start": datetime.fromtimestamp(start, tz=timezone.utc),
            "end": datetime.fromtimestamp(start + duration, tz=timezone.utc),
            "duration": duration // 60,
        }
        self._publish()
        try:
            self._async_store_sleep_statistics(records)
        except Exception:  # noqa: BLE001 - statistics must not hide the sensor value
            _LOGGER.debug("Pavlok sleep statistics unavailable", exc_info=True)

    def _async_store_sleep_statistics(
        self, records: list[tuple[int, int]]
    ) -> None:
        """Record past sleep minutes so they survive the recorder's purge."""
        from homeassistant.components.recorder.models import StatisticMeanType
        from homeassistant.components.recorder.statistics import (
            async_add_external_statistics,
        )

        # Statistics are keyed by whole hours and must be unique and ordered, so
        # sessions starting in the same hour are summed rather than dropped.
        hourly: dict[datetime, float] = {}
        for start, duration in records:
            hour = datetime.fromtimestamp(start, tz=timezone.utc).replace(
                minute=0, second=0, microsecond=0
            )
            hourly[hour] = hourly.get(hour, 0.0) + duration / 60
        metadata = {
            "source": DOMAIN,
            # 統計IDは entity_id と同じ制限（小文字・数字・アンダースコアのみ）。
            # entry_id は大文字を含むULIDなので使えない。
            "statistic_id": (
                f"{DOMAIN}:{self.address.replace(':', '').lower()}_sleep_minutes"
            ),
            "name": f"{self.name} sleep duration",
            "unit_of_measurement": "min",
            # has_mean は 2026.11 で廃止。両方置いておけば新旧どちらでも通る。
            "has_mean": True,
            "mean_type": StatisticMeanType.ARITHMETIC,
            "has_sum": False,
            "unit_class": None,
        }
        async_add_external_statistics(
            self.hass,
            metadata,
            [
                {"start": hour, "mean": minutes, "state": minutes}
                for hour, minutes in sorted(hourly.items())
            ],
        )
