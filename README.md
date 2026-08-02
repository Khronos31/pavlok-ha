# Pavlok for Home Assistant

Local control of the **Pavlok 3 (Shock Clock 3)** wearable over Bluetooth LE —
no cloud, no account, no phone app required. Connects through Home Assistant's
Bluetooth integration, so any Bluetooth adapter or **ESPHome `bluetooth_proxy`**
works as the radio.

The protocol was reverse-engineered from a real device and verified against the
official app. This integration talks to the device directly over BLE.

## ⚠️ Security notice — please read

The Pavlok 3's BLE interface has **no pairing, no bonding and no authentication
of any kind.** Anyone within radio range (tens of metres) can connect and:

- **read your full activity history** — steps, movement, and sleep start/end
  times, going back weeks (the device stores it internally),
- **fire the device** — vibrate, beep, or **zap** you.

This is a property of the *device*, not of this integration. This project simply
makes that access usable from Home Assistant. **You should assume the data on
your Pavlok is readable by others while you wear it**, and that a second-hand
unit may still hold the previous owner's history. There is currently **no known
way to erase the on-device history** (not via the app, not via BLE).

For that reason:

- The **Zap** button and `pavlok.stimulate` with `type: zap` are **disabled by
  default**. Enable them explicitly in the integration options if you want them.
- Nothing in this integration fires a stimulus on its own.

## Features

| Area | What you get |
| --- | --- |
| Stimulus | `button.pavlok_vibe` / `_beep` / `_zap` (zap off by default) + `pavlok.stimulate` service (choose type, intensity, count — the app can only send one repeat; BLE can send many) |
| Sensors | activity (per-second & cumulative), steps, battery, RSSI, connection, last sleep (bed/wake/duration), next alarm, timer/stopwatch state |
| Events | `event.pavlok_button` — top/mid/bottom × short/long, as automation triggers |
| Alarms | read the on-device alarm list; set/delete alarms (survive phone & HA being off) via services; a `time` + `switch` pair for your usual wake alarm |
| Buttons | assign the three physical buttons (`select` per slot) |
| Timer | start a device-side timer; `sensor.pavlok_timer` shows what's running |
| History | `pavlok.sync_history` pulls stored records into Home Assistant statistics |

## Requirements

- Home Assistant 2024.8 or newer
- A Bluetooth adapter on the HA host, or an ESPHome device running
  `bluetooth_proxy` within range of the Pavlok. The Pavlok's radio is weak, so
  proxy placement matters.

## Installation (HACS)

1. HACS → Integrations → ⋮ → Custom repositories → add
   `https://github.com/Khronos31/pavlok-ha` as an *Integration*.
2. Install **Pavlok**, restart Home Assistant.
3. The device should be discovered automatically (advertised name
   `Pavlok-3-XXXX`). Otherwise add it via *Settings → Devices & Services → Add
   Integration → Pavlok*.

## Status

Early release. The protocol is fully mapped for stimulus, time, history, alarms,
timers, button assignment and events. Sleep *stages* (REM/light/deep) are not
decoded yet. Hand-detection is not exposed (the device feature itself is
unreliable). See the source for details.

## License

MIT
