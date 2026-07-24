# bike-radar-docs

[![CI](https://github.com/partymola/bike-radar-docs/actions/workflows/ci.yml/badge.svg)](https://github.com/partymola/bike-radar-docs/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

Protocol notes and reference decoders for the `6a4e3200`-family BLE GATT services used by some cycling-radar accessories, written for interoperability with third-party clients.

This repo is a knowledge drop, not a finished product. It documents what the wire looks like and gives minimal, standalone decoders that anyone can run against a captured log.

## What's covered

- The `6a4e3200` radar service on currently-shipping rear-radar accessories in this device family (confirmed on a RearVue 820; other related devices probably share it but are untested here).
- The legacy V1 stream on characteristic `6a4e3203` (heartbeats, threat packets, sector amplitude packets).
- The V2 measurement stream on characteristic `6a4e3204` (per-target structs with lateral offset, length, width, lateral and longitudinal speed).
- The pre-handshake sequence that enables V2 on the RearVue 820.
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

The Kotlin sources are taken unchanged from my own Android client and depend only on standard library types. They compile against plain Kotlin/JVM; the JUnit tests run without Android instrumentation.

## Status


- V1 (`3203`) decoding: confirmed across thousands of packets from real commutes.
- V2 (`3204`) decoding: byte format confirmed against live captures; our own handshake replays it successfully. Range and target decoding is validated statistically against real-commute captures (trajectory-smoothness and distance-distribution checks; see PROTOCOL.md); the automated tests use synthetic frames, and instrumented ground-truth (decoded distance vs independently measured) is still to do.
- Pairing: verified on Android 16 / Pixel 10 Pro XL via both the manufacturer's official Android app and Settings -> Connected devices. Other Android versions and other phones untested.

## Prior art and credit

- github.com/rale/radarble is the only public writeup of the V2 `6a4e3204` target struct I have found. No code from that repo was copied; the byte layout here was cross-checked against live captures.
- github.com/Wunderfitz/harbour-tacho (C++, SailfishOS) is a long-running V1 client; the V1 layout here was cross-checked against its source.
- github.com/kartoone/mybiketraffic (Monkey C) is a Garmin Connect IQ data-field and the closest public reference to V1 packet behaviour from inside the ConnectIQ radar API. The "V1 third byte = approach speed in m/s (multiply by 3.6 for km/h)" reading cited from it and from other public notes does not match real-road 820 captures, where that byte only ever takes values 0 or 1 (see [PROTOCOL.md](PROTOCOL.md) §V1 threat packet).
- kartoone has an ongoing developer-forum thread covering the same territory; anyone with follow-up data or corrections is encouraged to participate there as well as opening an issue here.

## Licence

GPLv3 or later. See [LICENSE](LICENSE).

## Contributions welcome

- More captures from other models in this radar family (e.g. RTL515, RTL516) so the GATT-variant table can be filled in.
- Compatibility reports from other Android devices and from iOS: does the connection sequence in PROTOCOL.md produce the V2 stream on your hardware?
- Corrections or gaps in PROTOCOL.md.
