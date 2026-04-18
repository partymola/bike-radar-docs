#!/usr/bin/env python3
"""Unit tests for decode_3203.parse_heartbeat / is_threat_packet / parse_threat."""
from __future__ import annotations

import io
import unittest

from decode_3203 import (
    is_sector_amplitude,
    is_threat_packet,
    iter_3203_lines,
    parse_heartbeat,
    parse_threat,
)


def pack_threat(seq_byte: int, triplets: list[tuple[int, int, int]]) -> bytes:
    body = bytearray([seq_byte & 0xFF])
    for vid, dist, flag in triplets:
        body.extend([vid & 0xFF, dist & 0xFF, flag & 0xFF])
    return bytes(body)


class TestHeartbeat(unittest.TestCase):
    def test_seq_zero(self):
        self.assertEqual(parse_heartbeat(b"\x02"), 0)

    def test_seq_f(self):
        self.assertEqual(parse_heartbeat(b"\xf2"), 0xF)

    def test_all_counters(self):
        for counter in range(16):
            payload = bytes([(counter << 4) | 0x02])
            self.assertEqual(parse_heartbeat(payload), counter)

    def test_wrong_low_nibble_rejected(self):
        with self.assertRaises(ValueError):
            parse_heartbeat(b"\x03")

    def test_wrong_length_rejected(self):
        with self.assertRaises(ValueError):
            parse_heartbeat(b"\x02\x00")
        with self.assertRaises(ValueError):
            parse_heartbeat(b"")


class TestIsThreatPacket(unittest.TestCase):
    def test_valid_single_vehicle(self):
        self.assertTrue(is_threat_packet(pack_threat(0x12, [(0x80, 10, 0)])))

    def test_valid_six_vehicles(self):
        payload = pack_threat(0x12, [(0x80 + i, 10, 0) for i in range(6)])
        self.assertEqual(len(payload), 19)
        self.assertTrue(is_threat_packet(payload))

    def test_heartbeat_not_threat(self):
        self.assertFalse(is_threat_packet(b"\x02"))

    def test_wrong_length_not_threat(self):
        self.assertFalse(is_threat_packet(b"\x12\x80"))
        self.assertFalse(is_threat_packet(b"\x12\x80\x10"))
        self.assertFalse(is_threat_packet(b"\x12\x80\x10\x00\x81"))

    def test_sector_amplitude_not_threat(self):
        self.assertFalse(is_threat_packet(b"\x06\x30\x00\x00\x00\xaa"))

    def test_wrong_low_nibble_not_threat(self):
        self.assertFalse(is_threat_packet(b"\x13\x80\x10\x00"))


class TestIsSectorAmplitude(unittest.TestCase):
    def test_six_byte_with_0x06_tag(self):
        self.assertTrue(is_sector_amplitude(b"\x06\x30\x00\x00\x00\xaa"))

    def test_wrong_length(self):
        self.assertFalse(is_sector_amplitude(b"\x06\x30\x00\x00\x00"))
        self.assertFalse(is_sector_amplitude(b"\x06\x30\x00\x00\x00\xaa\xbb"))

    def test_wrong_tag(self):
        self.assertFalse(is_sector_amplitude(b"\x02\x30\x00\x00\x00\xaa"))


class TestParseThreat(unittest.TestCase):
    def test_single_valid_vehicle(self):
        seq, vehicles = parse_threat(pack_threat(0x12, [(0x80, 25, 0)]))
        self.assertEqual(seq, 0x12)
        self.assertEqual(vehicles, [(0, 25, 0)])

    def test_multi_vehicle(self):
        seq, vehicles = parse_threat(pack_threat(0x42, [
            (0x81, 50, 0),
            (0x82, 45, 1),
            (0x83, 40, 0),
        ]))
        self.assertEqual(seq, 0x42)
        self.assertEqual(vehicles, [(1, 50, 0), (2, 45, 1), (3, 40, 0)])

    def test_vid_without_bit7_filtered(self):
        _, vehicles = parse_threat(pack_threat(0x12, [(0x7F, 10, 0), (0x81, 20, 0)]))
        self.assertEqual(vehicles, [(1, 20, 0)])

    def test_vid_fd_filtered(self):
        _, vehicles = parse_threat(pack_threat(0x12, [(0xFD, 0, 0), (0x80, 30, 0)]))
        self.assertEqual(vehicles, [(0, 30, 0)])

    def test_vid_zero_filtered(self):
        _, vehicles = parse_threat(pack_threat(0x12, [(0x00, 0, 0), (0x81, 30, 0)]))
        self.assertEqual(vehicles, [(1, 30, 0)])

    def test_dist_ff_filtered(self):
        _, vehicles = parse_threat(pack_threat(0x12, [(0xFF, 0xFF, 0), (0x80, 30, 0)]))
        self.assertEqual(vehicles, [(0, 30, 0)])

    def test_track_id_is_vid_mod_0x80(self):
        # vid 0xA5 (bit 7 set, low 7 bits = 0x25) -> track id 0x25
        _, vehicles = parse_threat(pack_threat(0x12, [(0xA5, 10, 0)]))
        self.assertEqual(vehicles, [(0x25, 10, 0)])

    def test_flag_byte_preserved(self):
        _, vehicles = parse_threat(pack_threat(0x12, [(0x80, 10, 0x01)]))
        self.assertEqual(vehicles, [(0, 10, 1)])

    def test_empty_after_filtering(self):
        # All three filtered: 0x00, 0xFD, 0xFF/0xFF
        _, vehicles = parse_threat(pack_threat(0x12, [
            (0x00, 0, 0),
            (0xFD, 0, 0),
            (0xFF, 0xFF, 0),
        ]))
        self.assertEqual(vehicles, [])

    def test_heartbeat_rejected(self):
        with self.assertRaises(ValueError):
            parse_threat(b"\x02")

    def test_sector_amp_rejected(self):
        with self.assertRaises(ValueError):
            parse_threat(b"\x06\x30\x00\x00\x00\xaa")

    def test_wrong_seq_nibble_rejected(self):
        # byte[0] & 0x0F != 0x02
        with self.assertRaises(ValueError):
            parse_threat(b"\x13\x80\x10\x00")


class TestCaptureLineIteration(unittest.TestCase):
    def test_filters_non_3203(self):
        lines = [
            "# comment",
            "",
            "1700000000000 3203 02",
            "1700000000100 3204 0000",
            "1700000000200 3203 1280190000190000",
            "1700000000300 2a19 5a",
            "malformed line",
            "1700000000400 3203 notahexstring",
        ]
        out = list(iter_3203_lines(io.StringIO("\n".join(lines))))
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0], (1700000000000, b"\x02"))
        self.assertEqual(out[1][0], 1700000000200)
        self.assertEqual(len(out[1][1]), 8)


if __name__ == "__main__":
    unittest.main()
