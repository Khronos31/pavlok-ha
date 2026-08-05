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
| Alarms | `sensor.pavlok_alarms` lists what is stored on the device — time, days, name, each stimulus, snooze and dismissal method. `pavlok.set_alarm` / `pavlok.delete_alarm` write to it. Alarms live on the device, so they still fire with the phone and Home Assistant switched off. `time.pavlok_alarm_time` + `switch.pavlok_alarm` drive one reserved alarm from the dashboard without writing an action |
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

## Actions

Five actions are registered under the `pavlok` domain. Every one of them takes an
optional `entry_id`, which is only needed when more than one Pavlok is
configured; with a single device it can be left out.

Entity ids below are written in their short form. The real ones carry the device
name, so `sensor.pavlok_alarms` is `sensor.pavlok_3_xxxx_alarms` on a real
install.

### `pavlok.stimulate`

Fire a stimulus immediately.

| Field | Required | Range | Meaning |
| --- | --- | --- | --- |
| `type` | yes | `vibe` / `beep` / `zap` | which stimulus |
| `intensity` | yes | 0–100 | strength |
| `count` | yes | 1–127 | how many times in a row. The official app can only send 1 |

```yaml
action: pavlok.stimulate
data:
  type: vibe
  intensity: 60
  count: 3
```

### `pavlok.set_alarm`

Adds one alarm. The device list is read first and written back with the new
entry appended, so existing alarms survive; if the read looks unreliable the
write is refused rather than risking the loss of alarms already on the device.
The id is assigned automatically as the highest existing id plus one.

| Field | Default | Meaning |
| --- | --- | --- |
| `time` | — (required) | when it fires, `HH:MM:SS` |
| `days` | *empty* | `sun` `mon` `tue` `wed` `thu` `fri` `sat`, any combination. **Leave it out for a one-shot alarm** — see below |
| `name` | `Alarm` | shown in the official app; stored on the device as plain text |
| `enabled` | `true` | write it disabled with `false` |
| `vibe` / `beep` / `zap` | `false` | enable that stimulus. **All three default to off**, so an alarm set without any of them is silent |
| `vibe_intensity` / `beep_intensity` / `zap_intensity` | 100 / 80 / 50 | 0–100, used only when the matching stimulus is on |
| `vibe_count` / `beep_count` / `zap_count` | 1 | 1–127 |
| `interval` | 15 | seconds between repeats until the alarm is dismissed (1–65535) |
| `unlock` | `none` | how it has to be dismissed: `none`, `jacks`, `qr`, `puzzle` |
| `jacks` | 20 | number of jumping jacks, used when `unlock: jacks` |
| `snooze` | `true` | allow snoozing |
| `snooze_zap` | `false` | zap when snoozed |
| `light_sleep` | `false` | wake during light sleep near the set time |

A weekday alarm:

```yaml
action: pavlok.set_alarm
data:
  time: "07:00:00"
  days: [mon, tue, wed, thu, fri]
  name: Wake up
  vibe: true
  vibe_intensity: 100
  vibe_count: 3
  snooze: true
```

A one-shot alarm — omit `days` entirely. The device stores no weekday bits and
fires at the next occurrence of that time, which is exactly what the official
app writes for a non-repeating alarm. Once it has rung, the device clears the
alarm's enable bit but keeps the record, so it shows up in
`sensor.pavlok_alarms` as `enabled: false` rather than disappearing:

```yaml
action: pavlok.set_alarm
data:
  time: "18:11:00"
  name: Take the bread out
  vibe: true
  vibe_intensity: 50
  vibe_count: 5
```

An alarm that will not let go until you have moved:

```yaml
action: pavlok.set_alarm
data:
  time: "06:30:00"
  days: [sat, sun]
  name: Up
  beep: true
  zap: true
  zap_intensity: 40
  unlock: jacks
  jacks: 15
  interval: 30
  snooze: false
```

### The reserved alarm (`time` + `switch`)

For the common case of "wake me at this time", there is a pair of entities that
need no action at all: `time.pavlok_alarm_time` sets the time and
`switch.pavlok_alarm` arms it. They drive **one** alarm, which is stored on the
device under the fixed id 65535 and the name `Home Assistant Wake`. The official
app numbers its own alarms upwards from 0, so nothing it creates will collide
with the reserved slot, and `pavlok.set_alarm` skips that id when allocating.

The reserved alarm is a **one-shot**: it fires at the next occurrence of the set
time. The device disarms a one-shot by clearing its enable bit once it has rung —
the record, its time and its id all stay — so the switch turns itself off shortly
after the alarm goes off, and turning it on again arms it for the next day. For
an alarm that repeats on its own, use `pavlok.set_alarm` with `days`.

The switch reflects what is actually on the device rather than what Home
Assistant last asked for, so deleting or disabling that alarm in the official app
also turns the switch off.

### `pavlok.delete_alarm`

Removes one alarm by id and writes the rest back. `id` is required, and comes
from the `alarms` attribute of `sensor.pavlok_alarms`; the service raises an
error if no alarm has that id.

```yaml
action: pavlok.delete_alarm
data:
  id: "3"
```

Deleting by name, for a one-shot alarm an automation created earlier:

```yaml
action: pavlok.delete_alarm
data:
  id: >-
    {{ state_attr('sensor.pavlok_alarms', 'alarms')
       | selectattr('name', 'eq', 'Take the bread out')
       | map(attribute='id') | first }}
```

### `pavlok.timer`

Drives the device's own timer and stopwatch. Saving and starting are separate on
the device: `save` stores the settings without running them, and `start` without
`seconds` runs whatever is stored.

| Field | Default | Meaning |
| --- | --- | --- |
| `action` | — (required) | `start`, `save`, `pause`, `resume`, `reset` |
| `type` | `timer` | `timer` counts down, `stopwatch` counts up |
| `seconds` | — | countdown length, 0–86399. Required for `save`; a stopwatch uses 0 |
| `stimulus` | `vibe` | what fires when the countdown ends |
| `interval` | 0 | seconds between stimuli, 0–255 |
| `interval_mode` | `once` | `once` fires a single stimulus; `repeat` keeps firing every interval until the timer is reset |

```yaml
action: pavlok.timer
data:
  action: start
  type: timer
  seconds: 300
  stimulus: beep
  interval: 5
  interval_mode: repeat
```

```yaml
action: pavlok.timer
data:
  action: reset
```

### `pavlok.sync_history`

Forces a history read: activity and sleep records are pulled from the device and
past sleep durations are written to long-term statistics. This happens by itself
when a sleep recording ends and once per connection, so the service is only
needed to catch up sooner. The transfer holds the Bluetooth link for around a
minute, and is slow and retry-prone over an ESPHome proxy.

```yaml
action: pavlok.sync_history
```

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
