# Cycling-radar BLE protocol notes (`6a4e3200` family)

Verified on a RearVue 820 connected to a Pixel 10 Pro XL running Android 16. Most of this almost certainly applies to sibling radar devices in this family (e.g. RTL515, RTL516) but the V2 enabling sequence has only been tested on the 820.

## Contents

1. [Scope and conventions](#scope-and-conventions)
2. [Advertisement](#advertisement)
3. [GATT services and characteristics](#gatt-services-and-characteristics)
4. [V1 stream: characteristic `6a4e3203`](#v1-stream-characteristic-6a4e3203)
5. [V2 stream: characteristic `6a4e3204`](#v2-stream-characteristic-6a4e3204)
6. [Enabling V2: pairing and pre-handshake sequence](#enabling-v2-pairing-and-pre-handshake-sequence-informally-the-v2-unlock)
7. [Front-camera light: handshake and mode control](#front-camera-light-handshake-and-mode-control)
8. [Rear-radar tail-light: mode control](#rear-radar-tail-light-mode-control)
9. [Subscribing `6a4e3203` early pins V1](#subscribing-6a4e3203-early-pins-v1)
10. [Battery](#battery)
11. [Capture log format](#capture-log-format)
12. [Open questions](#open-questions)

## Scope and conventions

- All multi-byte integers are little-endian unless noted.
- Bit 0 = LSB.
- Hex shown with `0x` prefix. Characteristic UUIDs are abbreviated by their first 4 hex digits — e.g. `6a4e3203` = `6a4e3203-667b-11e3-949a-0800200c9a66`.
- "V1" and "V2" are our terms for the legacy and modern radar streams; the manufacturer does not publish names for them.

## Advertisement

- The advert carries the Bluetooth SIG "Member Service" UUID `0xfe1f` (Garmin Ltd; 128-bit form `0000fe1f-0000-1000-8000-00805f9b34fb`). Filtering on `0xfe1f` is a reliable pan-family scan filter.
- RearVue 820 also advertises service UUID `6a4e3200` (the radar service). The Vue (camera) does **not** advertise any `6a4e2xxx` or `abd2xxxx` services, only `0xfe1f`. Consequence: a passive `ScanFilter` built around `6a4e2800` or `6a4e2f00` misses the Vue; use `0xfe1f` to catch both.
- Observed local names: `RearVue8` (clean), `VUE-NNNNN` (padded with trailing null bytes in the advert payload; the readable prefix is the serial).

## GATT services and characteristics

Shorthand format = first 4 hex digits, extended to `6a4e????-667b-11e3-949a-0800200c9a66`.

| Service | Name | Device scope |
|---------|------|--------------|
| `6a4e2800` | Config / "AMV" control | observed on 820 and Vue |
| `6a4e2f00` | Control (indicate + write) | observed on 820 and Vue |
| `6a4e3200` | Radar | 820 (other radar devices untested) |
| `0000180f` | Standard Battery Service | observed on 820 and Vue |
| `00001800`, `00001801` | GAP / GATT | all BLE devices |

Under `6a4e3200`:

| Characteristic | Properties | Purpose |
|----------------|-----------|---------|
| `6a4e3203` | NOTIFY | V1 stream: heartbeats, threat packets, sector amplitude |
| `6a4e3204` | NOTIFY | V2 stream: per-target structs, emitted only after the connection sequence below |

Under `6a4e2f00`:

| Characteristic | Properties | Purpose |
|----------------|-----------|---------|
| `6a4e2f11` | INDICATE, WRITE | Control indicate; also the mode/config write channel for the front-camera light and rear-radar tail light |
| `6a4e2f12` | INDICATE | Secondary indicate |
| `6a4e2f14` | NOTIFY | Secondary notify; carries front-camera and rear-radar tail-light mode-state |

Under `6a4e2800`:

| Characteristic | Properties | Purpose |
|----------------|-----------|---------|
| `6a4e2811` | NOTIFY | AMV RX (rear-radar replies) |
| `6a4e2821` | WRITE | AMV TX (rear-radar commands) |
| `6a4e2810` | NOTIFY | AMV RX (front-camera replies) |
| `6a4e2820` | WRITE | AMV TX (front-camera commands) |

Under `0000180f`:

| Characteristic | Properties | Purpose |
|----------------|-----------|---------|
| `00002a19` | READ, NOTIFY | Battery level (uint8 %) |

## V1 stream: characteristic `6a4e3203`

The legacy stream is unencrypted and emits as soon as you subscribe its CCCD, no pairing required. Three packet shapes coexist.

### V1 heartbeat (1 byte)

`[seq]` where `seq = (counter << 4) | 0x02`.

The low nibble is always `0x2` (a type tag). The high nibble is a 4-bit sequence counter that wraps `0x02, 0x12, 0x22, ..., 0xf2, 0x02, ...`. Observed rate roughly 7 Hz at rest.

### V1 threat packet (`1 + 3N` bytes)

Detection rule: `len >= 4`, `(len - 1) % 3 == 0`, `payload[0] & 0x0F == 0x02`.

```
byte 0:              seq/fragment byte (same nibble rule as heartbeat)
byte 1 + 3*i:        vehicle id (uint8)
byte 2 + 3*i:        distance (uint8, metres)
byte 3 + 3*i:        flag or state byte (uint8)
```

Where `i = 0 .. N - 1` and `N = (len - 1) / 3`. Up to 6 targets per packet (`len = 19`).

Vehicle id rules:
- Bit 7 (`0x80`) is a "vehicle present" flag; ignore any record with `vid < 0x80`.
- `vid == 0x00` is a no-op placeholder; skip.
- `vid == 0xFD` is a header / status marker that can appear as the first triplet with `dist = 0, flag = 0`; skip.
- `vid == 0xFF` is a "far / uncertain" sentinel on the distance byte; skip the triplet if `distance == 0xFF`.
- The actual track id is `vid & 0x7F`.

The flag byte is **not** a velocity in m/s. Across 28,690 valid vehicle triplets sampled from real commutes it only ever takes two values: `0x00` (96.87%) and `0x01` (3.13%). A `0x01` correlates weakly and inversely with "approaching": it fires on roughly 0.2% of transitions from farther to nearer and 14.6% of transitions from nearer to farther. Its semantics are not yet pinned down. Prior public writeups that describe this byte as "approach speed in m/s, multiply by 3.6 for km/h" are wrong; real velocity is carried by the V2 stream instead.

Fragmentation: the harbour-tacho source documents a fragmentation rule where, if `seq(N+1) == seq(N) + 2`, the continuation packet's vehicles are prepended with the previous packet's vehicles. We have never observed this in practice on the 820. A scan over 26,472 V1 threat packets across nine captures found zero continuation pairs; every seq byte had low nibble `0x2` and no packet's seq was exactly the previous seq plus two. The Kotlin reference decoder is stateful and ages tracks out after roughly 2 seconds, so it reconstructs the same vehicle set without merging fragments at all; the Python inspection CLI does flag a packet whose seq is exactly the previous seq plus two and prepends the earlier vehicles (marked `[+frag]`), but on the 820 that branch never fires. If someone captures a fragmented stream on a different model, please open an issue.

### V1 sector amplitude packet (6 bytes)

Detection rule: `len == 6` and `payload[0] == 0x06`.

```
byte 0:  0x06              type tag
byte 1:  mode/channel | sector    bits 3..2 = sector index (4 values); bit 7 toggles between two values (mode or channel, meaning unconfirmed)
byte 2:  0x05              constant in observed traffic; semantics unverified
byte 3:  0x00              constant in observed traffic; semantics unverified
byte 4:  0x00              constant in observed traffic; semantics unverified
byte 5:  amplitude (uint8)
```

`byte[1]` cycles through eight values `0x30, 0x34, 0x38, 0x3c, 0xb0, 0xb4, 0xb8, 0xbc`. The pattern is consistent with 4 sectors times 2 modes or channels, but the exact meaning of the bit-7 flip has not been confirmed against a controlled test setup. `byte[5]` is the raw amplitude. These are low-level radar diagnostic packets; reconstructing a meaningful lateral position from them needs calibration against annotated overtakes.

## V2 stream: characteristic `6a4e3204`

Richer, per-target data. Each notification is `[2-byte header] + N * [9-byte target]`.

### V2 header (2 bytes)

Little-endian uint16.

| Bit | Meaning |
|-----|---------|
| 0 (`0x0001`) | Status / ack frame: no targets follow, skip the body. |
| 2 (`0x0004`) | Device-status frame: no targets follow. Body carries device telemetry; see "Device-status body" below. |
| other bits | When none of the above are set, body contains N targets. |

A payload of exactly 2 bytes with no target body is a "heartbeat" and is emitted whenever the device has no targets to report. Indoor captures consist almost entirely of these.

### V2 target struct (9 bytes)

| Offset | Field | Decode |
|--------|-------|--------|
| 0 | `targetId` | uint8 radar-assigned track id |
| 1 | `targetClass` | enum (observed values, project-native names): `LARGE=36`, `MODERATE=23`, `MODERATE_ALT=26`, `FAINT=16`, `FAINT_ALT=13`, `UNKNOWN=4`. Higher numeric value = larger / more confident return signature. |
| 2..4 | `rangeY` + `rangeX` | 24-bit little-endian packed; see decoding below |
| 5 | `lengthMeters` | uint8, multiply by 0.25 for metres (class-template, not a measurement) |
| 6 | `widthMeters` | uint8, multiply by 0.25 for metres (class-template, not a measurement) |
| 7 | `speedY` | int8, multiply by 0.5 for m/s (longitudinal closing speed) |
| 8 | `speedX` | int8, multiply by 0.5 for m/s (lateral). The constant `0x80` (-128 → -64 m/s) is the firmware's "no lateral velocity available" sentinel; treat values at the sentinel as unknown. |

**Decoding `rangeY` and `rangeX`.** Bytes [2..4] form a 24-bit little-endian word that packs two signed fields:

- **bits 0..10** = `rangeXBits`, an 11-bit two's-complement signed value.
- **bits 11..23** = `rangeYBits`, a 13-bit two's-complement signed value.

After sign-extension, multiply each by 0.1 m. Pseudocode:

```
packed     = byte[2] | (byte[3] << 8) | (byte[4] << 16)        # little-endian 24-bit
rangeXBits = packed & 0x07FF                                    # 11-bit
if rangeXBits & 0x0400: rangeXBits -= 0x0800                    # sign-extend (11-bit)
rangeYBits = (packed >> 11) & 0x1FFF                            # 13-bit
if rangeYBits & 0x1000: rangeYBits -= 0x2000                    # sign-extend (13-bit)
rangeX_m   = rangeXBits * 0.1                                   # ±204.7 m theoretical
rangeY_m   = rangeYBits * 0.1                                   # ±409.5 m theoretical, ~220 m in practice
```

**Sign convention for `rangeY` (rear-radar coordinate system).**
- `rangeY > 0` → target is BEHIND the rider. This is the dominant case (~99% of frames in commute captures).
- `rangeY < 0` → target is AHEAD of the rider, i.e. has just overtaken. Rare (~0.7% of frames). The radar's beam is rear-facing so coverage of "ahead" is incidental and usually short-lived.

`rangeX > 0` is to the rider's right.

**speedY sign convention.** byte [7] trends increasingly negative as a target approaches and increasingly positive as it falls behind, giving `0.5 m/s` quantised closing speed. Interpreted with this sign, `byte[7] = -7` means the target is closing at 3.5 m/s. byte [7] is the official approach-speed signal; deriving speed from frame-to-frame `rangeY` deltas is unnecessary and produces ~2.7 m/s RMS jitter against this baseline.

**Validation against V1 ground truth.** Decoding 22,804 V2 target frames across three independent commute captures with the formula above produces:

- Median `|rangeY|` = 30 m, max 220 m. Matches V1's `(median 29 m, max 211 m)` distance distribution within statistical noise.
- Frame-to-frame median `Δ|rangeY|` = 0.20 m, p98 = 1.70 m. V1 baseline is p98 ≤ 2 m. Trajectories are smooth.
- 96% of long+far track segments (≥3 s, ≥10 frames, max distance ≥30 m) satisfy V1's smoothness criterion.
- The known reference case "tid 42 = sustained 5-10 m tailgater" decodes as 9.8-51.1 m behind across 246 observations (i.e. the close end matches the user's eyeball estimate; the spread reflects multiple cars sharing the same tid over several minutes, a well-known firmware behaviour).

Bytes [3] bits 3..7 are NOT a separate field; they are the upper 5 bits of the packed 24-bit word and decode as part of `rangeY`. Treating them as a "reserved chirp counter" was an early hypothesis that this document's previous revision propagated incorrectly.

**History: prior incorrect decodings.** An earlier revision of this document described `byte[2..4]` as `rangeYLow + rangeYZone (3-bit) + rangeX (separate int8)`, with `rangeY = zone * 25.6 + byte[2] * 0.1`. That zone-counter interpretation places close tailgaters at ~25-30 m forward and produces phantom 200 m "ghost" frames. It is wrong, retracted as of this revision. The actual encoding on the 820 firmware is little-endian; we believe earlier reference implementations assumed big-endian.

Reference Python and Kotlin decoders are in `python/decode_3204.py` and `kotlin/RadarV2Decoder.kt`.

### Device-status body (header bit `0x0004`)

When the header has bit 2 set, the body is a 3- or 4-byte payload. The on-the-wire shape observed from a RearVue 820 is:

| Total payload | Shape |
|---------------|-------|
| 5 bytes | header(2) + 3-byte body — sparse frames |
| 6 bytes | header(2) + 4-byte body — full status frames, dominant during active riding |

For 6-byte payloads, the last byte (`payload[5]` zero-indexed) carries the rider's own bike speed:

```
bikeSpeed_ms  = payload[5] * 0.25      # m/s, scaled by 0.25 m/s per LSB
bikeSpeed_kmh = bikeSpeed_ms * 3.6     # equivalently, * 0.9 km/h per LSB
```

Stationary floor: raw 2 (~0.5 m/s, 1.8 km/h) — doppler noise floor above true zero; raw 0 not observed.

Observed ceiling: raw 63 (~15.75 m/s, 56.7 km/h), seen as a single-frame peak at the top of two independent downhill segments across the capture corpus. Across 6,499 device-status frames spanning multiple commute sessions, no value above 63 has been observed and bits 6-7 are never set. This is consistent both with a plain uint8 field whose dynamic range simply isn't reached by everyday cycling and with a 6-bit field with reserved upper bits; current data does not distinguish. Decoders should accept the full `[0, 255]` range to remain robust if larger values appear in the future, but should not assume the field is confirmed-uint8. An earlier revision of this document claimed a raw-50 firmware ceiling; that claim is retracted.

5-byte sparse frames carry the sub-header bytes without the trailing speed byte. Decoders should leave their cached bike-speed unchanged on a sparse frame.

## Enabling V2: pairing and pre-handshake sequence (informally, "the V2 unlock")

On the RearVue 820 the `6a4e3204` characteristic will accept a CCCD subscribe without complaint, but the device stays in V1 mode and nothing is ever notified on it. To enable V2 you need two things: a LESC bond, and a specific pre-handshake read-and-subscribe on the standard Battery Service before opening the AMV session.

### LESC bonding

The RearVue 820 requires **LE Secure Connections** (AuthReq flag `SC = 1`). It will reject any pair request that proposes Legacy pairing with `SMP_PAIR_NOT_SUPPORT`.

The manufacturer's official Android app pairs successfully, as does Android's own Settings pairing flow (Settings -> Connected devices -> Pair new device); both produce an `SC` bond. iOS presumably does too, since the manufacturer's iOS app exists and pairs without user workarounds, but it is untested here. What breaks is `BluetoothDevice.createBond()` called programmatically by a third-party app, on at least **Pixel 10 Pro XL running Android 16**: the stack initiates pairing without the `SC` flag, the 820 rejects, and the app sees `SMP_PAIR_NOT_SUPPORT sec_level:0x0`. A diagnostic log line that identifies this case is `btif_dm_get_smp_config: SMP pairing options not found in stack configuration`, which reflects that `bt_stack.conf` is absent from the Android 16 image. There is no public API to set `AuthReq.SC` from userspace.

**Recommended approach for app developers**: do not call `createBond()` from your own code. Ask the user to pair once via either:

1. Settings -> Connected devices -> Pair new device -> tap the radar while it is in pair mode (long-press its button until the LED blinks red). Power-cycle the radar after pairing to exit pair mode.
2. The manufacturer's official Android app's own pair flow.

Either path produces a phone-side bond with `PairingAlgorithm::SC(0x3)`, `le_enc_key_size:16`, `le_encrypted:T` (visible in `adb shell dumpsys bluetooth_manager | grep -A5 <mac>`), which is functionally identical for reusing. Your own service then connects without trying to bond; the stack reuses the phone-side LTK transparently.

### Pre-handshake Battery Service step

Even with a LESC bond and the AMV handshake completed successfully, the 820 will stay in V1 mode unless the central performs a specific read-and-subscribe on the standard Battery Service **before** opening the AMV session. The manufacturer's app performs the same read and subscribe. Nothing in the exchange is secret or key-derived - `0x2a19` is a Bluetooth SIG standard characteristic that any central can read - so the most likely explanation is a firmware capability check: a central that exercises the standard Battery Service is treated as supporting the modern stream, and one that does not falls back to the legacy stream.

The full verified sequence, post-connect, is:

1. `requestMtu(247)`. The device negotiates down to 100; either MTU works.
2. Discover services. Subscribe CCCDs on:
   - `6a4e2f11` (control indicate)
   - `6a4e2811` (AMV RX)
   - Defer CCCDs on `6a4e3203`, `6a4e3204`, `6a4e2f12`, `6a4e2f14` for now. The official app never writes the `6a4e3203` CCCD during a V2 session, and subscribing `6a4e3203` at this point pins the radar into V1 mode - see [Subscribing `6a4e3203` early pins V1](#subscribing-6a4e3203-early-pins-v1).
3. **The Battery Service step**: on the standard Battery Service:
   - `READ 0x2a19` (Battery Level), one byte, returns battery percent.
   - Subscribe the CCCD of `0x2a19` for NOTIFY.
4. Open the AMV session on `6a4e2821` with replies on `6a4e2811`. The session is a fixed sequence of six write / indicate exchanges. Each written payload is constant apart from a single leading byte taken from one of the device's own earlier replies - two distinct such values across the six frames: one for the first frame, one shared by the middle four, and that second value incremented by one for the final frame. Nothing is secret, key-derived, or computed over a device challenge: a central that emits the same byte sequence directly is accepted. `samples/3204-sample.log` records the device side of a complete exchange on `6a4e2811`, which is enough to align an independent implementation's replies against what the device expects. The central-side payloads are not reproduced here.
5. Post-handshake: `READ 0x2a24` (model), subscribe the CCCD of `6a4e3204`, `READ 0x2a26` (firmware), optional `READ 0x2a25` (serial).
6. Within roughly 100 ms of the step-5 `6a4e3204` CCCD enable, V2 notifications start flowing.

Every fresh-connection attempt that skips step 3 stays in V1 mode; every one that includes it moves to V2. Validated across six consecutive strategy cycles in a single session.

### What was **not** the trigger

For the benefit of anyone else going down this road:

- Subscribing `6a4e2f12` or `6a4e2f14` CCCDs pre-handshake. No effect.
- The `6a4e2f11` indicate writes (`20 04 01 10 04`) that follow the handshake. These are post-handshake housekeeping, and do not switch the stream.
- Running the AMV handshake on its own. Necessary but insufficient.
- Running the handshake three times in a row. The "cumulative" pattern in some earlier strategies was a red herring caused by the firmware briefly retaining V2 mode across reconnects.

### Minimal-subset work still to do

The sequence above is the full one. It has not yet been bisected to prove the minimum. In particular it is not known whether step 3's `READ` alone suffices without the CCCD subscribe, or vice versa, or whether a single "touch" of the Battery Service is enough regardless of direction. A capture-and-bisect session on a controlled bench setup would pin this down.

## Front-camera light: handshake and mode control

Verified on a front-camera light running firmware 5.80, paired to a Pixel 10 Pro XL running Android 16. The camera advertises only `0xfe1f` (no `6a4e2xxx` services in the advert; see [Advertisement](#advertisement)). It hosts the same `6a4e2800` and `6a4e2f00` services as the radar but uses a different AMV characteristic pair, and its handshake is shorter.

### AMV characteristic pair

The camera's AMV TX/RX pair is offset by one from the rear radar's:

| Role | Camera | Rear radar |
|------|--------|-----------|
| AMV TX (write) | `6a4e2820` | `6a4e2821` |
| AMV RX (notify) | `6a4e2810` | `6a4e2811` |

Mixing the two pairs causes a silent handshake failure: the device accepts writes but never responds. Centrals that target both device classes must select the right pair per device.

### Pre-handshake setup

Same as the rear radar (see [Pre-handshake Battery Service step](#pre-handshake-battery-service-step)):

1. Subscribe CCCDs on `6a4e2f11` (control indicate) and `6a4e2810` (AMV RX, camera).
2. `READ 0x2a19` on the standard Battery Service, then subscribe its CCCD.

Subscribing `6a4e2f12`, `6a4e2f14`, `6a4e3203`, or `6a4e3204` is unnecessary and not done by the official app. (`6a4e3204` does not exist on the camera; the radar service `6a4e3200` is rear-radar only.)

### AMV enabling sequence

The first phase mirrors the rear radar:

1. AMV cmd `04` (capability/version probe).
2. AMV enumerate steps `00`, `01`, `02`, `03`, `04`. Each reply carries two prefix bytes captured for use in later frames.

The camera adds a third phase not present on the radar: an AMV opcode `0x18` sub-mode toggle. Three frames are written to `6a4e2820` (AMV TX), with replies on `6a4e2810` (AMV RX). Frame format is 13 bytes:

```
00 SS 00 00 00 00 00 00 41 4d 56 18 PP
```

| Frame | `SS` | `PP` |
|-------|------|------|
| 1 | `00` | `02` |
| 2 | `02` | `82` |
| 3 | `00` | `02` |

`41 4d 56` (ASCII `AMV`) at bytes 8-10 is the AMV signature, and byte 11 is the opcode (`0x18`). Each reply also carries the signature at bytes 8-10 and `0x18` at byte 11; match on those positions only. The trailing status bytes vary across frames and across sessions and should not be pinned.

After the third reply, the front-camera handshake is complete. Mode-set writes (next section) succeed without further setup.

### Differences from the rear-radar handshake

The rear radar's post-handshake exchange does **not** apply on the camera:

- **AMV cmd `0x16`** does return a reply, but it is 13 bytes (`00 01 00 00 00 00 00 00 41 4d 56 16 00 01`) and carries no capability byte at offset 13. The rear radar's capability exchange, which reuses a leading byte from that reply, does not apply to the camera.
- **Device-ID push frame.** No notification of length > 20 bytes with `byte[0] >= 0x80` arrives. Waiting for one times out. The handshake completes at the third `0x18` reply.

### Mode control: `6a4e2f11` / `6a4e2f14`

Mode-set is a 3-byte write to `6a4e2f11` (WRITE_TYPE_DEFAULT):

```
07 00 NN
```

`NN` is the 1-based mode ordinal. Six modes are defined:

| Mode | `NN` |
|------|------|
| High | `01` |
| Medium | `02` |
| Low | `03` |
| Night flash | `04` |
| Day flash | `05` |
| Off | `06` |

The write is acknowledged on `6a4e2f11` as an indicate of `20 07 01 [next]`.

Mode state is published on `6a4e2f14` as 3-byte notifications:

```
01 [mode_zero_based] [flags]
```

- `byte[0] = 0x01` is the mode-state record tag. Filter on `len == 3 && byte[0] == 0x01`; `6a4e2f14` carries other notification types during connection setup.
- `byte[1]` is the zero-based mode index (`0` = High through `5` = Off).
- `byte[2]` is a small flags byte. Observed values: `0x10` (High), `0x11` (Medium), `0x12` (Low), `0x13` (Night flash), `0x14` (Day flash), `0x1F` (Off). The low nibble tracks the mode index; bit 4 is consistently set; `0x1F` for Off looks like a distinct sentinel rather than a continuation of the pattern.

## Rear-radar tail-light: mode control

Verified on the same RearVue 820 (Pixel 10 Pro XL, Android 16). The radar unit drives an integrated tail light whose mode can be set over the control service. Unlike the front camera, which is a separate device, the tail light shares the radar's own GATT link; there is no second connection. Each command below was confirmed by correlating it to the observed light behaviour and the resulting mode-state notification.

### Service and characteristics

Mode control reuses the same `6a4e2f00` control characteristics as the front camera:

| Characteristic | Dir | Purpose |
|----------------|-----|---------|
| `6a4e2f11` | WRITE (WRITE_TYPE_DEFAULT) | command channel: mode-set, cycle-list config, slot-select |
| `6a4e2f14` | NOTIFY | mode-state, plus a constant config blob and a short counter |

The mode/config protocol is on `6a4e2f00`, not on the `6a4e2800` AMV channel; AMV is only the enabling handshake.

### Mode types

Each mode has a stable 1-byte **type** code, independent of the unit's button-cycle configuration:

| Type | Mode |
|------|------|
| `0x11` | Solid (brightest) |
| `0x12` | Peloton (solid, dimmer) |
| `0x13` | Day flash |
| `0x14` | Night flash |
| `0x1F` | Off |
| `0x01` | Custom (user-defined pattern) |

The valid set is `{0x01, 0x11, 0x12, 0x13, 0x14, 0x1F}`. `0x10` and `0x15`-`0x1E` are no-ops: the light does not change.

> **Day/night byte assignment differs from the front camera.** On this radar `0x13` = day flash and `0x14` = night flash. The front camera's `6a4e2f14` flags byte uses the opposite pairing (`0x13` = night flash, `0x14` = day flash; see [Mode control: `6a4e2f11` / `6a4e2f14`](#mode-control-6a4e2f11--6a4e2f14)). The type-byte space is shared across the family but the day/night labels are not interchangeable between devices - check per device.

### Set current mode by type - `06 09 01 TT`

```
06 09 01 TT      TT = type byte from the table above
```

Sets the light to type `TT` immediately. This is an **output override**: it changes what the light does without moving the unit's selected cycle slot, so it never disturbs the rider's button-cycle configuration in the official app.

Slot-independence was confirmed directly: `06 09 01 12` (Peloton) selected Peloton even though Peloton was not present in the test unit's configured cycle list. Any mode type can therefore be selected regardless of which modes the user has exposed on the device button. Example: `06 09 01 13` → day flash, `06 09 01 14` → night flash.

> **Read-back caveat.** A type-override set this way does **not** update the `6a4e2f14` mode-state notification: during a type sweep the notification stayed at the previously selected slot's value while the light visibly changed. The notification reports the selected *slot*, not the current output. So `6a4e2f14` can detect a rider's physical button press (which does move the slot) but cannot confirm that your own `06 09 01 TT` write landed.

### Configure the button-cycle list - `06 09 05 ...`

The ordered list of types reachable by the unit's physical button is read and written with opcode `06 09 05`:

```
06 09 05 [T0 T1 T2 ...] ff ff      write: set the cycle list (ff-padded)
06 09 05                           query: unit replies with the current list
```

Observed writes, confirmed against edits made in the official app:

| Bytes | Cycle list |
|-------|-----------|
| `06 09 05 14 11 1f 13 ff ff` | night flash, solid, off, day flash |
| `06 09 05 14 11 13 ff ff ff` | night flash, solid, day flash |
| `06 09 05 13 14 11 ff ff ff` | day flash, night flash, solid |
| `06 09 05 13 14 11 01 ff ff` | day flash, night flash, solid, custom |

A central that wants a mode reachable from the physical button (rather than only overriding the output) writes the desired list here. For a simple day/night automation the set-by-type command above is preferable: it needs no knowledge of, and makes no change to, the user's list.

### Select a cycle slot by ordinal - `07 00 NN`

```
07 00 NN         NN = 1-based slot ordinal
```

Selects whichever mode currently occupies slot `NN`. This is the same `07 00 NN` opcode the front camera uses for its mode ordinals. Because the result depends entirely on the current cycle-list contents, it is fragile for automation; set-by-type is the robust alternative.

### Mode-state notification - `6a4e2f14`

The unit publishes its selected-slot state as a 4-byte notification:

```
01 [slot] ff [type]
```

- `byte[0] = 0x01` is the mode-state record tag.
- `byte[1]` is the selected cycle-slot ordinal (0-based).
- `byte[2] = 0xff` is constant.
- `byte[3]` is the type byte of the mode in that slot.

Filter on `len == 4 && byte[0] == 0x01 && byte[2] == 0xff`. `6a4e2f14` also carries an 11-byte config blob (`00 60 00 00 00 00 12 00 00 00 00`, constant in observed traffic) and 2-byte counter/ack records (`02 NN`), both of which fail this filter. This is the 4-byte rear-radar shape; the front camera's `6a4e2f14` mode-state is a different 3-byte record (`01 [mode] [flags]`).

### Custom mode - `03 29 ...` (partial)

Opcode `03 29` defines and queries the user-defined custom pattern (type `0x01`). A custom pattern of solid 100%, then 50% for 1 s, 0% for 10 ms, 70% for 600 ms was observed as `03 29 ff 32 7f 64 00 0a b2 3c 00 ...`, where brightness appears as a direct percentage byte (`0x64` = 100%, `0x32` = 50%). The full per-segment timing encoding and the trailing checksum are not decoded; see [Open questions](#open-questions).

### Subscribing `6a4e2f14` does not pin V1

The [V2 enabling notes](#enabling-v2-pairing-and-pre-handshake-sequence-informally-the-v2-unlock) warn that subscribing the `6a4e3203` CCCD can hold the radar in V1 mode. Subscribing `6a4e2f14` does **not** have this effect: enabling its CCCD *after* the V2 handshake, to read tail-light mode-state, left the V2 stream flowing normally. `6a4e3203` is the characteristic to avoid; `6a4e2f14` is safe.

## Subscribing `6a4e3203` early pins V1

Which stream is enabled depends on the pre-handshake state. RearVue 820, fw 6.70, one trial per row:

| `6a4e3203` CCCD written | `6a4e3203` | `6a4e3204` |
|---|---|---|
| Before the [Battery Service step](#pre-handshake-battery-service-step) + AMV session | V1 heartbeats | never emits |
| After V2 is successfully enabled | silent | unaffected (frame cadence unchanged) |
| V2 never enabled | silent | silent |

Written early, the handshake still reports success and the CCCD reads back `0x0001`: the write is accepted, and the radar settles into V1.

The pin outlives the connection. Later connections that never touched the CCCD also got no V2 - handshake complete, `6a4e3204` silent - across every reconnect until the test was stopped. A power-cycle restored V2. Whether the pin expires on its own was not tested; [What was not the trigger](#what-was-not-the-trigger) records the mirror case, where V2 mode is briefly retained across reconnects.

So V1 is not a dead path on 6.70, but no tested configuration produced both streams: selecting V1 costs V2, and keeps costing it across reconnects. The failure is silent - link up, handshake OK, zero targets.

Untested: the CCCD written *between* the Battery Service step and the AMV session; and whether the AMV session alone, without it, starts V1.

## Battery

Standard GATT Battery Service works on both the radar and the camera.

- Service: `0000180f`.
- Characteristic: `00002a19`, read returns a single uint8 percent.

On the camera this is the easiest way to surface a battery reading in third-party apps: connect, read, disconnect. No bonding required for the read on either the RearVue 820 or the Vue tested here.

## Capture log format

The reference decoders in this repo read a simple line-based format. One line per BLE notification:

```
# comments start with hash
<unix_ms> <char_tail_4hex> <hex_bytes_no_spaces>
1744681038109 3203 02
1744681038254 3203 1281304c8220a8...
1744681038306 3204 0200
```

- `unix_ms`: wall-clock millisecond timestamp of the notification arrival.
- `char_tail_4hex`: last 4 hex digits of the characteristic UUID (e.g. `3203`, `3204`, `2811`).
- `hex_bytes_no_spaces`: the raw notification payload.

Both `python/decode_3203.py` and `python/decode_3204.py` take one or more such files on the command line and emit a line per packet / frame.

You can capture this format with any BLE central that can log raw GATT notifications. On Android, enable Bluetooth HCI snoop log (Developer options -> "Enable Bluetooth HCI snoop log"), then post-process the btsnoop file with Wireshark's `btatt` dissector. On Linux, Wireshark can capture directly from a BlueZ controller.

## Open questions

Issues / PRs welcome on any of these.

1. Does the V2 enabling sequence apply to older radar units in this family (e.g. RTL515, RTL516) or to other current-generation models? Only the RearVue 820 has been tested here.
2. What exactly is the `0x00` / `0x01` flag in the V1 threat triplet? It is neither speed nor approach direction.
3. What is the minimal subset of the Battery Service step that enables V2? One read? One CCCD? Either?
4. Are any of the `6a4e2800` service's other writable characteristics used during normal official-app operation?
5. Does the 820 emit anything richer than sector amplitude in V1 mode that we have not decoded? `byte[2..4]` of the sector packet are constant `0x05 0x00 0x00` across thousands of observed packets; whether byte 2's `0x05` is a reserved value, a fixed channel id, or a firmware-version field that simply has not varied is unverified.
6. Is there an iOS equivalent of Android's LESC-pairing quirk, or does iOS always get this right via its standard pair flow?
7. Does the front camera's AMV `0x18` sub-mode toggle apply to other devices in this family, or only the front-camera light?
8. What do the trailing status bytes in the `0x18` toggle replies represent? They vary across frames and across sessions.
9. What does `[next]` in the `6a4e2f11` mode-set ack indicate (`20 07 01 [next]`) carry — a firmware-suggested next mode, an echo of the requested mode, or something else?
10. What is the full segment-timing encoding (and trailing checksum) of the rear-radar tail-light custom mode (`03 29`)? Only the brightness percentage bytes are decoded.
11. Does the rear-radar set-by-type command (`06 09 01 TT`) work on other devices in this family, or is the tail-light type table specific to the 820?
12. What does the rear-radar `6a4e2f14` 11-byte config blob (`00 60 00 00 00 00 12 00 00 00 00`) encode? It is constant in observed traffic.
