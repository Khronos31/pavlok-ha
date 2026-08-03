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
- **read a log of commands sent to the device**, which includes alarm names in
  plain text,
- **fire the device** — vibrate, beep, or **zap** you.

This is a property of the *device*, not of this integration. This project simply
makes that access usable from Home Assistant. **You should assume the data on
your Pavlok is readable by others while you wear it**, and that a second-hand
unit may still hold the previous owner's history. There is currently **no known
way to erase the on-device history** (not via the app, not via BLE).

For that reason:

- `button.pavlok_zap` is created **disabled**. Enable it from the entity
  settings if you want a one-press zap on your dashboard. The `pavlok.stimulate`
  service can still send a zap, so automations remain possible without it.
- Nothing in this integration fires a stimulus on its own.

## Features

| Area | What you get |
| --- | --- |
| Stimulus | `button.pavlok_vibe` / `_beep` / `_zap` plus a `number` pair per type for intensity and repeats, and the `pavlok.stimulate` service. The app can only send one repeat; BLE accepts up to 127 |
| Alarms | `sensor.pavlok_alarms` lists what is stored on the device — time, days, name, each stimulus, snooze and dismissal method. `pavlok.set_alarm` / `pavlok.delete_alarm` write to it. Alarms live on the device, so they still fire with the phone and Home Assistant switched off |
| Sleep | `switch.pavlok_sleep_detection` (automatic detection) and `switch.pavlok_sleep_recording` (start/stop a session). `sensor.pavlok_last_sleep` gives bed time, wake time and duration, and updates by itself when a recording ends |
| Timer | `pavlok.timer` saves, starts, pauses, resumes and resets the device-side timer or stopwatch. `sensor.pavlok_timer` shows the elapsed time and the stored setup; `sensor.pavlok_timer_finishes` gives the end time, which Home Assistant renders as a live countdown |
| Buttons | `select` per slot assigns the three physical buttons (short and long press), including the repeat count. The assignments are read back from the device, so changes made in the official app show up too |
| Events | `event.pavlok_button` — top/mid/bottom × short/long, as automation triggers |
| Sensors | activity, steps, battery, connection, and RSSI with a per-proxy breakdown |
| History | fetched automatically when a sleep recording ends and once per connection; `pavlok.sync_history` forces it. Past sleep durations go to long-term statistics |

## Requirements

- Home Assistant 2026.7 or newer (developed and tested against 2026.7.4;
  earlier versions may work but are untested)
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

## Notes on the data

Values reported by this integration are **the same values the official Pavlok
app shows** — they are read from the device's own stored records, not
recalculated here. This was verified against the app for a full night (all six
sleep sessions matched exactly, as did the daily step total).

Two things are deliberately *not* exposed:

- **Sleep score** — the app computes it rather than storing it on the device, so
  there is nothing to read.
- **Sleep stages** (REM / light / deep) — not found in the device's records. A
  trailing block of each sleep record is still undecoded, but the stages appear
  to be derived by the app rather than stored.

Hand detection is not exposed either; the setting can be toggled but the feature
did not behave as expected during testing.

The bedtime window in the app's *sleep tracking time range* screen is never sent
to the device — only "detect sleep" on or off is. The times are used by the app
when it interprets the recorded data.

## Status

Working, but young. Reading and writing have both been exercised on a real
device: alarms (list, add, delete), stimulus, button assignment, timer setup and
control, sleep tracking, and history transfer.

Transfers over an ESPHome proxy drop notifications, so history is fetched one
block at a time and retried. A direct Bluetooth adapter is noticeably faster and
more reliable.

There are no automated tests yet.

## License

MIT
