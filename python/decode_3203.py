#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 the bike-radar-docs contributors.
"""V1 stream (characteristic 0x3203) decoder.

Reads BLE capture files in the line-based format documented in
PROTOCOL.md and prints decoded heartbeats and vehicle threat packets.

Stateless per-packet parsing. Callers that want stateful track ageing
(e.g. to drop a vehicle that has not been seen for N seconds) should
layer that on top using the (seq_byte, vehicles) tuple returned by
parse_threat().

See PROTOCOL.md section "V1 stream: characteristic 0x3203" for the
byte layout, vehicle-id filter rules, and the fragmentation rule
(which we have never observed in practice on a RearVue 820).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Iterable, Iterator

THREAT_SEQ_NIBBLE = 0x02
SECTOR_AMP_LEN = 6
SECTOR_AMP_TAG = 0x06


def parse_heartbeat(payload: bytes) -> int:
    """Return the 4-bit sequence counter of a V1 heartbeat packet.

    Raises ValueError if the payload is not a heartbeat.
    """
    if len(payload) != 1 or (payload[0] & 0x0F) != THREAT_SEQ_NIBBLE:
        raise ValueError("not a V1 heartbeat")
    return (payload[0] >> 4) & 0xF


def is_threat_packet(payload: bytes) -> bool:
    return (
        len(payload) >= 4
        and (len(payload) - 1) % 3 == 0
        and (payload[0] & 0x0F) == THREAT_SEQ_NIBBLE
    )


def parse_threat(payload: bytes) -> tuple[int, list[tuple[int, int, int]]]:
    """Parse a V1 threat packet.

    Returns (seq_byte, vehicles) where vehicles is a list of
    (track_id, distance_m, flag) tuples. Filtered-out entries
    (vid without bit 7 set, vid == 0xFD, dist == 0xFF) are already
    removed. track_id == vid & 0x7F.

    Caller can implement fragmentation by checking whether this
    packet's seq_byte equals the previous packet's seq_byte + 2 and
    prepending the previous packet's vehicles if so. On the RearVue
    820 this case has never been observed in practice.

    Raises ValueError if the payload is not a threat packet.
    """
    if not is_threat_packet(payload):
        raise ValueError("not a V1 threat packet")
    seq_byte = payload[0]
    vehicles: list[tuple[int, int, int]] = []
    n = (len(payload) - 1) // 3
    for i in range(n):
        vid = payload[1 + 3 * i]
        dist = payload[1 + 3 * i + 1]
        flag = payload[1 + 3 * i + 2]
        if vid < 0x80 or vid == 0xFD:
            continue
        if dist == 0xFF:
            continue
        vehicles.append((vid & 0x7F, dist, flag))
    return seq_byte, vehicles


def is_sector_amplitude(payload: bytes) -> bool:
    return len(payload) == SECTOR_AMP_LEN and payload[0] == SECTOR_AMP_TAG


def iter_3203_lines(fp: Iterable[str]) -> Iterator[tuple[int, bytes]]:
    """Yield (unix_ms, payload_bytes) for every well-formed 0x3203 line."""
    for line in fp:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3 or parts[1] != "3203":
            continue
        try:
            ts = int(parts[0])
            payload = bytes.fromhex(parts[2])
        except ValueError:
            continue
        yield ts, payload


def decode_file(path: str, verbose: bool = False) -> None:
    heartbeats = 0
    threats = 0
    sector_amps = 0
    unknown = 0
    session_start_ms: int | None = None
    prev_seq: int | None = None
    prev_vehicles: list[tuple[int, int, int]] = []

    def ts_str(unix_ms: int) -> str:
        dt = datetime.fromtimestamp(unix_ms / 1000, tz=timezone.utc).astimezone()
        return dt.strftime("%H:%M:%S.") + f"{unix_ms % 1000:03d}"

    def elapsed(unix_ms: int) -> str:
        if session_start_ms is None:
            return "?"
        return f"+{(unix_ms - session_start_ms) / 1000:7.1f}s"

    with open(path) as fp:
        for ts, payload in iter_3203_lines(fp):
            if session_start_ms is None:
                session_start_ms = ts

            if len(payload) == 1:
                heartbeats += 1
                if verbose:
                    counter = parse_heartbeat(payload)
                    print(f"  {ts_str(ts)}  HEARTBEAT  seq={counter:X}")
                continue

            if is_threat_packet(payload):
                seq_byte, vehicles = parse_threat(payload)

                frag_note = ""
                if prev_seq is not None and seq_byte == ((prev_seq + 2) & 0xFF):
                    vehicles = prev_vehicles + vehicles
                    frag_note = " [+frag]"
                prev_seq = seq_byte
                prev_vehicles = vehicles
                threats += 1

                parts_out = [
                    f"id={vid:#04x} dist={dist:3d}m flag={flag:#04x}"
                    for vid, dist, flag in vehicles
                ]
                veh_str = "  |  ".join(parts_out) if parts_out else "(empty)"
                print(f"  {ts_str(ts)}  {elapsed(ts)}  n={len(vehicles)}{frag_note}  {veh_str}")
                continue

            if is_sector_amplitude(payload):
                sector_amps += 1
                if verbose:
                    sector = (payload[1] >> 2) & 0x03
                    mode = (payload[1] >> 7) & 0x01
                    amp = payload[5]
                    print(f"  {ts_str(ts)}  SECTOR_AMP  mode={mode} sector={sector} amp={amp}")
                continue

            unknown += 1
            if verbose or unknown <= 5:
                print(f"  {ts_str(ts)}  UNKNOWN({len(payload)}B)  raw={payload.hex()}")

    print()
    print(
        f"Summary: heartbeats={heartbeats}  threats={threats}  "
        f"sector_amp={sector_amps}  unknown={unknown}"
    )


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Decode V1 (0x3203) BLE capture logs")
    ap.add_argument("files", nargs="+", help="capture .log files")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print heartbeats and sector-amplitude packets too")
    args = ap.parse_args(argv[1:])
    for f in args.files:
        print(f"\n=== {f} ===")
        decode_file(f, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
