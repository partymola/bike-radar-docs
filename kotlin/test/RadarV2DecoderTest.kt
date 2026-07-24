package es.jjrh.bikeradar

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RadarV2DecoderTest {

    private var clock = 0L
    private val decoder = RadarV2Decoder(nowMs = { clock })

    /**
     * Pack a 9-byte target struct. rangeXRaw (11-bit signed) and rangeYRaw
     * (13-bit signed) are packed into the 24-bit little-endian word at bytes
     * [2..4]; the decoder multiplies each by 0.1 m. speedYRaw is a signed int8
     * (x0.5 m/s). speedXRaw defaults to the 0x80 "no lateral" sentinel and is
     * unused by this decoder.
     */
    private fun packTarget(
        tid: Int, cls: Int, rangeXRaw: Int = 0, rangeYRaw: Int = 0,
        lengthRaw: Int = 0, widthRaw: Int = 0, speedYRaw: Int = 0, speedXRaw: Int = 0x80,
    ): ByteArray {
        val packed = (rangeXRaw and 0x07FF) or ((rangeYRaw and 0x1FFF) shl 11)
        return byteArrayOf(
            tid.toByte(),
            cls.toByte(),
            (packed and 0xFF).toByte(),
            ((packed shr 8) and 0xFF).toByte(),
            ((packed shr 16) and 0xFF).toByte(),
            lengthRaw.toByte(),
            widthRaw.toByte(),
            speedYRaw.toByte(),
            speedXRaw.toByte(),
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

    @Test fun status_frame_prunes_stale_tracks() {
        clock = 1000
        decoder.feed(packFrame(0x0000, listOf(packTarget(5, 23, rangeYRaw = 100, speedYRaw = -20))))
        clock = 1000 + RadarV2Decoder.STALE_MOVING_MS + 1
        // A status frame arriving after tracks exist still prunes the stale one.
        val state = decoder.feed(packFrame(RadarV2Decoder.STATUS_FRAME_BIT, emptyList()))
        assertNotNull(state)
        assertTrue(state!!.vehicles.isEmpty())
    }

    @Test fun single_target_decodes_range_and_speed() {
        clock = 1000
        // rangeY 500 -> 50.0 m; rangeX 15 -> 1.5 m; speedY -20 -> -10 m/s approaching
        val payload = packFrame(0x0000, listOf(packTarget(5, 23, rangeXRaw = 15, rangeYRaw = 500, speedYRaw = -20)))
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
            packTarget(1, 23, rangeYRaw = 400, speedYRaw = -20),  // 40.0 m
            packTarget(2, 36, rangeYRaw = 100, speedYRaw = -10),  // 10.0 m
            packTarget(3, 13, rangeYRaw = 200, speedYRaw = -8),   // 20.0 m
        ))
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(3, state!!.vehicles.size)
        // Sorted by distance ascending.
        assertEquals(listOf(2, 3, 1), state.vehicles.map { it.id })
        assertEquals(listOf(10, 20, 40), state.vehicles.map { it.distanceM })
    }

    @Test fun range_y_decodes_near_and_far() {
        clock = 1000
        val near = decoder.feed(packFrame(0x0000, listOf(packTarget(1, 23, rangeYRaw = 100))))
        assertEquals(10, near!!.vehicles[0].distanceM)  // 10.0 m
        decoder.reset()
        val far = decoder.feed(packFrame(0x0000, listOf(packTarget(1, 23, rangeYRaw = 2047))))
        assertEquals(204, far!!.vehicles[0].distanceM)  // 204.7 m -> 204
    }

    @Test fun range_y_negative_is_sign_extended() {
        clock = 1000
        // A target ahead of the rider: rangeY -50 -> -5.0 m (drives the 13-bit
        // sign-extension). distanceM uses abs(), so it reads 5.
        val state = decoder.feed(packFrame(0x0000, listOf(packTarget(1, 23, rangeYRaw = -50))))
        assertEquals(5, state!!.vehicles[0].distanceM)
    }

    @Test fun rangeX_independent_of_rangeY() {
        clock = 1000
        // rangeX and rangeY share the 24-bit word; both must decode cleanly.
        val payload = packFrame(0x0000, listOf(packTarget(1, 16, rangeXRaw = -30, rangeYRaw = 2047)))
        val state = decoder.feed(payload)
        val v = state!!.vehicles[0]
        assertEquals(204, v.distanceM)               // rangeY 204.7 unaffected by rangeX
        assertEquals(-1.0f, v.lateralPos, 0.001f)    // rangeX -3.0 m -> -1.0 full-left
    }

    @Test fun lateral_pos_scales_to_lateral_full_m() {
        clock = 1000
        // rangeX 30 -> 3.0 m -> lateralPos 1.0 (right edge, LATERAL_FULL_M = 3.0).
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, rangeXRaw = 30, rangeYRaw = 100)))
        val state = decoder.feed(payload)
        assertEquals(1.0f, state!!.vehicles[0].lateralPos, 0.001f)
    }

    @Test fun lateral_pos_clamps_at_plus_one() {
        clock = 1000
        // rangeX 100 -> 10.0 m lateral, clamps to +1.0.
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, rangeXRaw = 100, rangeYRaw = 100)))
        val state = decoder.feed(payload)
        assertEquals(1.0f, state!!.vehicles[0].lateralPos, 0.001f)
    }

    @Test fun lateral_pos_clamps_at_minus_one() {
        clock = 1000
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, rangeXRaw = -100, rangeYRaw = 100)))
        val state = decoder.feed(payload)
        assertEquals(-1.0f, state!!.vehicles[0].lateralPos, 0.001f)
    }

    @Test fun speedY_signed_int8_approaching() {
        clock = 1000
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, rangeYRaw = 100, speedYRaw = -20)))
        val state = decoder.feed(payload)
        assertEquals(-10, state!!.vehicles[0].speedMs)  // -20 * 0.5 = -10 m/s
    }

    @Test fun speedY_signed_int8_receding() {
        clock = 1000
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, rangeYRaw = 100, speedYRaw = 20)))
        val state = decoder.feed(payload)
        assertEquals(10, state!!.vehicles[0].speedMs)  // +20 * 0.5 = +10 m/s
    }

    @Test fun class_faint_is_bike() {
        clock = 1000
        for (cls in listOf(RadarV2Decoder.CLASS_FAINT, RadarV2Decoder.CLASS_FAINT_ALT)) {
            decoder.reset()
            val state = decoder.feed(packFrame(0x0000, listOf(packTarget(1, cls, rangeYRaw = 100))))
            assertEquals(VehicleSize.BIKE, state!!.vehicles[0].size)
        }
    }

    @Test fun class_large_is_truck() {
        clock = 1000
        val state = decoder.feed(packFrame(0x0000, listOf(packTarget(1, RadarV2Decoder.CLASS_LARGE, rangeYRaw = 100))))
        assertEquals(VehicleSize.TRUCK, state!!.vehicles[0].size)
    }

    @Test fun class_moderate_is_car() {
        clock = 1000
        for (cls in listOf(RadarV2Decoder.CLASS_MODERATE, RadarV2Decoder.CLASS_UNKNOWN)) {
            decoder.reset()
            val state = decoder.feed(packFrame(0x0000, listOf(packTarget(1, cls, rangeYRaw = 100))))
            assertEquals(VehicleSize.CAR, state!!.vehicles[0].size)
        }
    }

    @Test fun moving_track_stale_window_is_short() {
        clock = 1000
        // speedY -20 -> 10 m/s (moving)
        decoder.feed(packFrame(0x0000, listOf(packTarget(5, 23, rangeYRaw = 100, speedYRaw = -20))))
        // Past STALE_MOVING_MS -> pruned
        clock = 1000 + RadarV2Decoder.STALE_MOVING_MS + 1
        val state = decoder.feed(packFrame(0x0000, emptyList()))
        assertNotNull(state)
        assertTrue(state!!.vehicles.isEmpty())
    }

    @Test fun parked_track_stale_window_is_long() {
        clock = 1000
        // speedY 0 -> 0 m/s (parked)
        decoder.feed(packFrame(0x0000, listOf(packTarget(5, 23, rangeYRaw = 100, speedYRaw = 0))))
        // Past the moving window but not the parked window -> still present.
        clock = 1000 + RadarV2Decoder.STALE_MOVING_MS + 100
        decoder.feed(packFrame(0x0000, emptyList()))
        // Past the parked window -> pruned.
        clock = 1000 + RadarV2Decoder.STALE_PARKED_MS + 1
        val state = decoder.feed(packFrame(0x0000, emptyList()))
        assertNotNull(state)
        assertTrue(state!!.vehicles.isEmpty())
    }

    @Test fun empty_body_is_legal() {
        clock = 1000
        val state = decoder.feed(packFrame(0x0000, emptyList()))
        assertNull(state)  // no change
    }

    @Test fun short_payload_is_tolerated() {
        clock = 1000
        // < 2 bytes - decoder returns null without raising.
        assertNull(decoder.feed(byteArrayOf(0x01)))
        assertNull(decoder.feed(byteArrayOf()))
    }

    @Test fun trailing_partial_target_ignored() {
        clock = 1000
        // 1 full target + 3 trailing bytes (incomplete second target) -> 1 parsed.
        val payload = packFrame(0x0000, listOf(packTarget(1, 23, rangeYRaw = 100, speedYRaw = -20))) +
            byteArrayOf(0xAA.toByte(), 0xBB.toByte(), 0xCC.toByte())
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(1, state!!.vehicles.size)
    }

    @Test fun reset_clears_all_tracks() {
        clock = 1000
        decoder.feed(packFrame(0x0000, listOf(packTarget(5, 23, rangeYRaw = 100, speedYRaw = -20))))
        decoder.reset()
        clock = 1100
        val state = decoder.feed(packFrame(0x0000, emptyList()))
        assertNull(state)
    }
}
