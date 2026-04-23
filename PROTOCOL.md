# Garmin Varia BLE Protocol

Verified on a Garmin Varia RearVue 820 connected to a Pixel 10 Pro XL running Android 16. Most of this almost certainly applies to sibling devices (RTL515, RTL516, Vue 870) but the V2 unlock sequence has only been tested on the 820.

Last updated: 2026-04-18.

## Contents

1. [Scope and conventions](#scope-and-conventions)
2. [Advertisement](#advertisement)
3. [GATT services and characteristics](#gatt-services-and-characteristics)
4. [V1 stream: characteristic `6a4e3203`](#v1-stream-characteristic-6a4e3203)
5. [V2 stream: characteristic `6a4e3204`](#v2-stream-characteristic-6a4e3204)
6. [Unlocking V2: pairing and pre-handshake dance](#unlocking-v2-pairing-and-pre-handshake-dance)
7. [Battery](#battery)
8. [Capture log format](#capture-log-format)
9. [Open questions](#open-questions)

## Scope and conventions

- All multi-byte integers are little-endian unless noted.
- Bit 0 = LSB.
- Hex shown with `0x` prefix. Characteristic UUIDs written in the Garmin shorthand `6a4e3203` = `6a4e3203-667b-11e3-949a-0800200c9a66`.
- "V1" and "V2" are our terms for the legacy and modern radar streams; Garmin does not publish names for them.

## Advertisement

- The advert carries the Bluetooth SIG "Member Service" UUID `0xfe1f` (Garmin Ltd; 128-bit form `0000fe1f-0000-1000-8000-00805f9b34fb`). Filtering on `0xfe1f` is a reliable pan-Varia scan filter.
- RearVue 820 also advertises service UUID `6a4e3200` (the radar service). The Varia Vue (camera) does **not** advertise any `6a4e2xxx` or `abd2xxxx` services, only `0xfe1f`. Consequence: a passive `ScanFilter` built around `6a4e2800` or `6a4e2f00` misses the Vue; use `0xfe1f` to catch both.
- Observed local names: `RearVue8` (clean), `VUE-NNNNN` (padded with trailing null bytes in the advert payload; the readable prefix is the serial).

## GATT services and characteristics

Garmin shorthand format = first 4 hex digits, extended to `6a4e????-667b-11e3-949a-0800200c9a66`.

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
| `6a4e3204` | NOTIFY | V2 stream: per-target structs, gated behind the unlock sequence |

Under `6a4e2f00`:

| Characteristic | Properties | Purpose |
|----------------|-----------|---------|
| `6a4e2f11` | INDICATE | Control indicate |
| `6a4e2f12` | INDICATE | Secondary indicate |
| `6a4e2f14` | NOTIFY | Secondary notify |

Under `6a4e2800`:

| Characteristic | Properties | Purpose |
|----------------|-----------|---------|
| `6a4e2811` | NOTIFY | AMV RX (replies from device) |
| `6a4e2821` | WRITE | AMV TX (commands to device) |

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

Fragmentation: the harbour-tacho source documents a fragmentation rule where, if `seq(N+1) == seq(N) + 2`, the continuation packet's vehicles are prepended with the previous packet's vehicles. We have never observed this in practice on the 820. A scan over 26,472 V1 threat packets across nine captures found zero continuation pairs; every seq byte had low nibble `0x2` and no packet's seq was exactly the previous seq plus two. A stateful decoder that ages tracks out after roughly 2 seconds reconstructs the same vehicle set without ever needing to merge fragments. Reference implementations in this repo therefore do not implement the merge rule. If someone captures a fragmented stream on a different model, please open an issue.

### V1 sector amplitude packet (6 bytes)

Detection rule: `len == 6` and `payload[0] == 0x06`.

```
byte 0:  0x06              type tag
byte 1:  mode/channel | sector    bits 3..2 = sector index (4 values); bit 7 toggles between two values (mode or channel, meaning unconfirmed)
byte 2:  unknown (often 0)
byte 3:  unknown (often 0)
byte 4:  unknown (often 0)
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
| 2 (`0x0004`) | Device-status frame: no targets follow, log and skip. |
| other bits | When none of the above are set, body contains N targets. |

A payload of exactly 2 bytes with no target body is a "heartbeat" and is emitted whenever the device has no targets to report. Indoor captures consist almost entirely of these.

### V2 target struct (9 bytes)

| Offset | Field | Decode |
|--------|-------|--------|
| 0 | `targetId` | uint8 radar-assigned track id |
| 1 | `targetClass` | enum (observed values, project-native names): `STRONG=36`, `MEDIUM=23`, `MEDIUM_HOLD=26`, `WEAK=16`, `WEAK_HOLD=13`, `UNCLASSIFIED=4`. Higher numeric value = larger / more confident return signature. |
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

**History: prior incorrect decodings.** An earlier revision of this document described `byte[2..4]` as `rangeYLow + rangeYZone (3-bit) + rangeX (separate int8)`, with `rangeY = zone * 25.6 + byte[2] * 0.1`. That zone-counter interpretation places close tailgaters at ~25-30 m forward (the audit's flagship failure case) and produces phantom 200 m "ghost" frames. It is wrong, retracted as of this revision. The rale/radarble project has the right idea (24-bit packed, two signed fields) but uses big-endian byte order; the actual encoding is little-endian as documented above.

This documentation describes the on-the-wire format of `6a4e3204` notifications observed from a RearVue 820 owned by the author. The byte layout given above is stated in this project's own variable names and is cross-checked against live captures; the pseudocode in this section is original to this project.

Reference Python and Kotlin decoders are in `python/decode_3204.py` and `kotlin/RadarV2Decoder.kt`.

## Unlocking V2: pairing and pre-handshake dance

On the RearVue 820 the `6a4e3204` characteristic will accept a CCCD subscribe without complaint, but the device stays in V1 mode and nothing is ever notified on it. To unlock V2 you need two things: a LESC bond, and a specific pre-handshake read-and-subscribe on the standard Battery Service before opening the AMV session.

### LESC bonding

The RearVue 820 requires **LE Secure Connections** (AuthReq flag `SC = 1`). It will reject any pair request that proposes Legacy pairing with `SMP_PAIR_NOT_SUPPORT`.

The Garmin Varia Mobile Android app handles this correctly (it runs through a privileged path that proposes `SC = 1`). iOS presumably does too, since Varia Mobile on iOS exists and pairs without user workarounds, but it is untested here. Android's stock `BluetoothDevice.createBond()` also handles it correctly when triggered from system UI (Settings -> Connected devices -> Pair new device). It is broken on at least **Pixel 10 Pro XL running Android 16** when triggered programmatically by a third-party app: the stack initiates pairing without the `SC` flag, the 820 rejects, and the app sees `SMP_PAIR_NOT_SUPPORT sec_level:0x0`. A diagnostic log line that identifies this case is `btif_dm_get_smp_config: SMP pairing options not found in stack configuration`, which reflects that `bt_stack.conf` is absent from the Android 16 image. There is no public API to set `AuthReq.SC` from userspace.

**Recommended approach for app developers**: do not call `createBond()` from your own code. Ask the user to pair once via either:

1. Settings -> Connected devices -> Pair new device -> tap the Varia while it is in pair mode (long-press its button until the LED blinks red). Power-cycle the Varia after pairing to exit pair mode.
2. The Garmin Varia Mobile app's own pair flow.

Either path produces a phone-side bond with `PairingAlgorithm::SC(0x3)`, `le_enc_key_size:16`, `le_encrypted:T` (visible in `adb shell dumpsys bluetooth_manager | grep -A5 <mac>`), which is functionally identical for reusing. Your own service then connects without trying to bond; the stack reuses the phone-side LTK transparently.

### Pre-handshake battery dance

Even with a LESC bond and the AMV handshake completed successfully, the 820 will stay in V1 mode unless the central performs a specific read-and-subscribe on the standard Battery Service **before** opening the AMV session. This mimics what the Varia Mobile app does and appears to function as an "authenticated modern central detected" signal.

The full verified sequence, post-connect, is:

1. `requestMtu(247)`. The device negotiates down to 100; either MTU works.
2. Discover services. Subscribe CCCDs on:
   - `6a4e2f11` (control indicate)
   - `6a4e2811` (AMV RX)
   - Defer CCCDs on `6a4e3203`, `6a4e3204`, `6a4e2f12`, `6a4e2f14` for now. Observational note: Varia Mobile never writes the `6a4e3203` CCCD during a V2 session, and subscribing `6a4e3203` early appears to pin some firmware states into V1 mode.
3. **The gate**: on the standard Battery Service:
   - `READ 0x2a19` (Battery Level), one byte, returns battery percent.
   - Subscribe the CCCD of `0x2a19` for NOTIFY.
4. Open the AMV session on `6a4e2821` with replies on `6a4e2811`. The full handshake is a sequence of write / indicate / write / indicate exchanges built on a replayable static payload with two prefix bytes (`pfxEnum`, `pfxCmd`) captured from the device's initial replies. A verbatim replay of the six frames sent by Varia Mobile works every time. The exact payloads run to roughly 150 bytes across six frames and are awkward to reproduce faithfully in prose; a reference btsnoop capture is included under `samples/` so the frames can be inspected in Wireshark's `btatt` dissector and cross-checked against your own replay.
5. Post-handshake: `READ 0x2a24` (model), subscribe the CCCD of `6a4e3204`, `READ 0x2a26` (firmware), optional `READ 0x2a25` (serial).
6. Within roughly 100 ms of the step-5 `6a4e3204` CCCD enable, V2 notifications start flowing.

Every fresh-connection attempt that skips step 3 stays in V1 mode; every one that includes it moves to V2. Validated across six consecutive strategy cycles in a single session.

### What was **not** the gate

For the benefit of anyone else going down this road:

- Subscribing `6a4e2f12` or `6a4e2f14` CCCDs pre-handshake. No effect.
- The `6a4e2f11` indicate writes (`20 04 01 10 04`) that follow the handshake. These are post-handshake housekeeping, not an unlock.
- Running the AMV handshake on its own. Necessary but insufficient.
- Running the handshake three times in a row. The "cumulative" pattern in some earlier strategies was a red herring caused by the firmware briefly retaining V2 mode across reconnects.

### Minimal-subset work still to do

The recipe above is the full Varia Mobile replay. It has not yet been bisected to prove the minimum. In particular it is not known whether step 3's `READ` alone suffices without the CCCD subscribe, or vice versa, or whether a single "touch" of the Battery Service is enough regardless of direction. A motivated capture-and-bisect session on a locked-down device would pin this down.

## Battery

Standard GATT Battery Service works on both the radar and the camera.

- Service: `0000180f`.
- Characteristic: `00002a19`, read returns a single uint8 percent.

On the camera this is the easiest way to surface a battery reading in third-party apps: connect, read, disconnect. No bonding required for the read on either the RearVue 820 or the Varia Vue tested here.

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

1. Does the V2 unlock recipe apply to older Varia radar units (e.g. RTL515, RTL516) or to other current-generation Varia models? Only the RearVue 820 has been tested here.
2. What exactly is the `0x00` / `0x01` flag in the V1 threat triplet? It is neither speed nor approach direction.
3. What is the minimal subset of the Battery Service dance that unlocks V2? One read? One CCCD? Either?
4. Are any of the `6a4e2800` service's other writable characteristics used during normal Varia Mobile operation?
5. Does the 820 emit anything richer than sector amplitude in V1 mode that we have not decoded? `byte[2..4]` of the sector packet look like padding but have not been examined against known-angle targets.
6. Is there an iOS equivalent of Android's LESC-pairing quirk, or does iOS always get this right via its standard pair flow?
