#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 the bike-radar-docs contributors.
"""Decode 3204 (V2 measurement) notifications from a varia-reader capture file.

Wire format per notification:
  [2-byte little-endian header] + N * [9-byte target struct]

Header bits:
  bit 0 set -> status/ack frame, no target payload (skip)
  bit 2 set -> device-status frame (carries the rider's own bike speed in
               byte[len-1] * 0.25 km/h; otherwise no target payload)

Target struct (9 bytes):
  [0]    uint8  targetId       radar-assigned track ID
  [1]    uint8  targetClass    enum (see CLASS_NAMES)
  [2..4] packed range field    24-bit little-endian (see decoding below)
  [5]    uint8  length         x0.25 m (class template; not a real measurement)
  [6]    uint8  width          x0.25 m (class template; not a real measurement)
  [7]    int8   speedY         x0.5 m/s (longitudinal closing speed; -ve = approaching)
  [8]    int8   speedX         x0.5 m/s (lateral; 0x80 / -64 m/s is the
                                "no lateral velocity" sentinel)

Reconstructing rangeY and rangeX from bytes [2..4]:
  packed     = byte[2] | (byte[3] << 8) | (byte[4] << 16)
  rangeXBits = packed & 0x07FF                                  # 11-bit
  if rangeXBits & 0x0400: rangeXBits -= 0x0800                  # sign-extend
  rangeYBits = (packed >> 11) & 0x1FFF                          # 13-bit
  if rangeYBits & 0x1000: rangeYBits -= 0x2000                  # sign-extend
  rangeX_m   = rangeXBits * 0.1                                 # ~+/-204.7 m theoretical
  rangeY_m   = rangeYBits * 0.1                                 # ~+/-409.5 m theoretical, ~220 m in practice

Sign convention for the rear-radar: rangeY > 0 = behind the rider (the
dominant case), rangeY < 0 = ahead (post-overtake, rare).

Validation: this decoder produces median |rangeY| = 30 m, max 220 m, and
matches V1's distance distribution within statistical noise across 22 k
target frames from real commute captures. Frame-to-frame median delta is
0.20 m; p98 = 1.70 m. 96% of long+far track segments satisfy V1's
smoothness criterion.

An earlier revision of this file documented a different byte[2..4]
decoding (rangeYLow plus a 3-bit zone selector); that interpretation
does not fit long-range captures and has been retracted. See
PROTOCOL.md for the byte layout and validation history.
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

CLASS_NAMES = {
    4:  "UNCLASSIFIED",
    13: "WEAK_HOLD",
    16: "WEAK",
    23: "MEDIUM",
    26: "MEDIUM_HOLD",
    36: "STRONG",
}


@dataclass
class Target:
    target_id: int
    target_class: int
    range_x: float   # lateral (m), positive = right of rider
    range_y: float   # longitudinal (m), positive = behind rider
    length: float    # m (class template)
    width: float     # m (class template)
    speed_y: float   # closing velocity (m/s, negative = approaching)
    speed_x: float   # lateral velocity (m/s); -64 m/s is the "no data" sentinel

    @property
    def class_name(self) -> str:
        return CLASS_NAMES.get(self.target_class, f"UNKNOWN({self.target_class})")

    @property
    def is_behind(self) -> bool:
        return self.range_y > 0


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
    packed = data[2] | (data[3] << 8) | (data[4] << 16)
    rx_bits = packed & 0x07FF
    if rx_bits & 0x0400:
        rx_bits -= 0x0800
    ry_bits = (packed >> 11) & 0x1FFF
    if ry_bits & 0x1000:
        ry_bits -= 0x2000
    range_x = rx_bits * 0.1
    range_y = ry_bits * 0.1
    length = data[5] * 0.25
    width = data[6] * 0.25
    speed_y = struct.unpack_from("b", data, 7)[0] * 0.5
    speed_x = struct.unpack_from("b", data, 8)[0] * 0.5
    return Target(
        target_id=tid,
        target_class=cls,
        range_x=range_x,
        range_y=range_y,
        length=length,
        width=width,
        speed_y=speed_y,
        speed_x=speed_x,
    )


def parse_notification(payload: bytes) -> Frame:
    if len(payload) < HEADER_SIZE:
        return Frame(header=0, targets=[], is_status_frame=False, is_device_status=False)
    header = payload[0] | (payload[1] << 8)
    is_status = (header & STATUS_FRAME_BIT) != 0
    is_device_status = (header & DEVICE_STATUS_BIT) != 0
    if is_status or is_device_status:
        return Frame(header=header, targets=[], is_status_frame=is_status, is_device_status=is_device_status)
    body = payload[HEADER_SIZE:]
    n = len(body) // TARGET_SIZE
    targets = [parse_target(body[i * TARGET_SIZE:(i + 1) * TARGET_SIZE]) for i in range(n)]
    return Frame(header=header, targets=targets, is_status_frame=False, is_device_status=False)


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
    side = "behind" if t.is_behind else "ahead"
    return (
        f"{ts} T{idx} id={t.target_id:>3} cls={t.class_name:<13} "
        f"rx={t.range_x:+6.1f}m ry={t.range_y:+7.1f}m ({side}) "
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
            frame = parse_notification(payload)
            if frame.is_status_frame or frame.is_device_status or not frame.targets:
                continue
            for idx, t in enumerate(frame.targets):
                total_targets += 1
                print(format_target(ts, idx, t))
    print(f"# {total_frames} frames, {total_targets} targets", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
