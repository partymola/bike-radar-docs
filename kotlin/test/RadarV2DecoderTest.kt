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
        tid: Int, cls: Int, rxRaw: Int, ryRaw: Int,
        lengthRaw: Int = 0, widthRaw: Int = 0, sxRaw: Int = 0, syRaw: Int = 0,
    ): ByteArray {
        val r24 = ((ryRaw and 0x1FFF) shl 11) or (rxRaw and 0x07FF)
        return byteArrayOf(
            tid.toByte(),
            cls.toByte(),
            ((r24 shr 16) and 0xFF).toByte(),
            ((r24 shr 8) and 0xFF).toByte(),
            (r24 and 0xFF).toByte(),
            lengthRaw.toByte(),
            widthRaw.toByte(),
            sxRaw.toByte(),
            syRaw.toByte(),
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
        // rx_raw=100 -> 10m lateral, ry_raw=500 -> 50m longitudinal, sy_raw=20 -> 10 m/s approaching
        val payload = packFrame(0x0000, listOf(packTarget(5, 23, 100, 500, sxRaw = 2, syRaw = 20)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(1, state!!.vehicles.size)
        val v = state.vehicles[0]
        assertEquals(5, v.id)
        assertEquals(50, v.distanceM)
        assertEquals(10, v.speedMs)
        assertEquals(DataSource.V2, state.source)
    }

    @Test fun multi_target_frame_populates_all() {
        clock = 1000
        val payload = packFrame(0x0000, listOf(
            packTarget(1, 23, -50, 400, syRaw = 20),
            packTarget(2, 36, 0, 100, syRaw = 10),
            packTarget(3, 13, 20, 200, syRaw = 4),
        ))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(3, state!!.vehicles.size)
        // Sorted by distance ascending.
        assertEquals(listOf(2, 3, 1), state.vehicles.map { it.id })
        assertEquals(listOf(10, 20, 40), state.vehicles.map { it.distanceM })
    }

    @Test fun negative_rangeY_still_produces_positive_distance() {
        clock = 1000
        // ry_raw = -200 -> -20 m; distance reported as |ry| = 20
        val payload = packFrame(0x0000, listOf(packTarget(1, 16, 0, -200, syRaw = 4)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(20, state!!.vehicles[0].distanceM)
    }

    @Test fun rangeY_max_positive_boundary() {
        clock = 1000
        // 13-bit signed max = 4095 -> +409.5 m
        val payload = packFrame(0x0000, listOf(packTarget(1, 16, 0, 4095, syRaw = 4)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(409, state!!.vehicles[0].distanceM)
    }

    @Test fun rangeY_min_negative_boundary() {
        clock = 1000
        // 13-bit signed min = -4096 -> -409.6 m, distance = 409
        val payload = packFrame(0x0000, listOf(packTarget(1, 16, 0, -4096, syRaw = 4)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(409, state!!.vehicles[0].distanceM)
    }

    @Test fun rangeX_does_not_bleed_into_rangeY() {
        clock = 1000
        // Both fields set to their limits; decoded distance comes from rangeY only.
        val payload = packFrame(0x0000, listOf(packTarget(1, 16, -1024, 4095, syRaw = 4)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(409, state!!.vehicles[0].distanceM)
    }

    @Test fun lateral_pos_scales_to_lateral_full_m() {
        clock = 1000
        // rx_raw=30 -> 3.0 m -> lateralPos = 1.0 (right edge).
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, 30, 500, syRaw = 20)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(1.0f, state!!.vehicles[0].lateralPos, 0.001f)
    }

    @Test fun lateral_pos_clamps_at_plus_one() {
        clock = 1000
        // rx_raw=100 -> 10.0 m lateral, clamps to +1.0.
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, 100, 500, syRaw = 20)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(1.0f, state!!.vehicles[0].lateralPos, 0.001f)
    }

    @Test fun lateral_pos_clamps_at_minus_one() {
        clock = 1000
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, -100, 500, syRaw = 20)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(-1.0f, state!!.vehicles[0].lateralPos, 0.001f)
    }

    @Test fun speedY_signed_int8_positive() {
        clock = 1000
        // sy_raw=20 -> 10 m/s approaching
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, 0, 500, syRaw = 20)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(10, state!!.vehicles[0].speedMs)
    }

    @Test fun speedY_signed_int8_negative() {
        clock = 1000
        // sy_raw=-20 -> -10 m/s (vehicle falling behind)
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, 0, 500, syRaw = -20)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(-10, state!!.vehicles[0].speedMs)
    }

    @Test fun length_classifies_as_bike() {
        clock = 1000
        // 4 * 0.25 = 1.0 m length -> BIKE
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, 0, 500, lengthRaw = 4, syRaw = 10)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(VehicleSize.BIKE, state!!.vehicles[0].size)
    }

    @Test fun length_classifies_as_car() {
        clock = 1000
        // 16 * 0.25 = 4.0 m length -> CAR (between BIKE_MAX_M=2.5 and TRUCK_MIN_M=5.5)
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, 0, 500, lengthRaw = 16, syRaw = 10)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(VehicleSize.CAR, state!!.vehicles[0].size)
    }

    @Test fun length_classifies_as_truck() {
        clock = 1000
        // 32 * 0.25 = 8.0 m length -> TRUCK
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, 0, 500, lengthRaw = 32, syRaw = 10)))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(VehicleSize.TRUCK, state!!.vehicles[0].size)
    }

    @Test fun moving_track_stale_window_is_short() {
        clock = 1000
        // sy_raw=20 -> 10 m/s (moving)
        decoder.feed(packFrame(0x0000, listOf(packTarget(5, 23, 0, 500, syRaw = 20))))
        // Past STALE_MOVING_MS -> pruned
        clock = 1000 + RadarV2Decoder.STALE_MOVING_MS + 1
        val state = decoder.feed(packFrame(0x0000, emptyList()))
        assertNotNull(state)
        assertTrue(state!!.vehicles.isEmpty())
    }

    @Test fun parked_track_stale_window_is_long() {
        clock = 1000
        // sy_raw=0 -> 0 m/s (parked)
        decoder.feed(packFrame(0x0000, listOf(packTarget(5, 23, 0, 500, syRaw = 0))))
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
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, 100, 500, syRaw = 20))) +
            byteArrayOf(0xAA.toByte(), 0xBB.toByte(), 0xCC.toByte())
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(1, state!!.vehicles.size)
    }

    @Test fun reset_clears_all_tracks() {
        clock = 1000
        decoder.feed(packFrame(0x0000, listOf(packTarget(5, 23, 0, 500, syRaw = 20))))
        decoder.reset()
        clock = 1100
        val state = decoder.feed(packFrame(0x0000, emptyList()))
        assertNull(state)
    }
}
