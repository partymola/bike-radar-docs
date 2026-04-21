#!/usr/bin/env python3
"""Decode 3204 (V2 measurement) notifications from a varia-reader capture file.

Wire format per notification:
  [2-byte little-endian header] + N * [9-byte target struct]

Header bits:
  bit 0 set -> status/ack frame, no target payload (skip)
  bit 2 set -> device-status frame (log, no targets)

Target struct (9 bytes):
  [0]    uint8  targetId       radar-assigned track ID
  [1]    uint8  targetClass    enum (see CLASS_NAMES)
  [2]    uint8  rangeYLow      x0.1 m distance within the current 25.6 m zone
  [3]    bits 0..2: rangeYZone (see below); bits 3..7 reserved, not decoded
  [4]    int8   rangeX         lateral offset x0.1 m (+ve = right)
  [5]    uint8  length         x0.25 m (class template)
  [6]    uint8  width          x0.25 m (class template)
  [7]    int8   speedY         x0.5 m/s (negative = approaching)
  [8]    uint8  0x80           observed constant sentinel

Reconstructing rangeY:
  zone       = ((byte[3] & 0x07) + 1) & 0x07  # 0..7, rotation: 7->0->1->2->...
  rangeY_m   = zone * 25.6 + byte[2] * 0.1

Eight zones x 25.6 m gives 0..204.8 m, covering the RearVue 820's 175 m spec.

Derived from a 200-wrap-event bit-flip analysis across 15k packets on 2026-04-21
on RearVue 820 firmware. The earlier rale/radarble packed interpretation
(13-bit signed rangeY + 11-bit signed rangeX in a 24-bit big-endian word)
does not match this firmware's output - see PROTOCOL.md for details.
"""
from __future__ import annotations

import struct
import sys
from dataclasses import dataclass
from typing import Iterable, Iterator

TARGET_SIZE = 9
HEADER_SIZE = 2
STATUS_FRAME_BIT = 0x0001
DEVICE_STATUS_BIT = 0x0004

ZONE_SIZE_M = 25.6

CLASS_NAMES = {
    4:  "UNKNOWN",
    13: "LOW_STABLE",
    16: "LOW",
    23: "NORMAL",
    26: "NORMAL_STABLE",
    36: "HIGH",
}


@dataclass
class Target:
    target_id: int
    target_class: int
    range_x: float   # lateral (m)
    range_y: float   # longitudinal distance (m)
    zone: int        # 0..7, number of 25.6 m zones (0 = 0..25.5 m)
    length: float    # m
    width: float     # m
    speed_y: float   # closing velocity (m/s, -ve = approaching)

    @property
    def class_name(self) -> str:
        return CLASS_NAMES.get(self.target_class, f"UNKNOWN({self.target_class})")


@dataclass
class Frame:
    header: int
    targets: list[Target]
    is_status_frame: bool
    is_device_status: bool


def parse_target(data: bytes) -> Target:
    if len(data) != TARGET_SIZE:
        raise ValueError(f"target struct must be {TARGET_SIZE} bytes, got {len(data)}")
    tid = data[0]
    cls = data[1]
    b2 = data[2]
    b3 = data[3]
    zone = ((b3 & 0x07) + 1) & 0x07
    range_y = zone * ZONE_SIZE_M + b2 * 0.1
    rx_signed = struct.unpack_from("b", data, 4)[0]
    range_x = rx_signed * 0.1
    length = data[5] * 0.25
    width = data[6] * 0.25
    speed_y = struct.unpack_from("b", data, 7)[0] * 0.5
    return Target(
        target_id=tid,
        target_class=cls,
        range_x=range_x,
        range_y=range_y,
        zone=zone,
        length=length,
        width=width,
        speed_y=speed_y,
    )


def parse_notification(payload: bytes) -> Frame:
    if len(payload) < HEADER_SIZE:
        raise ValueError(f"notification too short ({len(payload)} bytes, need >= {HEADER_SIZE})")
    header = payload[0] | (payload[1] << 8)
    is_status = bool(header & STATUS_FRAME_BIT)
    is_device = bool(header & DEVICE_STATUS_BIT)
    body = payload[HEADER_SIZE:]
    targets: list[Target] = []
    if not is_status and not is_device:
        n = len(body) // TARGET_SIZE
        for i in range(n):
            chunk = body[i * TARGET_SIZE:(i + 1) * TARGET_SIZE]
            targets.append(parse_target(chunk))
    return Frame(header=header, targets=targets, is_status_frame=is_status, is_device_status=is_device)


def iter_3204_lines(fp: Iterable[str]) -> Iterator[tuple[int, bytes]]:
    for line in fp:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3 or parts[1] != "3204":
            continue
        try:
            ts = int(parts[0])
            payload = bytes.fromhex(parts[2])
        except ValueError:
            continue
        yield ts, payload


def format_target(ts: int, idx: int, t: Target) -> str:
    return (
        f"{ts} T{idx} id={t.target_id:>3} cls={t.class_name:<13} "
        f"rx={t.range_x:+6.1f}m ry={t.range_y:+7.1f}m zone={t.zone} "
        f"L={t.length:.2f} W={t.width:.2f} vy={t.speed_y:+5.1f}"
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <capture.log>", file=sys.stderr)
        return 2
    total_frames = 0
    total_targets = 0
    with open(argv[1]) as fp:
        for ts, payload in iter_3204_lines(fp):
            total_frames += 1
            try:
                frame = parse_notification(payload)
            except ValueError as e:
                print(f"{ts} BAD {payload.hex()} ({e})")
                continue
            if frame.is_status_frame:
                print(f"{ts} STATUS hdr=0x{frame.header:04x}")
                continue
            if frame.is_device_status:
                print(f"{ts} DEVSTATUS hdr=0x{frame.header:04x} body={payload[HEADER_SIZE:].hex()}")
                continue
            if not frame.targets:
                print(f"{ts} EMPTY hdr=0x{frame.header:04x}")
                continue
            for i, t in enumerate(frame.targets):
                print(format_target(ts, i, t))
                total_targets += 1
    print(f"# {total_frames} frames, {total_targets} target rows", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
