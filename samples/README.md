# samples/

Real captures that you can run the reference decoders against.

## Format

Each file is a line-based capture. See PROTOCOL.md section "Capture log
format" for the full spec. Comments start with `#`; data lines look like:

```
<unix_ms> <char_tail_4hex> <hex_bytes_no_spaces>
```

- `unix_ms`: wall-clock millisecond timestamp of the notification arrival.
- `char_tail_4hex`: last 4 hex digits of the characteristic UUID (e.g. `3203`).
- `hex_bytes_no_spaces`: raw notification payload.

Captures have been scrubbed — any BLE MAC addresses in comment lines are
replaced with `AA:BB:CC:DD:EE:FF`.

## What is here

| File | What | Notes |
|------|------|-------|
| `3203-sample.log` | ~900 notifications from a short RearVue 820 session | Mostly V1 heartbeats with a handful of threat packets. Good for exercising `python/decode_3203.py` and `kotlin/RadarV1Decoder.kt`. |
| `3204-sample.log` | ~2.6k lines from a second RearVue 820 session after the V2 unlock | ~1,200 V2 target frames on `3204` interleaved with V1 heartbeats on `3203`. Runs `python/decode_3204.py` and `kotlin/RadarV2Decoder.kt`. |

Both captures come from post-bond sessions. Reproducing the V2 unlock
from scratch needs the LESC bond + Battery Service pre-handshake
described in PROTOCOL.md §"Unlocking V2"; the V2 sample here is the
output once that unlock has already happened.

## Capturing your own

Two options.

### 1. Android: Bluetooth HCI snoop log

Enable developer options on the phone, turn on "Enable Bluetooth HCI
snoop log", exercise the BLE connection, then pull the btsnoop file via
`adb bugreport` or directly from `/data/misc/bluetooth/logs/`. Decode it
with Wireshark's built-in `btatt` dissector — each ATT Handle Value
Notification corresponds to one line in this repo's format.

### 2. Linux / BlueZ: Wireshark live capture

Wireshark can capture directly from a BlueZ controller. Start a capture
on the `bluetooth0` (or equivalent) interface, connect to the radar via
`bluetoothctl` or your preferred tool, then export the `btatt`
notifications as text.

Either path produces the same information; this repo's line format is
the minimum subset the reference decoders need.

## Btsnoop captures for the V2 unlock handshake

Not included here yet. See PROTOCOL.md §"Unlocking V2" for the sequence
description; a reference btsnoop capture may be added in a future drop
so the AMV handshake frames can be cross-checked in Wireshark.
