#!/usr/bin/env python3
"""Unit tests for decode_3204.parse_target / parse_notification.

Hand-packs synthetic frames covering the 24-bit packed range field
(rangeX/rangeY sign-extension and independence), lateral/longitudinal
speed scaling, multi-target notifications, status frames, device-status
frames, and capture-line iteration. No real capture required.

Expected values are derived from the documented byte layout: bytes [2..4]
are a 24-bit little-endian word with rangeX in bits 0..10 (11-bit signed,
x0.1 m) and rangeY in bits 11..23 (13-bit signed, x0.1 m).
"""
from __future__ import annotations

import io
import unittest

from decode_3204 import (
    CLASS_NAMES,
    DEVICE_STATUS_BIT,
    STATUS_FRAME_BIT,
    iter_3204_lines,
    parse_notification,
    parse_target,
)

# int8 sentinel meaning "no lateral velocity" (byte[8] == 0x80 -> -64.0 m/s).
NO_LATERAL = 0x80


def pack_target(tid: int, cls: int, range_x_raw: int = 0, range_y_raw: int = 0,
                length_raw: int = 0, width_raw: int = 0,
                speed_y_raw: int = 0, speed_x_raw: int = NO_LATERAL) -> bytes:
    """Pack a 9-byte target struct.

    ``range_x_raw`` (11-bit signed) and ``range_y_raw`` (13-bit signed) are
    packed into the 24-bit little-endian word at bytes [2..4] (rangeX = bits
    0..10, rangeY = bits 11..23); the decoder multiplies each raw value by
    0.1 m. ``speed_y_raw`` and ``speed_x_raw`` are signed int8 (x0.5 m/s).
    """
    packed = (range_x_raw & 0x07FF) | ((range_y_raw & 0x1FFF) << 11)
    return bytes([
        tid & 0xff,
        cls & 0xff,
        packed & 0xff,
        (packed >> 8) & 0xff,
        (packed >> 16) & 0xff,
        length_raw & 0xff,
        width_raw & 0xff,
        speed_y_raw & 0xff,
        speed_x_raw & 0xff,
    ])


def pack_frame(header: int, targets: list[bytes]) -> bytes:
    return bytes([header & 0xff, (header >> 8) & 0xff]) + b"".join(targets)


class TestParseTarget(unittest.TestCase):
    def test_zero_target(self):
        t = parse_target(pack_target(0, 4))
        self.assertEqual(t.target_id, 0)
        self.assertEqual(t.target_class, 4)
        self.assertEqual(t.range_x, 0.0)
        self.assertEqual(t.range_y, 0.0)
        self.assertEqual(t.length, 0.0)
        self.assertEqual(t.width, 0.0)
        self.assertEqual(t.speed_y, 0.0)
        self.assertEqual(t.speed_x, -64.0)  # default 0x80 sentinel
        self.assertEqual(t.class_name, "UNKNOWN")

    def test_range_y_positive_is_behind(self):
        # range_y_raw=100 -> 10.0 m behind the rider
        t = parse_target(pack_target(7, 23, range_y_raw=100))
        self.assertAlmostEqual(t.range_y, 10.0)
        self.assertTrue(t.is_behind)

    def test_range_y_long(self):
        # range_y_raw=2000 -> 200.0 m (representative long-range track)
        t = parse_target(pack_target(7, 23, range_y_raw=2000))
        self.assertAlmostEqual(t.range_y, 200.0)

    def test_range_y_max_positive(self):
        # 13-bit signed max = 4095 -> 409.5 m
        t = parse_target(pack_target(7, 23, range_y_raw=4095))
        self.assertAlmostEqual(t.range_y, 409.5)

    def test_range_y_negative_is_ahead(self):
        # range_y_raw=-50 -> -5.0 m (target has overtaken, ahead of rider)
        t = parse_target(pack_target(7, 23, range_y_raw=-50))
        self.assertAlmostEqual(t.range_y, -5.0)
        self.assertFalse(t.is_behind)

    def test_range_y_min_negative(self):
        # 13-bit signed min = -4096 -> -409.6 m
        t = parse_target(pack_target(7, 23, range_y_raw=-4096))
        self.assertAlmostEqual(t.range_y, -409.6)

    def test_range_x_positive(self):
        # range_x_raw=50 -> +5.0 m (target to the right)
        t = parse_target(pack_target(1, 16, range_x_raw=50))
        self.assertAlmostEqual(t.range_x, 5.0)

    def test_range_x_negative(self):
        # range_x_raw=-30 -> -3.0 m (target to the left)
        t = parse_target(pack_target(1, 16, range_x_raw=-30))
        self.assertAlmostEqual(t.range_x, -3.0)

    def test_range_x_max_positive(self):
        # 11-bit signed max = 1023 -> +102.3 m
        t = parse_target(pack_target(1, 16, range_x_raw=1023))
        self.assertAlmostEqual(t.range_x, 102.3)

    def test_range_x_min_negative(self):
        # 11-bit signed min = -1024 -> -102.4 m
        t = parse_target(pack_target(1, 16, range_x_raw=-1024))
        self.assertAlmostEqual(t.range_x, -102.4)

    def test_range_x_and_y_independent(self):
        # Both share the 24-bit word; setting both must decode cleanly.
        t = parse_target(pack_target(1, 23, range_x_raw=-1, range_y_raw=1))
        self.assertAlmostEqual(t.range_x, -0.1)
        self.assertAlmostEqual(t.range_y, 0.1)

    def test_length_width_scale(self):
        # length_raw=16 -> 4.0 m; width_raw=8 -> 2.0 m
        t = parse_target(pack_target(1, 23, length_raw=16, width_raw=8))
        self.assertEqual(t.length, 4.0)
        self.assertEqual(t.width, 2.0)

    def test_speed_y_approaching(self):
        # speed_y_raw=-10 -> -5.0 m/s (approaching at 5 m/s)
        t = parse_target(pack_target(1, 23, speed_y_raw=-10))
        self.assertEqual(t.speed_y, -5.0)

    def test_speed_y_receding(self):
        # speed_y_raw=+10 -> +5.0 m/s (falling behind)
        t = parse_target(pack_target(1, 23, speed_y_raw=10))
        self.assertEqual(t.speed_y, 5.0)

    def test_speed_y_min_max(self):
        # int8 range: -128..127 -> -64.0..+63.5 m/s
        self.assertEqual(parse_target(pack_target(1, 23, speed_y_raw=127)).speed_y, 63.5)
        self.assertEqual(parse_target(pack_target(1, 23, speed_y_raw=-128)).speed_y, -64.0)

    def test_speed_x_lateral(self):
        # speed_x_raw=20 -> +10.0 m/s lateral
        t = parse_target(pack_target(1, 23, speed_x_raw=20))
        self.assertEqual(t.speed_x, 10.0)

    def test_speed_x_no_lateral_sentinel(self):
        # 0x80 == -128 int8 -> -64.0 m/s, the "no lateral velocity" sentinel
        t = parse_target(pack_target(1, 23, speed_x_raw=NO_LATERAL))
        self.assertEqual(t.speed_x, -64.0)

    def test_class_name_lookup(self):
        for code, name in CLASS_NAMES.items():
            t = parse_target(pack_target(0, code))
            self.assertEqual(t.class_name, name)

    def test_class_name_unmapped(self):
        t = parse_target(pack_target(0, 99))
        self.assertEqual(t.class_name, "UNMAPPED(99)")

    def test_wrong_length_rejected(self):
        with self.assertRaises(ValueError):
            parse_target(b"\x00" * 8)
        with self.assertRaises(ValueError):
            parse_target(b"\x00" * 10)


class TestParseNotification(unittest.TestCase):
    def test_single_target(self):
        payload = pack_frame(
            0x0000,
            [pack_target(5, 23, range_x_raw=15, range_y_raw=456, speed_y_raw=-10)],
        )
        frame = parse_notification(payload)
        self.assertFalse(frame.is_status_frame)
        self.assertFalse(frame.is_device_status)
        self.assertEqual(len(frame.targets), 1)
        t = frame.targets[0]
        self.assertEqual(t.target_id, 5)
        self.assertEqual(t.class_name, "MODERATE")
        self.assertAlmostEqual(t.range_x, 1.5)
        self.assertAlmostEqual(t.range_y, 45.6)
        self.assertEqual(t.speed_y, -5.0)

    def test_multi_target(self):
        t1 = pack_target(1, 23, range_x_raw=-15, range_y_raw=100)   # MODERATE, -1.5 m, 10.0 m
        t2 = pack_target(2, 36, range_y_raw=512)                    # LARGE, 51.2 m
        t3 = pack_target(3, 13, range_x_raw=20, range_y_raw=306)    # FAINT_ALT, +2.0 m, 30.6 m
        frame = parse_notification(pack_frame(0x0000, [t1, t2, t3]))
        self.assertEqual(len(frame.targets), 3)
        self.assertEqual([t.target_id for t in frame.targets], [1, 2, 3])
        self.assertEqual(frame.targets[0].class_name, "MODERATE")
        self.assertEqual(frame.targets[1].class_name, "LARGE")
        self.assertEqual(frame.targets[2].class_name, "FAINT_ALT")
        self.assertAlmostEqual(frame.targets[0].range_x, -1.5)
        self.assertAlmostEqual(frame.targets[0].range_y, 10.0)
        self.assertAlmostEqual(frame.targets[1].range_y, 51.2)
        self.assertAlmostEqual(frame.targets[2].range_y, 30.6)

    def test_status_frame_skipped(self):
        payload = pack_frame(STATUS_FRAME_BIT, [b"\xff" * 9])
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
        payload = pack_frame(0x0000, [pack_target(1, 23, range_y_raw=100)]) + b"\xaa\xbb\xcc"
        frame = parse_notification(payload)
        self.assertEqual(len(frame.targets), 1)

    def test_short_notification_returns_empty(self):
        # Below the 2-byte header, the decoder returns an empty frame (no raise).
        self.assertEqual(parse_notification(b"\x01").targets, [])
        self.assertEqual(parse_notification(b"").targets, [])

    def test_header_preserved(self):
        frame = parse_notification(pack_frame(0x1234, []))
        self.assertEqual(frame.header, 0x1234)


class TestCaptureLineIteration(unittest.TestCase):
    def test_filters_non_3204(self):
        lines = [
            "# comment line",
            "",
            "1700000000000 3203 02",
            "1700000000100 3204 0000",
            "1700000000200 3204 0000" + pack_target(1, 23, range_y_raw=100).hex(),
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
        target_bytes = pack_target(9, 36, range_x_raw=-40, range_y_raw=818, speed_y_raw=-20)
        hex_payload = pack_frame(0x0000, [target_bytes]).hex()
        line = f"1700000005000 3204 {hex_payload}\n"
        fp = io.StringIO(line)
        entries = list(iter_3204_lines(fp))
        self.assertEqual(len(entries), 1)
        ts, payload = entries[0]
        frame = parse_notification(payload)
        self.assertEqual(len(frame.targets), 1)
        t = frame.targets[0]
        self.assertEqual(t.target_id, 9)
        self.assertEqual(t.class_name, "LARGE")
        self.assertAlmostEqual(t.range_x, -4.0)
        self.assertAlmostEqual(t.range_y, 81.8)
        self.assertEqual(t.speed_y, -10.0)


if __name__ == "__main__":
    unittest.main()
