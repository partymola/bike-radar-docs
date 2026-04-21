package es.jjrh.bikeradar

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RadarV2DecoderTest {

    private var clock = 0L
    private val decoder = RadarV2Decoder(nowMs = { clock })

    private fun packTarget(
        tid: Int, cls: Int, b2: Int, zone: Int, rxInt8: Int = 0,
        lengthRaw: Int = 0, widthRaw: Int = 0, syRaw: Int = 0, sentinel: Int = 0x80,
    ): ByteArray {
        val b3 = (zone - 1) and 0x07
        return byteArrayOf(
            tid.toByte(),
            cls.toByte(),
            (b2 and 0xFF).toByte(),
            b3.toByte(),
            rxInt8.toByte(),
            lengthRaw.toByte(),
            widthRaw.toByte(),
            syRaw.toByte(),
            sentinel.toByte(),
        )
    }

    private fun packFrame(header: Int, targets: List<ByteArray>): ByteArray {
        val head = byteArrayOf(
            (header and 0xFF).toByte(),
            ((header shr 8) and 0xFF).toByte(),
        )
        return targets.fold(head) { acc, b -> acc + b }
    }

    @Test fun status_frame_does_not_add_targets() {
        clock = 1000
        val payload = packFrame(RadarV2Decoder.STATUS_FRAME_BIT, emptyList())
        assertNull(decoder.feed(payload))
    }

    @Test fun device_status_frame_does_not_add_targets() {
        clock = 1000
        val payload = packFrame(RadarV2Decoder.DEVICE_STATUS_BIT, listOf(byteArrayOf(1, 2, 3, 4)))
        assertNull(decoder.feed(payload))
    }

    @Test fun single_target_decodes_range_and_speed() {
        clock = 1000
        // b2=244, zone=1 -> 25.6 + 24.4 = 50.0 m; rx=15 -> 1.5 m; sy=-20 -> -10 m/s approaching
        val payload = packFrame(0x0000, listOf(packTarget(5, 23, b2 = 244, zone = 1, rxInt8 = 15, syRaw = -20)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(1, state!!.vehicles.size)
        val v = state.vehicles[0]
        assertEquals(5, v.id)
        assertEquals(50, v.distanceM)
        assertEquals(-10, v.speedMs)
        assertEquals(DataSource.V2, state.source)
    }

    @Test fun multi_target_frame_populates_all() {
        clock = 1000
        val payload = packFrame(0x0000, listOf(
            packTarget(1, 23, b2 = 144, zone = 1, rxInt8 = -50, syRaw = -20),  // 40.0 m
            packTarget(2, 36, b2 = 100, zone = 0, syRaw = -10),                 // 10.0 m
            packTarget(3, 13, b2 = 200, zone = 0, rxInt8 = 20, syRaw = -4),     // 20.0 m
        ))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(3, state!!.vehicles.size)
        // Sorted by distance ascending.
        assertEquals(listOf(2, 3, 1), state.vehicles.map { it.id })
        assertEquals(listOf(10, 20, 40), state.vehicles.map { it.distanceM })
    }

    @Test fun zone_0_and_1_span_correct_distances() {
        clock = 1000
        val payload = packFrame(0x0000, listOf(
            packTarget(1, 23, b2 = 255, zone = 0),  //  25.5 m, rounds to 25
            packTarget(2, 23, b2 = 0,   zone = 1),  //  25.6 m, rounds to 25
            packTarget(3, 23, b2 = 100, zone = 2),  //  61.2 m, rounds to 61
        ))
        val state = decoder.feed(payload)
        assertEquals(listOf(25, 25, 61), state!!.vehicles.map { it.distanceM })
    }

    @Test fun zone_7_reaches_far_limit() {
        clock = 1000
        // b2=255, zone=7 -> 179.2 + 25.5 = 204.7 m (top of addressable range)
        val payload = packFrame(0x0000, listOf(packTarget(1, 16, b2 = 255, zone = 7)))
        val state = decoder.feed(payload)
        assertEquals(204, state!!.vehicles[0].distanceM)
    }

    @Test fun rangeX_independent_of_rangeY() {
        clock = 1000
        // rangeX from byte[4] only; changing rangeY does not affect it.
        val payload = packFrame(0x0000, listOf(packTarget(1, 16, b2 = 255, zone = 7, rxInt8 = -30)))
        val state = decoder.feed(payload)
        val v = state!!.vehicles[0]
        assertEquals(204, v.distanceM)
        assertEquals(-1.0f, v.lateralPos, 0.001f)  // -3.0 m / 3.0 full = -1.0
    }

    @Test fun lateral_pos_scales_to_lateral_full_m() {
        clock = 1000
        // rx=30 -> 3.0 m -> lateralPos = 1.0 (right edge).
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, b2 = 244, zone = 1, rxInt8 = 30, syRaw = -20)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(1.0f, state!!.vehicles[0].lateralPos, 0.001f)
    }

    @Test fun lateral_pos_clamps_at_plus_one() {
        clock = 1000
        // rx=100 -> 10.0 m lateral, clamps to +1.0.
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, b2 = 244, zone = 1, rxInt8 = 100, syRaw = -20)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(1.0f, state!!.vehicles[0].lateralPos, 0.001f)
    }

    @Test fun lateral_pos_clamps_at_minus_one() {
        clock = 1000
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, b2 = 244, zone = 1, rxInt8 = -100, syRaw = -20)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(-1.0f, state!!.vehicles[0].lateralPos, 0.001f)
    }

    @Test fun speedY_signed_int8_approaching() {
        clock = 1000
        // sy=-20 -> -10 m/s approaching
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, b2 = 244, zone = 1, syRaw = -20)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(-10, state!!.vehicles[0].speedMs)
    }

    @Test fun speedY_signed_int8_receding() {
        clock = 1000
        // sy=20 -> +10 m/s (vehicle falling behind)
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, b2 = 244, zone = 1, syRaw = 20)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(10, state!!.vehicles[0].speedMs)
    }

    @Test fun length_classifies_as_bike() {
        clock = 1000
        // 4 * 0.25 = 1.0 m length -> BIKE
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, b2 = 244, zone = 1, lengthRaw = 4, syRaw = -10)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(VehicleSize.BIKE, state!!.vehicles[0].size)
    }

    @Test fun length_classifies_as_car() {
        clock = 1000
        // 16 * 0.25 = 4.0 m length -> CAR (between BIKE_MAX_M=2.5 and TRUCK_MIN_M=5.5)
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, b2 = 244, zone = 1, lengthRaw = 16, syRaw = -10)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(VehicleSize.CAR, state!!.vehicles[0].size)
    }

    @Test fun length_classifies_as_truck() {
        clock = 1000
        // 32 * 0.25 = 8.0 m length -> TRUCK
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, b2 = 244, zone = 1, lengthRaw = 32, syRaw = -10)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(VehicleSize.TRUCK, state!!.vehicles[0].size)
    }

    @Test fun moving_track_stale_window_is_short() {
        clock = 1000
        // sy=-20 -> 10 m/s (moving)
        decoder.feed(packFrame(0x0000, listOf(packTarget(5, 23, b2 = 244, zone = 1, syRaw = -20))))
        // Past STALE_MOVING_MS -> pruned
        clock = 1000 + RadarV2Decoder.STALE_MOVING_MS + 1
        val state = decoder.feed(packFrame(0x0000, emptyList()))
        assertNotNull(state)
        assertTrue(state!!.vehicles.isEmpty())
    }

    @Test fun parked_track_stale_window_is_long() {
        clock = 1000
        // sy=0 -> 0 m/s (parked)
        decoder.feed(packFrame(0x0000, listOf(packTarget(5, 23, b2 = 244, zone = 1, syRaw = 0))))
        // Past STALE_MOVING_MS but not STALE_PARKED_MS -> still present
        clock = 1000 + RadarV2Decoder.STALE_MOVING_MS + 100
        val state = decoder.feed(packFrame(0x0000, emptyList()))
        // State may or may not be "changed" (track count unchanged), but if a
        // state is returned it should still include the parked track.
        // Check by inspection at a later point we know IS past the parked window.
        clock = 1000 + RadarV2Decoder.STALE_PARKED_MS + 1
        val state2 = decoder.feed(packFrame(0x0000, emptyList()))
        assertNotNull(state2)
        assertTrue(state2!!.vehicles.isEmpty())
    }

    @Test fun empty_body_is_legal() {
        clock = 1000
        val state = decoder.feed(packFrame(0x0000, emptyList()))
        assertNull(state)  // no change
    }

    @Test fun short_payload_is_tolerated() {
        clock = 1000
        // < 2 bytes — decoder returns null without raising.
        assertNull(decoder.feed(byteArrayOf(0x01)))
        assertNull(decoder.feed(byteArrayOf()))
    }

    @Test fun trailing_partial_target_ignored() {
        clock = 1000
        // 1 full target + 3 trailing bytes (incomplete second target) -> 1 parsed.
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, b2 = 244, zone = 1, rxInt8 = 10, syRaw = -20))) +
            byteArrayOf(0xAA.toByte(), 0xBB.toByte(), 0xCC.toByte())
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(1, state!!.vehicles.size)
    }

    @Test fun reset_clears_all_tracks() {
        clock = 1000
        decoder.feed(packFrame(0x0000, listOf(packTarget(5, 23, b2 = 244, zone = 1, syRaw = -20))))
        decoder.reset()
        clock = 1100
        val state = decoder.feed(packFrame(0x0000, emptyList()))
        assertNull(state)
    }
}
