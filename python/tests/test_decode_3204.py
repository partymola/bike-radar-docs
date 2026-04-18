#!/usr/bin/env python3
"""Unit tests for decode_3204.parse_target / parse_notification.

Hand-packs synthetic frames covering signed bit-packing corner cases
(rangeX/rangeY boundaries, negative speeds), multi-target notifications,
status frames, and device-status frames. No real capture required.
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
    Frame,
    iter_3204_lines,
    parse_notification,
    parse_target,
)


def pack_target(tid: int, cls: int, rx_raw: int, ry_raw: int,
                length_raw: int = 0, width_raw: int = 0,
                sx_raw: int = 0, sy_raw: int = 0) -> bytes:
    """Pack a 9-byte target struct from raw (pre-scaling) values.

    rx_raw is 11-bit signed, ry_raw is 13-bit signed, speeds are 8-bit signed.
    length_raw / width_raw are unsigned bytes (decoder scales by 0.25).
    """
    r24 = ((ry_raw & 0x1FFF) << 11) | (rx_raw & 0x07FF)
    return bytes([
        tid & 0xff,
        cls & 0xff,
        (r24 >> 16) & 0xff,
        (r24 >> 8) & 0xff,
        r24 & 0xff,
        length_raw & 0xff,
        width_raw & 0xff,
        sx_raw & 0xff,
        sy_raw & 0xff,
    ])


def pack_frame(header: int, targets: list[bytes]) -> bytes:
    return bytes([header & 0xff, (header >> 8) & 0xff]) + b"".join(targets)


class TestParseTarget(unittest.TestCase):
    def test_zero_target(self):
        t = parse_target(pack_target(0, 4, 0, 0))
        self.assertEqual(t.target_id, 0)
        self.assertEqual(t.target_class, 4)
        self.assertEqual(t.range_x, 0.0)
        self.assertEqual(t.range_y, 0.0)
        self.assertEqual(t.length, 0.0)
        self.assertEqual(t.width, 0.0)
        self.assertEqual(t.speed_x, 0.0)
        self.assertEqual(t.speed_y, 0.0)
        self.assertEqual(t.class_name, "UNKNOWN")

    def test_positive_ranges(self):
        # rx_raw=100 -> 10.0 m lateral; ry_raw=500 -> 50.0 m longitudinal
        t = parse_target(pack_target(7, 23, 100, 500))
        self.assertEqual(t.target_id, 7)
        self.assertEqual(t.class_name, "NORMAL")
        self.assertAlmostEqual(t.range_x, 10.0)
        self.assertAlmostEqual(t.range_y, 50.0)

    def test_negative_rangex(self):
        # rx_raw = -100 -> lateral -10.0 m (vehicle to the left)
        t = parse_target(pack_target(1, 16, -100, 500))
        self.assertAlmostEqual(t.range_x, -10.0)
        self.assertAlmostEqual(t.range_y, 50.0)

    def test_negative_rangey(self):
        t = parse_target(pack_target(1, 16, 0, -200))
        self.assertAlmostEqual(t.range_y, -20.0)
        self.assertAlmostEqual(t.range_x, 0.0)

    def test_rangex_max_positive(self):
        # 11-bit signed max = 1023 -> +102.3 m
        t = parse_target(pack_target(1, 16, 1023, 0))
        self.assertAlmostEqual(t.range_x, 102.3)

    def test_rangex_min_negative(self):
        # 11-bit signed min = -1024 -> -102.4 m
        t = parse_target(pack_target(1, 16, -1024, 0))
        self.assertAlmostEqual(t.range_x, -102.4)

    def test_rangey_max_positive(self):
        # 13-bit signed max = 4095 -> +409.5 m
        t = parse_target(pack_target(1, 16, 0, 4095))
        self.assertAlmostEqual(t.range_y, 409.5)

    def test_rangey_min_negative(self):
        # 13-bit signed min = -4096 -> -409.6 m
        t = parse_target(pack_target(1, 16, 0, -4096))
        self.assertAlmostEqual(t.range_y, -409.6)

    def test_rangex_and_rangey_independent(self):
        # Both fields non-zero; confirm no bleed between the 11-bit and 13-bit slots.
        t = parse_target(pack_target(1, 16, -1024, 4095))
        self.assertAlmostEqual(t.range_x, -102.4)
        self.assertAlmostEqual(t.range_y, 409.5)

    def test_length_width_scale(self):
        # length_raw=16 -> 4.0 m; width_raw=8 -> 2.0 m
        t = parse_target(pack_target(1, 23, 0, 0, length_raw=16, width_raw=8))
        self.assertEqual(t.length, 4.0)
        self.assertEqual(t.width, 2.0)

    def test_speed_positive(self):
        # sx=10 -> +5.0 m/s; sy=20 -> +10.0 m/s
        t = parse_target(pack_target(1, 23, 0, 0, sx_raw=10, sy_raw=20))
        self.assertEqual(t.speed_x, 5.0)
        self.assertEqual(t.speed_y, 10.0)

    def test_speed_negative(self):
        # sx=-10 (two's complement) -> -5.0; sy=-20 -> -10.0
        t = parse_target(pack_target(1, 23, 0, 0, sx_raw=-10, sy_raw=-20))
        self.assertEqual(t.speed_x, -5.0)
        self.assertEqual(t.speed_y, -10.0)

    def test_speed_min_max(self):
        # int8 range: -128..127 -> -64.0..+63.5 m/s
        t = parse_target(pack_target(1, 23, 0, 0, sx_raw=127, sy_raw=-128))
        self.assertEqual(t.speed_x, 63.5)
        self.assertEqual(t.speed_y, -64.0)

    def test_class_name_lookup(self):
        for code, name in CLASS_NAMES.items():
            t = parse_target(pack_target(0, code, 0, 0))
            self.assertEqual(t.class_name, name)

    def test_class_name_unknown(self):
        t = parse_target(pack_target(0, 99, 0, 0))
        self.assertEqual(t.class_name, "UNKNOWN(99)")

    def test_wrong_length_rejected(self):
        with self.assertRaises(ValueError):
            parse_target(b"\x00" * 8)
        with self.assertRaises(ValueError):
            parse_target(b"\x00" * 10)


class TestParseNotification(unittest.TestCase):
    def test_single_target(self):
        payload = pack_frame(0x0000, [pack_target(5, 23, 50, 300, sx_raw=2, sy_raw=20)])
        frame = parse_notification(payload)
        self.assertFalse(frame.is_status_frame)
        self.assertFalse(frame.is_device_status)
        self.assertEqual(len(frame.targets), 1)
        t = frame.targets[0]
        self.assertEqual(t.target_id, 5)
        self.assertAlmostEqual(t.range_x, 5.0)
        self.assertAlmostEqual(t.range_y, 30.0)
        self.assertEqual(t.speed_y, 10.0)

    def test_multi_target(self):
        t1 = pack_target(1, 23, -50, 400)
        t2 = pack_target(2, 36, 0, 100)
        t3 = pack_target(3, 13, 20, 200)
        frame = parse_notification(pack_frame(0x0000, [t1, t2, t3]))
        self.assertEqual(len(frame.targets), 3)
        ids = [t.target_id for t in frame.targets]
        self.assertEqual(ids, [1, 2, 3])
        self.assertEqual(frame.targets[0].class_name, "NORMAL")
        self.assertEqual(frame.targets[1].class_name, "HIGH")
        self.assertEqual(frame.targets[2].class_name, "LOW_STABLE")
        self.assertAlmostEqual(frame.targets[0].range_x, -5.0)
        self.assertAlmostEqual(frame.targets[0].range_y, 40.0)

    def test_status_frame_skipped(self):
        # bit 0 set -> decoder should NOT attempt to parse the body as targets
        garbage = b"\xff" * 9
        payload = pack_frame(STATUS_FRAME_BIT, [garbage])
        frame = parse_notification(payload)
        self.assertTrue(frame.is_status_frame)
        self.assertEqual(frame.targets, [])

    def test_device_status_frame(self):
        # bit 2 set -> flagged but no targets parsed
        payload = pack_frame(DEVICE_STATUS_BIT, [b"\x01\x02\x03\x04"])
        frame = parse_notification(payload)
        self.assertTrue(frame.is_device_status)
        self.assertEqual(frame.targets, [])

    def test_empty_body(self):
        # Header only, no targets — legal (e.g. no vehicles in view)
        frame = parse_notification(pack_frame(0x0000, []))
        self.assertEqual(frame.targets, [])
        self.assertFalse(frame.is_status_frame)
        self.assertFalse(frame.is_device_status)

    def test_body_with_trailing_bytes(self):
        # 1 target + 3 trailing bytes -> trailing bytes ignored, one target returned
        payload = pack_frame(0x0000, [pack_target(1, 23, 10, 100)]) + b"\xaa\xbb\xcc"
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
            "1700000000000 3203 02",           # legacy heartbeat
            "1700000000100 3204 0000",          # empty 3204 frame
            "1700000000200 3204 0000" + pack_target(1, 23, 10, 100).hex(),
            "1700000000300 2a19 5a",            # unrelated char (battery)
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
        target_bytes = pack_target(9, 36, -512, 1500, sx_raw=-4, sy_raw=40)
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
        self.assertAlmostEqual(t.range_x, -51.2)
        self.assertAlmostEqual(t.range_y, 150.0)
        self.assertEqual(t.speed_x, -2.0)
        self.assertEqual(t.speed_y, 20.0)


if __name__ == "__main__":
    unittest.main()
