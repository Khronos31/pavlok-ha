"""Constants and low-level protocol helpers for the Pavlok 3 (Shock Clock 3).

All values here were reverse-engineered from a real device (2026-08-02) and
verified against the official app. Getting a byte wrong here is expensive, so
this module is the single source of truth for the wire format. The higher-level
integration code should import from here rather than hard-coding bytes.

Protocol summary
----------------
Vendor service family: ``156eN000-a300-4fea-897b-86f698d74461``.
No pairing / bonding / authentication is required to connect, read or write.
The device silently ignores writes it does not accept (a successful ACK does
NOT mean the write took effect) and rejects malformed alarm payloads with a
CRC error, so verify by observing actual behaviour, not by the write result.
"""

from __future__ import annotations

import struct

DOMAIN = "pavlok"

# Advertised local name. Pavlok advertises no service UUID, so discovery must
# match on the name. Address is a public/static address (not rotating RPA).
LOCAL_NAME_PREFIX = "Pavlok-3-"


def svc(n: int) -> str:
    """Return a vendor service UUID for the ``156eN000`` family."""
    return f"156e{n:04x}-a300-4fea-897b-86f698d74461"


def chr16(handle16: int) -> str:
    """Return a 128-bit UUID for a 16-bit vendor characteristic id."""
    return f"0000{handle16:04x}-0000-1000-8000-00805f9b34fb"


# --- Services -------------------------------------------------------------
SVC_CMD = svc(0)        # 156e0000: status/command channel (0x0001 battery notify)
SVC_STIM = svc(0x1000)  # 156e1000: Vibe/Beep/Zap/LED/Time/HD/Button/DAQ
SVC_DATA = svc(0x2000)  # 156e2000: Time2/Events/Timers/Files/AlarmTime
SVC_SENSOR = svc(0x4000)
SVC_ALARM = svc(0x5000)  # 156e5000: A-Ctrl/A-Write/A-Ntfy
SVC_7000 = svc(0x7000)   # 156e7000: general command channel (NOT DFU). 0x7999 = do not touch

BATTERY_SVC = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_CHR = "00002a19-0000-1000-8000-00805f9b34fb"
DEVICE_INFO_SVC = "0000180a-0000-1000-8000-00805f9b34fb"

# --- 156e1000 stimulus / settings ----------------------------------------
CHR_VIBE = chr16(0x1001)   # RW  5 bytes
CHR_BEEP = chr16(0x1002)   # RW  5 bytes
CHR_ZAP = chr16(0x1003)
# Sleep recording control.  Start is 01 02 (the app pads it with three bytes that
# turn out to be optional); stop is a single 00.
CHR_DAQ = chr16(0x1008)
DAQ_START = bytes([0x01, 0x02])
DAQ_STOP = bytes([0x00])    # RWN 2 bytes
CHR_LED = chr16(0x1004)
CHR_TIME = chr16(0x1005)   # RW  BCD 8 bytes (see time_bytes)
CHR_HD = chr16(0x1006)     # hand detection; byte0 bit0 = enabled
CHR_BUTTON = chr16(0x1007)
CHR_DAQ = chr16(0x1008)

# --- 156e2000 data --------------------------------------------------------
CHR_EVENTS = chr16(0x2002)     # notify: activity/steps/button/timer/sleep events
CHR_TIMERS = chr16(0x2003)
CHR_FILES = chr16(0x2009)      # history transfer (write command -> notify stream)
CHR_ALARM_TIME = chr16(0x200a)  # next-alarm cache (BCD-ish, see below)

# --- 156e5000 alarm channel ----------------------------------------------
CHR_ACTRL = chr16(0x5001)   # mode byte: 0x06 read, 0x01 write-begin, 0x00 commit
CHR_AWRITE = chr16(0x5002)  # TLV payload in <=20 byte chunks; notify 00000000=ok 02000000=reject
CHR_ANTFY = chr16(0x5003)

# --- 156e0000 status ------------------------------------------------------
CHR_STATUS = chr16(0x0001)  # notify ~15s; byte2 = battery %
# Automatic sleep detection on/off.  Write only; the state comes back as an event.
CHR_SLEEP_AUTO = chr16(0x0008)
SLEEP_AUTO_ON = bytes([0x02, 0x01])
SLEEP_AUTO_OFF = bytes([0x02, 0x00])

# --- 156e7000 command channel --------------------------------------------
CHR_CMD7 = chr16(0x7001)    # timer/stopwatch + button assignment commands

# The default alarm profile name used by the app. A-Ctrl carries this string.
ALARM_PROFILE = b"Single 1"


# ==========================================================================
# Stimulus  (0x1001 Vibe / 0x1002 Beep / 0x1003 Zap)
# ==========================================================================
# First byte = fire-bit (0x80) | count (1..127). Writes without bit7 are ignored
# (they do NOT change the stored value either). Firing does NOT change the
# stored setting. Intensity 0..100.
#   Vibe/Beep: [0x80|count, 0x0c, intensity, 0x16, 0x16]   (5 bytes)
#   Zap:       [0x80|count, intensity]                     (2 bytes)
# Byte 1 (0x0c) is an unconfirmed waveform/duration field. The stored quick-remote
# defaults differ per type (vibe 0x0c, beep 0x06), but the official app always
# sends 0x0c when firing with an explicit intensity — observed on both Vibe and
# Beep characteristics — so we do the same. Length must match exactly or the
# device silently ignores the write.
STIM_VIBE = "vibe"
STIM_BEEP = "beep"
STIM_ZAP = "zap"


def stimulus_bytes(kind: str, intensity: int, count: int) -> bytes:
    """Build a fire-now payload. Length differs per kind or the write is ignored."""
    count = max(1, min(127, int(count)))
    intensity = max(0, min(100, int(intensity)))
    head = 0x80 | count
    if kind == STIM_ZAP:
        return bytes([head, intensity])
    return bytes([head, 0x0C, intensity, 0x16, 0x16])


# ==========================================================================
# Time  (0x1005)  BCD 8 bytes: [sec, min, hour, day, weekday, month, year, tz]
# ==========================================================================
# weekday: 0=Sunday. tz in 15-minute units (0x24 = 36 => +540 min => UTC+9).


def _bcd(n: int) -> int:
    return ((n // 10) << 4) | (n % 10)


def time_bytes(dt, tz_minutes: int) -> bytes:
    """Encode a local datetime for 0x1005. ``dt`` is a naive local datetime."""
    return bytes([
        _bcd(dt.second), _bcd(dt.minute), _bcd(dt.hour),
        _bcd(dt.day), _bcd(dt.weekday_sun0()) if hasattr(dt, "weekday_sun0")
        else _bcd((dt.weekday() + 1) % 7),
        _bcd(dt.month), _bcd(dt.year % 100),
        tz_minutes // 15,
    ])


# ==========================================================================
# Events  (0x2002 notify)  first byte = event type
# ==========================================================================
EVT_ACTIVITY = 0x02   # 11 bytes: [02, cum_activity u32 @1, time u16 @5, ...]
EVT_STEPS = 0x03      # [03, cumulative_steps u32 LE]
EVT_BUTTON = 0x05     # [05, (button_mask<<4)|press]  btn 0x10 top /0x20 mid /0x40 bottom; press 1 short 3 long
EVT_TIMER = 0x0D      # 0d idle | 0d KIND 90 <elapsed u24 LE> 00 ; KIND 01=stopwatch 02=timer
# 04 <自動検出 00/01> <記録中 00/02>。2バイト目を読み落としていて、
# 「自動追跡が有効か」を捨てていた（2026-08-03 にアプリの操作4種で確認）。
EVT_SLEEP = 0x04

BUTTON_TOP = 0x10
BUTTON_MID = 0x20
BUTTON_BOTTOM = 0x40
PRESS_SHORT = 1
PRESS_LONG = 3


def parse_activity(payload: bytes) -> int | None:
    """Cumulative activity counter (increments only while moving)."""
    if len(payload) >= 5 and payload[0] == EVT_ACTIVITY:
        return struct.unpack_from("<I", payload, 1)[0]
    return None


def parse_steps(payload: bytes) -> int | None:
    if len(payload) >= 5 and payload[0] == EVT_STEPS:
        return struct.unpack_from("<I", payload, 1)[0]
    return None


def parse_button(payload: bytes):
    """Return (button_mask, press_type) or None."""
    if len(payload) >= 2 and payload[0] == EVT_BUTTON:
        b = payload[1]
        return (b & 0xF0, b & 0x0F)
    return None


def parse_timer(payload: bytes):
    """Return (kind, elapsed_seconds, running) or ('idle', 0, False).

    Byte 2 carries the run state: 0x80 just started, 0x90 running, 0x10 stopped.
    """
    if not payload or payload[0] != EVT_TIMER:
        return None
    if len(payload) < 7:
        return ("idle", 0, False)
    kind = {1: "stopwatch", 2: "timer"}.get(payload[1], "unknown")
    elapsed = payload[3] | (payload[4] << 8) | (payload[5] << 16)
    return (kind, elapsed, bool(payload[2] & TIMER_FLAG_RUNNING))


def parse_sleep_state(payload: bytes):
    """Return (automatic_detection_on, recording_now) or None."""
    if len(payload) < 3 or payload[0] != EVT_SLEEP:
        return None
    return (payload[1] == 0x01, payload[2] == 0x02)


# ==========================================================================
# History / Files  (0x2009)
# ==========================================================================
# Command (9 bytes): [type, start u32 LE, end u32 LE]
#   type 0x03 or 0x82 (=0x02|0x80). end=0 -> header only. end=0xffffffff -> all.
# Enable notify on the CCCD, write command, receive notify stream, then unsub.
# File header (14 bytes): <magic u16><first_index u32><block_count u32><total_bytes u32>
# Block: <marker u8><type u8><len u16><index u32><start_unix u32> + len body
#        marker 0x3f = more follows, 0x7f = final block
FILES_TYPE_COARSE = 0x03   # steps/summary style (contains sleep records)
FILES_TYPE_FINE = 0x82     # fine-grained (activity); fills ~every 6h

BLOCK_MARK_MORE = 0x3F
BLOCK_MARK_LAST = 0x7F


def files_command(type_: int, start: int = 0, count: int = 0xFFFFFFFF) -> bytes:
    """Ask for ``count`` blocks beginning at block ``start``.

    The second field is a count, not an end index (measured 2026-08-03):
    start=26 count=1 returns one block, start=0 count=2 returns the two oldest,
    and count=0 returns only the 14 byte header.  ``start`` is clamped to the
    oldest block the device still holds.
    """
    return struct.pack("<BII", type_, start, count)


def parse_file_header(data: bytes):
    """(magic, first_index, block_count, total_bytes)."""
    return struct.unpack_from("<HIII", data, 0)


BLOCK_MAX_BODY = 4096


def _is_block_header(data: bytes, p: int) -> bool:
    """Test whether a block header plausibly starts at ``p``."""
    if p + 12 > len(data):
        return False
    mark, _, ln, _, start_unix = struct.unpack_from("<BBHII", data, p)
    return (
        mark in (BLOCK_MARK_MORE, BLOCK_MARK_LAST)
        and 0 < ln <= BLOCK_MAX_BODY
        and _UNIX_MIN <= start_unix <= _UNIX_MAX
    )


def truncate_block(block: bytes) -> bytes:
    """Rewrite a partly received block's length field to match what arrived."""
    if len(block) < 12:
        return b""
    return block[:2] + struct.pack("<H", len(block) - 12) + block[4:]


def iter_blocks(data: bytes):
    """Yield (mark, type, index, start_unix, body) for each block after the header.

    A weak BLE link drops notifications, which leaves a hole in the middle of the
    file and pushes every following block out of alignment.  Stopping at the hole
    would discard the newest blocks, which are exactly the ones a caller wants, so
    scan forward for the next plausible header instead.
    """
    p = 14
    n = len(data)
    while p + 12 <= n:
        if not _is_block_header(data, p):
            p += 1
            continue
        mark, btype, ln, index, start_unix = struct.unpack_from("<BBHII", data, p)
        yield (mark, btype, index, start_unix, data[p + 12:p + 12 + ln])
        p += 12 + ln


# Sleep session record inside a coarse (type 3) block body. Length is VARIABLE
# and the duration field width depends on it (verified against the official app
# on 2026-08-03 — all 6 sessions of one night matched exactly):
#
#   21 <len> <u16 ?> <start_unix u32> <duration> [tail]
#     len 7  -> duration is 1 byte  (seconds).  short "tried to sleep" sessions
#     len >=9 -> duration is u16 LE (seconds), followed by `len-8` tail bytes
#
# A full night produced len=94, i.e. an 86-byte tail that is NOT decoded yet.
# Ground truth for that night (app): 23:42:25 -> 07:01:09, 7h18m; stages
# Awake 45m / REM 3h15m / Light 3h15m / Deep 15m (all quantised to 15 min).
# The tail is where the stage breakdown is expected to live.
SLEEP_RECORD_TAG = 0x21
_UNIX_MIN = 1_700_000_000
_UNIX_MAX = 2_000_000_000


def find_sleep_records(body: bytes):
    """Yield ``(start_unix, duration_seconds, tail)`` for each sleep session.

    ``tail`` is the not-yet-decoded remainder (empty for short sessions).
    """
    i = 0
    n = len(body)
    while i + 2 <= n:
        if body[i] != SLEEP_RECORD_TAG:
            i += 1
            continue
        ln = body[i + 1]
        if not (7 <= ln <= 200) or i + 2 + ln > n:
            i += 1
            continue
        payload = body[i + 2:i + 2 + ln]
        start_unix = struct.unpack_from("<I", payload, 2)[0]
        if not (_UNIX_MIN <= start_unix <= _UNIX_MAX):
            i += 1
            continue
        if ln == 7:
            duration = payload[6]
            tail = b""
        else:
            duration = struct.unpack_from("<H", payload, 6)[0]
            tail = bytes(payload[8:])
        yield (start_unix, duration, tail)
        i += 2 + ln


# ==========================================================================
# Alarm  (156e5000, TLV with CRC-16/CCITT-FALSE)
# ==========================================================================
# A-Ctrl mode byte: 0x06 read, 0x01 write-begin, 0x00 commit (followed by profile).
# TLV: 2-char ASCII tag + u16 LE length + body (nested for container tags).
# Container "AH" holds a 2-byte checksum (its first 2 body bytes) computed as
# CRC-16/CCITT-FALSE over the ENTIRE AH TLV with those 2 bytes zeroed, stored LE.
ACTRL_READ = 0x06
ACTRL_WRITE_BEGIN = 0x01
ACTRL_COMMIT = 0x00
# The ringing-alarm screen sends two short A-Ctrl actions that carry no profile
# name (captured 2026-08-12, verified against the official app): stop dismisses
# the ringing alarm, snooze delays it. The app follows a stop with a full alarm
# re-write to re-arm a repeating alarm.
ACTRL_STOP = bytes([0x02, 0x00])
ACTRL_SNOOZE = bytes([0x03, 0x01])

AWRITE_OK = bytes.fromhex("00000000")
AWRITE_REJECT = bytes.fromhex("02000000")  # CRC mismatch / malformed

DOW_MON_FIRST = "月火水木金土日"  # for reference; bitmask below is Sunday-based
# TM 4th byte: bit0=Sun bit1=Mon ... bit6=Sat, bit7=enabled.
# mask 0 with bit7 set is a ONE-SHOT alarm, not a dead one: the official app writes
# exactly that for a non-repeating alarm (btsnoop 2026-08-05, TM=00 11 18 80), and the
# device then reported it on the next-alarm characteristic (0x200a: 00 11 18 00 03 01 00
# = 18:11, alarm id 1).  The app also omits unused PH/ZH tags in a record it creates,
# while records it edits keep them; the device accepts both.
ALARM_ENABLED_BIT = 0x80

# AO (options) bitmask, confirmed 2026-08-02:
AO_UNLOCK_NONE = 0x01     # dismiss method: none (default)   <- must set one of the 4
AO_UNLOCK_JACKS = 0x02    # jumping jacks (pairs with JL count)
AO_UNLOCK_QR = 0x04       # QR code
AO_UNLOCK_PUZZLE = 0x80   # puzzle/quiz
AO_LIGHT_SLEEP = 0x08     # "light sleep"
AO_ESCALATE = 0x20        # escalating alarm (pairs with ES tag)
AO_SMART = 0x40           # smart alarm (pairs with SM tag)
# NOTE: AO must contain exactly one unlock method (bit0/1/2/7). AO=0x00 is NOT
# "no challenge"; use AO_UNLOCK_NONE (0x01). Getting this wrong created an
# un-dismissable alarm on the real device.

# SN (snooze) bitfield: bit0 = snooze enabled, bit1 = snooze-zap.
# WD は画面上の対応項目が見つかっていないが、実機のレコードは常に 30 だった。
ALARM_WD_DEFAULT = 30

SN_SNOOZE = 0x01
SN_SNOOZE_ZAP = 0x02

# The one alarm the time/switch pair drives.  Its id is pinned to the top of the u16
# ID field rather than allocated: the official app counts up from 0, so nothing it
# creates will ever land on this slot and overwrite the reserved alarm.  New alarms
# from pavlok.set_alarm must therefore skip it when picking the next free id.
RESERVED_ALARM_ID = "65535"
RESERVED_ALARM_NAME = "Home Assistant Wake"


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def tlv(tag: str, body: bytes) -> bytes:
    return tag.encode("ascii") + struct.pack("<H", len(body)) + body


def bcd(value: int) -> int:
    return ((value // 10) << 4) | (value % 10)


def alarm_stimulus_bytes(kind: str, intensity: int, count: int) -> bytes:
    """Build the MC/PC/ZC payload carried inside an alarm.

    Same shape as a fire-now stimulus but the two trailing bytes are 0xFA rather
    than 0x16, and a disabled stimulus is written with its enable bit cleared
    instead of being left out.
    """
    enabled = count > 0 and intensity > 0
    head = (0x80 if enabled else 0x00) | (max(1, min(127, count)) & 0x7F)
    intensity = max(0, min(100, int(intensity)))
    if kind == STIM_ZAP:
        return bytes([head, intensity])
    return bytes([head, 0x0C, intensity, 0xFA, 0xFA])


_ALARM_DAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


def _split_alarm_tlvs(data: bytes):
    cursor = 0
    while cursor + 4 <= len(data):
        tag = data[cursor : cursor + 2].decode("ascii", errors="ignore")
        length = struct.unpack_from("<H", data, cursor + 2)[0]
        end = cursor + 4 + length
        if len(tag) != 2 or end > len(data):
            return
        yield tag, data[cursor + 4 : end]
        cursor = end


def parse_alarm_record(raw: bytes) -> dict:
    """Decode one stored alarm into the fields the app shows."""
    roots = list(_split_alarm_tlvs(raw))
    if not roots or roots[0][0] != "HA":
        return {}
    out: dict = {}
    for tag, value in _split_alarm_tlvs(roots[0][1]):
        if tag == "ID" and len(value) == 2:
            out["id"] = str(struct.unpack("<H", value)[0])
        elif tag == "AN":
            out["name"] = value.decode("utf-8", errors="replace")
        elif tag == "TM" and len(value) >= 4:
            hour = (value[2] >> 4) * 10 + (value[2] & 0x0F)
            minute = (value[1] >> 4) * 10 + (value[1] & 0x0F)
            out["time"] = f"{hour:02d}:{minute:02d}"
            out["enabled"] = bool(value[3] & ALARM_ENABLED_BIT)
            out["days"] = [
                name
                for bit, name in enumerate(_ALARM_DAYS)
                if value[3] & (1 << bit)
            ]
        elif tag == "WI" and len(value) == 2:
            out["interval"] = struct.unpack("<H", value)[0]
        elif tag == "SN" and value:
            out["snooze"] = bool(value[0] & SN_SNOOZE)
            out["snooze_zap"] = bool(value[0] & SN_SNOOZE_ZAP)
        elif tag == "AO" and value:
            out["unlock"] = {
                AO_UNLOCK_JACKS: "jacks",
                AO_UNLOCK_QR: "qr",
                AO_UNLOCK_PUZZLE: "puzzle",
            }.get(value[0] & ~(AO_LIGHT_SLEEP | AO_ESCALATE | AO_SMART) & 0xFF, "none")
            out["light_sleep"] = bool(value[0] & AO_LIGHT_SLEEP)
        elif tag == "JL" and value:
            out["jacks"] = value[0]
        elif tag in ("MH", "PH", "ZH"):
            kind = {"MH": STIM_VIBE, "PH": STIM_BEEP, "ZH": STIM_ZAP}[tag]
            inner = next(iter(_split_alarm_tlvs(value)), None)
            if inner and inner[1]:
                head = inner[1][0]
                if head & 0x80:
                    out[kind] = {
                        "count": head & 0x7F,
                        "intensity": inner[1][-3] if kind != STIM_ZAP else inner[1][1],
                    }
    return out


def seal_alarm_message(ah_body_without_crc: bytes) -> bytes:
    """Wrap an AH body (already starting with 2 placeholder bytes 00 00 then AP/HA...)
    into a full AH TLV with a valid CRC in place of the placeholder."""
    msg = tlv("AH", b"\x00\x00" + ah_body_without_crc)
    crc = crc16_ccitt_false(msg)  # over whole message, checksum field already 0
    return msg[:4] + struct.pack("<H", crc) + msg[6:]


# ==========================================================================
# 156e7000 command channel: timer/stopwatch + button assignment
# ==========================================================================
# Timer/stopwatch set+start:
#   22 <len u16 LE> <12=SW|13=timer> f5 <n> <duration varlen> f0 04 <stim> 2a <interval> <80|00>
# Duration uses the UTF-8-style varlen integer below (NOT protobuf varint).
# Operation:  13 03 11 02 <op> <kind>   op 11=start 12=reset 13=pause 14=resume ; kind 01=SW 02=timer
CMD7_HELLO = bytes.fromhex("120d0000000000")  # written by the app right after connect
STIM_CODE = {STIM_VIBE: 1, STIM_BEEP: 2, STIM_ZAP: 3}

TIMER_KIND_STOPWATCH = 0x12
TIMER_KIND_TIMER = 0x13
TIMER_OP_START = 0x11
TIMER_OP_RESET = 0x12
TIMER_OP_PAUSE = 0x13
TIMER_OP_RESUME = 0x14
# Operation frame prefix; the trailing kind byte is 0x01 stopwatch / 0x02 timer.
TIMER_OP_FRAME = bytes.fromhex("13031102")
TIMER_OP_KIND = {"stopwatch": 0x01, "timer": 0x02}
# Trailing flag of the 0x22 setup command, outside the u16 body length.
# Confirmed on the device 2026-08-03 by counting vibrations: 0x80 fired once,
# 0x00 fired again every interval.  The notifications are identical either way.
TIMER_ONCE = 0x80
TIMER_REPEAT = 0x00
# Timer event flag byte: 0x80 just started, 0x90 running, 0x10 stopped.
TIMER_FLAG_RUNNING = 0x80


def varlen_encode(v: int) -> bytes:
    """UTF-8-style length-prefixed integer with per-tier bias (used by timer duration)."""
    if v < 128:
        return bytes([v])
    v -= 128
    if v < (1 << 14):
        return bytes([0x80 | (v >> 8), v & 0xFF])
    v -= (1 << 14)
    if v < (1 << 21):
        return bytes([0xC0 | (v >> 16), (v >> 8) & 0xFF, v & 0xFF])
    v -= (1 << 21)
    return bytes([0xE0 | (v >> 24), (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF])


def varlen_decode(b: bytes) -> int:
    if b[0] < 0x80:
        return b[0]
    if b[0] < 0xC0:
        return 128 + ((b[0] & 0x3F) << 8) + b[1]
    if b[0] < 0xE0:
        return 128 + (1 << 14) + ((b[0] & 0x1F) << 16) + (b[1] << 8) + b[2]
    return (128 + (1 << 14) + (1 << 21) + ((b[0] & 0x0F) << 24)
            + (b[1] << 16) + (b[2] << 8) + b[3])


# Button assignment: 02 <slot 1..6> <action bytes>
#   slots: 1 top-short 2 mid-short 3 bottom-short 4 top-long 5 mid-long 6 bottom-long
#   (mid-long has no stopwatch/timer option in the app UI, but the firmware
#    accepts them anyway.)
BTN_SLOT = {
    ("top", "short"): 1, ("mid", "short"): 2, ("bottom", "short"): 3,
    ("top", "long"): 4, ("mid", "long"): 5, ("bottom", "long"): 6,
}
# Action payloads (append after "02 <slot>"):
BTN_ACT_STOPWATCH = bytes.fromhex("11021001")
BTN_ACT_TIMER = bytes.fromhex("11021002")
BTN_ACT_SLEEP = bytes.fromhex("130102")
BTN_ACT_DISABLE = bytes.fromhex("ff")
# Read the six assignments back: write BTN_READ to 0x7001 with notifications on and
# the device answers with one 01 <slot> <action> frame per slot.  The action bytes
# are exactly what the write command takes, so one codec serves both directions.
BTN_READ = bytes.fromhex("0101")
BTN_READ_REPLY = 0x01
# The command channel answers three read requests, each as a notification on
# itself.  They stay silent while nothing is stored, which is what made them look
# write-only until a timer had actually been saved (2026-08-03).
TIMER_READ = {"timer": bytes.fromhex("1013"), "stopwatch": bytes.fromhex("1012")}
TIMER_CONFIG_REPLY = 0x12


def parse_timer_config(payload: bytes):
    """Decode a stored timer/stopwatch setup, or None.

        12 13 f5 03 01 8d90 f0 04 01 2a 0a 80
        │  │  └ 時間 (01 + 可変長整数)  │     └ 80=一度に / 00=毎日
        │  └ 13=タイマー 12=SW          └ 刺激・間隔
        └ 応答
    """
    if len(payload) < 8 or payload[0] != TIMER_CONFIG_REPLY or payload[2] != 0xF5:
        return None
    length = payload[3]
    tail = payload[4 + length :]
    if len(tail) < 6:
        return None
    try:
        seconds = varlen_decode(payload[5 : 4 + length])
    except (IndexError, ValueError):
        return None
    return {
        "kind": "stopwatch" if payload[1] == TIMER_KIND_STOPWATCH else "timer",
        "seconds": seconds,
        "stimulus": {v: k for k, v in STIM_CODE.items()}.get(tail[2]),
        "interval": tail[4],
        "interval_mode": "repeat" if tail[5] == TIMER_REPEAT else "once",
    }
# The app offers one to five repeats for a stimulus assignment.
BTN_STIM_COUNTS = range(1, 6)
_BTN_FIXED = {
    "disabled": BTN_ACT_DISABLE,
    "timer": BTN_ACT_TIMER,
    "stopwatch": BTN_ACT_STOPWATCH,
    "sleep_tracking": BTN_ACT_SLEEP,
}
# One select option per distinct payload the device can hold, because a slot
# stores a single value: the repeat count is part of the assignment, not a
# separate setting.
BTN_OPTIONS = (
    ["disabled"]
    + [
        f"{kind}_{count}"
        for kind in (STIM_VIBE, STIM_BEEP, STIM_ZAP)
        for count in BTN_STIM_COUNTS
    ]
    + ["timer", "stopwatch", "sleep_tracking"]
)


def btn_action_bytes(option: str) -> bytes:
    """Encode a select option as an assignment payload."""
    if option in _BTN_FIXED:
        return _BTN_FIXED[option]
    kind, _, count = option.rpartition("_")
    return btn_action_stim(kind, int(count))


def parse_btn_action(action: bytes) -> str:
    """Map an assignment payload back to a select option, or "" when unknown."""
    for option, payload in _BTN_FIXED.items():
        if action == payload:
            return option
    for kind in (STIM_VIBE, STIM_BEEP, STIM_ZAP):
        reference = btn_action_stim(kind)
        if len(action) != len(reference) or action[0] != reference[0]:
            continue
        # Byte 1 is 0x40 | count; bit 6 marks the payload as a button assignment.
        count = action[1] & 0x3F
        return f"{kind}_{count}" if count in BTN_STIM_COUNTS else ""
    return ""


def btn_action_stim(kind: str, count: int = 1) -> bytes:
    """Stimulus button action. First byte is 0x40|count (bit6 = 'assigned to button')."""
    head = 0x40 | max(1, min(127, count))
    if kind == STIM_ZAP:
        return bytes([0x03, head, 0x32])
    if kind == STIM_BEEP:
        return bytes([0x02, head, 0x0C, 0x50, 0x16, 0x16])
    return bytes([0x01, head, 0x0C, 0x64, 0x16, 0x16])  # vibe
