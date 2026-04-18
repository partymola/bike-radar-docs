#!/usr/bin/env python3
"""Decode 3204 (V2 measurement) notifications from a varia-reader capture file.

Format reimplemented from the public README at github.com/rale/radarble
(no code copied — repo has no licence). Independent implementation.

Wire format per notification:
  [2-byte little-endian header] + N * [9-byte target struct]

Header bits (from README):
  bit 0 set -> status/ack frame, no target payload (skip)
  bit 2 set -> device-status frame (log, no targets)

Target struct (9 bytes):
  [0]    uint8  targetId          radar-assigned track ID
  [1]    uint8  targetClass       enum (see CLASS_NAMES)
  [2:5]  24-bit big-endian pack   top 13 bits signed rangeY (x0.1 m longitudinal),
                                  bottom 11 bits signed rangeX (x0.1 m lateral)
  [5]    uint8  length            x0.25 m
  [6]    uint8  width             x0.25 m
  [7]    int8   speedX            x0.5 m/s (lateral velocity)
  [8]    int8   speedY            x0.5 m/s (longitudinal velocity; +ve approaching)

Signed ranges (with x0.1 m scale):
  rangeX: -1024..1023  ->  -102.4..+102.3 m
  rangeY: -4096..4095  ->  -409.6..+409.5 m
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
    length: float    # m
    width: float     # m
    speed_x: float   # lateral velocity (m/s)
    speed_y: float   # longitudinal velocity (m/s, +ve approaching)

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
    r24 = (data[2] << 16) | (data[3] << 8) | data[4]
    rx_raw = r24 & 0x07FF
    ry_raw = (r24 >> 11) & 0x1FFF
    if rx_raw & 0x0400:
        rx_raw -= 0x0800
    if ry_raw & 0x1000:
        ry_raw -= 0x2000
    length = data[5] * 0.25
    width = data[6] * 0.25
    sx = struct.unpack_from("b", data, 7)[0] * 0.5
    sy = struct.unpack_from("b", data, 8)[0] * 0.5
    return Target(
        target_id=tid,
        target_class=cls,
        range_x=rx_raw * 0.1,
        range_y=ry_raw * 0.1,
        length=length,
        width=width,
        speed_x=sx,
        speed_y=sy,
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
        f"rx={t.range_x:+6.1f}m ry={t.range_y:+7.1f}m "
        f"L={t.length:.2f} W={t.width:.2f} "
        f"vx={t.speed_x:+5.1f} vy={t.speed_y:+5.1f}"
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
