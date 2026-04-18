# bike-radar-docs

Reverse-engineered protocol notes and reference decoders for Garmin Varia radar devices over BLE.

This repo is a knowledge drop, not a finished product. It documents what the wire looks like and gives minimal, standalone decoders that anyone can run against a captured log. A more complete Android client exists and will be released here in stages.

## What's covered

- The `6a4e3200` radar service on current-generation Varia devices (confirmed on a RearVue 820; other Varia radar devices probably share it but are untested here).
- The legacy V1 stream on characteristic `6a4e3203` (heartbeats, threat packets, sector amplitude packets).
- The V2 measurement stream on characteristic `6a4e3204` (per-target structs with lateral offset, length, width, lateral and longitudinal speed).
- The pre-handshake sequence that unlocks V2 on the RearVue 820.
- The LESC (LE Secure Connections) pairing quirk that breaks programmatic `createBond()` on Android 16 / Pixel 10 Pro XL, and the workaround.

See [PROTOCOL.md](PROTOCOL.md) for the full byte-level spec.

## What's in the repo

```
.
|-- PROTOCOL.md             # authoritative protocol doc
|-- python/
|   |-- decode_3203.py      # V1 stream decoder (stdlib-only CLI)
|   |-- decode_3204.py      # V2 stream decoder (stdlib-only CLI)
|   `-- tests/              # pytest suite for both decoders
|-- kotlin/
|   |-- Model.kt            # Vehicle / RadarState / VehicleSize / DataSource types
|   |-- RadarV1Decoder.kt   # V1 stateful decoder, pure JVM (no Android imports)
|   |-- RadarV2Decoder.kt   # V2 stateful decoder, pure JVM (no Android imports)
|   `-- test/               # JUnit4 tests for both decoders
`-- samples/
    `-- README.md           # notes on capture log format + how to gather your own
```

The Kotlin sources are lifted verbatim from a working Android app and depend only on standard library types. They compile against plain Kotlin/JVM; the JUnit tests run without Android instrumentation.

## Status

Accurate as of 2026-04-18.

- V1 (`3203`) decoding: confirmed across thousands of packets from real commutes.
- V2 (`3204`) decoding: byte format confirmed against live captures from Varia Mobile; our own handshake replays it successfully. Real-road target decoding is tested against synthetic frames; end-to-end road validation is pending.
- Pairing: verified on Android 16 / Pixel 10 Pro XL via both the Garmin Varia Mobile app and Settings -> Connected devices. Other Android versions and other phones untested.
- The full Android app that drives this stack will be added once it's past its throwaway-harness phase.

## Prior art and credit

- github.com/rale/radarble is the only public writeup of the V2 `6a4e3204` target struct I have found. No code from that repo was copied; the byte layout here was cross-checked against live captures.
- github.com/Wunderfitz/harbour-tacho (C++, SailfishOS) is a long-running V1 client; the V1 layout here was cross-checked against its source.
- github.com/kartoone/mybiketraffic (Monkey C) is a Garmin Connect IQ data-field and the closest public reference to V1 packet behaviour from inside the ConnectIQ radar API. The "V1 third byte = approach speed in m/s (multiply by 3.6 for km/h)" reading cited from it and from other public notes does not match real-road 820 captures, where that byte only ever takes values 0 or 1 (see [PROTOCOL.md](PROTOCOL.md) §V1 threat packet).
- kartoone's ongoing Garmin developer-forum thread [Garmin Varia Rearvue 820 Radar development](https://forums.garmin.com/developer/connect-iq/f/discussion/431074/garmin-varia-rearvue-820-radar-development) is where most of this was discussed publicly; anyone with follow-up data or corrections is encouraged to post there as well as opening an issue here.

## Licence

GPLv3 or later. See [LICENSE](LICENSE).

## Contributions welcome

- More captures from other Varia models (RTL515, RTL516, Vue 870) so the GATT-variant table can be filled in.
- Independent confirmation of the V2 unlock sequence on non-Pixel Android devices or on iOS.
- Corrections or gaps in PROTOCOL.md.
