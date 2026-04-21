#!/usr/bin/env python3
"""Unit tests for decode_3204.parse_target / parse_notification.

Hand-packs synthetic frames covering zone transitions, lateral offset
signed-byte boundaries, multi-target notifications, status frames, and
device-status frames. No real capture required.
"""
from __future__ import annotations

import io
import unittest

from decode_3204 import (
    CLASS_NAMES,
    DEVICE_STATUS_BIT,
    HEADER_SIZE,
    STATUS_FRAME_BIT,
    TARGET_SIZE,
    ZONE_SIZE_M,
    Frame,
    iter_3204_lines,
    parse_notification,
    parse_target,
)


def pack_target(tid: int, cls: int, b2: int, zone: int,
                rx_int8: int = 0, length_raw: int = 0, width_raw: int = 0,
                sy_raw: int = 0, sentinel: int = 0x80) -> bytes:
    """Pack a 9-byte target struct.

    `zone` (0..7) is encoded into byte[3] bits 0..2 via the inverse of the
    decoder's ((b3 & 7) + 1) & 7 rotation: b3 bits 0..2 = (zone - 1) & 7.
    Bits 3..7 of byte[3] are left at zero (decoder ignores them).
    `rx_int8` is a signed 8-bit lateral offset (* 0.1 m by the decoder).
    """
    b3 = (zone - 1) & 0x07
    return bytes([
        tid & 0xff,
        cls & 0xff,
        b2 & 0xff,
        b3 & 0xff,
        rx_int8 & 0xff,
        length_raw & 0xff,
        width_raw & 0xff,
        sy_raw & 0xff,
        sentinel & 0xff,
    ])


def pack_frame(header: int, targets: list[bytes]) -> bytes:
    return bytes([header & 0xff, (header >> 8) & 0xff]) + b"".join(targets)


class TestParseTarget(unittest.TestCase):
    def test_zero_target(self):
        t = parse_target(pack_target(0, 4, b2=0, zone=0))
        self.assertEqual(t.target_id, 0)
        self.assertEqual(t.target_class, 4)
        self.assertEqual(t.range_x, 0.0)
        self.assertEqual(t.range_y, 0.0)
        self.assertEqual(t.zone, 0)
        self.assertEqual(t.length, 0.0)
        self.assertEqual(t.width, 0.0)
        self.assertEqual(t.speed_y, 0.0)
        self.assertEqual(t.class_name, "UNKNOWN")

    def test_zone_0_near_field(self):
        # b2=100, zone=0 -> 10.0 m
        t = parse_target(pack_target(7, 23, b2=100, zone=0))
        self.assertEqual(t.zone, 0)
        self.assertAlmostEqual(t.range_y, 10.0)

    def test_zone_0_top_of_range(self):
        # b2=255, zone=0 -> 25.5 m (just inside the near zone)
        t = parse_target(pack_target(7, 23, b2=255, zone=0))
        self.assertAlmostEqual(t.range_y, 25.5)

    def test_zone_1_wrap(self):
        # b2=0, zone=1 -> exactly 25.6 m (just past the near zone boundary)
        t = parse_target(pack_target(7, 23, b2=0, zone=1))
        self.assertEqual(t.zone, 1)
        self.assertAlmostEqual(t.range_y, 25.6)

    def test_zone_2(self):
        # b2=100, zone=2 -> 51.2 + 10.0 = 61.2 m
        t = parse_target(pack_target(7, 23, b2=100, zone=2))
        self.assertEqual(t.zone, 2)
        self.assertAlmostEqual(t.range_y, 61.2)

    def test_zone_3(self):
        # b2=200, zone=3 -> 76.8 + 20.0 = 96.8 m
        t = parse_target(pack_target(7, 23, b2=200, zone=3))
        self.assertEqual(t.zone, 3)
        self.assertAlmostEqual(t.range_y, 96.8)

    def test_zone_7_far_limit(self):
        # b2=255, zone=7 -> 179.2 + 25.5 = 204.7 m (top of addressable range)
        t = parse_target(pack_target(7, 23, b2=255, zone=7))
        self.assertEqual(t.zone, 7)
        self.assertAlmostEqual(t.range_y, 204.7)

    def test_rangex_positive(self):
        # rx_int8=50 -> +5.0 m (vehicle to the right)
        t = parse_target(pack_target(1, 16, b2=100, zone=0, rx_int8=50))
        self.assertAlmostEqual(t.range_x, 5.0)

    def test_rangex_negative(self):
        # rx_int8=-30 -> -3.0 m (vehicle to the left)
        t = parse_target(pack_target(1, 16, b2=100, zone=0, rx_int8=-30))
        self.assertAlmostEqual(t.range_x, -3.0)

    def test_rangex_max_positive(self):
        # int8 max = 127 -> +12.7 m
        t = parse_target(pack_target(1, 16, b2=0, zone=0, rx_int8=127))
        self.assertAlmostEqual(t.range_x, 12.7)

    def test_rangex_min_negative(self):
        # int8 min = -128 -> -12.8 m
        t = parse_target(pack_target(1, 16, b2=0, zone=0, rx_int8=-128))
        self.assertAlmostEqual(t.range_x, -12.8)

    def test_length_width_scale(self):
        # length_raw=16 -> 4.0 m; width_raw=8 -> 2.0 m
        t = parse_target(pack_target(1, 23, b2=0, zone=0, length_raw=16, width_raw=8))
        self.assertEqual(t.length, 4.0)
        self.assertEqual(t.width, 2.0)

    def test_speed_approaching(self):
        # sy_raw=-10 -> -5.0 m/s (approaching at 5 m/s)
        t = parse_target(pack_target(1, 23, b2=0, zone=0, sy_raw=-10))
        self.assertEqual(t.speed_y, -5.0)

    def test_speed_receding(self):
        # sy_raw=+10 -> +5.0 m/s (falling behind)
        t = parse_target(pack_target(1, 23, b2=0, zone=0, sy_raw=10))
        self.assertEqual(t.speed_y, 5.0)

    def test_speed_min_max(self):
        # int8 range: -128..127 -> -64.0..+63.5 m/s
        t = parse_target(pack_target(1, 23, b2=0, zone=0, sy_raw=127))
        self.assertEqual(t.speed_y, 63.5)
        t = parse_target(pack_target(1, 23, b2=0, zone=0, sy_raw=-128))
        self.assertEqual(t.speed_y, -64.0)

    def test_class_name_lookup(self):
        for code, name in CLASS_NAMES.items():
            t = parse_target(pack_target(0, code, b2=0, zone=0))
            self.assertEqual(t.class_name, name)

    def test_class_name_unknown(self):
        t = parse_target(pack_target(0, 99, b2=0, zone=0))
        self.assertEqual(t.class_name, "UNKNOWN(99)")

    def test_wrong_length_rejected(self):
        with self.assertRaises(ValueError):
            parse_target(b"\x00" * 8)
        with self.assertRaises(ValueError):
            parse_target(b"\x00" * 10)


class TestParseNotification(unittest.TestCase):
    def test_single_target(self):
        payload = pack_frame(0x0000, [pack_target(5, 23, b2=200, zone=1, rx_int8=15, sy_raw=-10)])
        frame = parse_notification(payload)
        self.assertFalse(frame.is_status_frame)
        self.assertFalse(frame.is_device_status)
        self.assertEqual(len(frame.targets), 1)
        t = frame.targets[0]
        self.assertEqual(t.target_id, 5)
        self.assertAlmostEqual(t.range_x, 1.5)
        # zone=1, b2=200 -> 25.6 + 20.0 = 45.6 m
        self.assertAlmostEqual(t.range_y, 45.6)
        self.assertEqual(t.speed_y, -5.0)

    def test_multi_target(self):
        t1 = pack_target(1, 23, b2=100, zone=0, rx_int8=-15)   # 10.0 m, -1.5 m
        t2 = pack_target(2, 36, b2=0, zone=2, rx_int8=0)        # 51.2 m
        t3 = pack_target(3, 13, b2=50, zone=1, rx_int8=20)      # 30.6 m, +2.0 m
        frame = parse_notification(pack_frame(0x0000, [t1, t2, t3]))
        self.assertEqual(len(frame.targets), 3)
        ids = [t.target_id for t in frame.targets]
        self.assertEqual(ids, [1, 2, 3])
        self.assertEqual(frame.targets[0].class_name, "NORMAL")
        self.assertEqual(frame.targets[1].class_name, "HIGH")
        self.assertEqual(frame.targets[2].class_name, "LOW_STABLE")
        self.assertAlmostEqual(frame.targets[0].range_x, -1.5)
        self.assertAlmostEqual(frame.targets[0].range_y, 10.0)
        self.assertAlmostEqual(frame.targets[1].range_y, 51.2)
        self.assertAlmostEqual(frame.targets[2].range_y, 30.6)

    def test_status_frame_skipped(self):
        garbage = b"\xff" * 9
        payload = pack_frame(STATUS_FRAME_BIT, [garbage])
        frame = parse_notification(payload)
        self.assertTrue(frame.is_status_frame)
        self.assertEqual(frame.targets, [])

    def test_device_status_frame(self):
        payload = pack_frame(DEVICE_STATUS_BIT, [b"\x01\x02\x03\x04"])
        frame = parse_notification(payload)
        self.assertTrue(frame.is_device_status)
        self.assertEqual(frame.targets, [])

    def test_empty_body(self):
        frame = parse_notification(pack_frame(0x0000, []))
        self.assertEqual(frame.targets, [])
        self.assertFalse(frame.is_status_frame)
        self.assertFalse(frame.is_device_status)

    def test_body_with_trailing_bytes(self):
        # 1 target + 3 trailing bytes -> trailing bytes ignored, one target returned
        payload = pack_frame(0x0000, [pack_target(1, 23, b2=100, zone=0)]) + b"\xaa\xbb\xcc"
        frame = parse_notification(payload)
        self.assertEqual(len(frame.targets), 1)

    def test_short_notification_rejected(self):
        with self.assertRaises(ValueError):
            parse_notification(b"\x01")
        with self.assertRaises(ValueError):
            parse_notification(b"")

    def test_header_preserved(self):
        payload = pack_frame(0x1234, [])
        frame = parse_notification(payload)
        self.assertEqual(frame.header, 0x1234)


class TestCaptureLineIteration(unittest.TestCase):
    def test_filters_non_3204(self):
        lines = [
            "# comment line",
            "",
            "1700000000000 3203 02",
            "1700000000100 3204 0000",
            "1700000000200 3204 0000" + pack_target(1, 23, b2=100, zone=0).hex(),
            "1700000000300 2a19 5a",
            "malformed line",
            "1700000000400 3204 notahexstring",
        ]
        fp = io.StringIO("\n".join(lines))
        out = list(iter_3204_lines(fp))
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0][0], 1700000000100)
        self.assertEqual(out[0][1], b"\x00\x00")
        self.assertEqual(out[1][0], 1700000000200)

    def test_roundtrip_through_file_iterator(self):
        target_bytes = pack_target(9, 36, b2=50, zone=3, rx_int8=-40, sy_raw=-20)
        hex_payload = (pack_frame(0x0000, [target_bytes])).hex()
        line = f"1700000005000 3204 {hex_payload}\n"
        fp = io.StringIO(line)
        entries = list(iter_3204_lines(fp))
        self.assertEqual(len(entries), 1)
        ts, payload = entries[0]
        frame = parse_notification(payload)
        self.assertEqual(len(frame.targets), 1)
        t = frame.targets[0]
        self.assertEqual(t.target_id, 9)
        self.assertEqual(t.class_name, "HIGH")
        self.assertAlmostEqual(t.range_x, -4.0)
        # zone=3, b2=50 -> 76.8 + 5.0 = 81.8 m
        self.assertAlmostEqual(t.range_y, 81.8)
        self.assertEqual(t.speed_y, -10.0)


if __name__ == "__main__":
    unittest.main()
