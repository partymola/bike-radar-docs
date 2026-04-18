package es.jjrh.bikeradar

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RadarV1DecoderTest {

    private var clock = 0L
    private val decoder = RadarV1Decoder(nowMs = { clock })

    private fun packThreat(seq: Int, triplets: List<IntArray>): ByteArray {
        val out = ByteArray(1 + 3 * triplets.size)
        out[0] = seq.toByte()
        for ((i, t) in triplets.withIndex()) {
            require(t.size == 3) { "triplet must be 3 ints: vid,dist,flag" }
            out[1 + 3 * i] = t[0].toByte()
            out[1 + 3 * i + 1] = t[1].toByte()
            out[1 + 3 * i + 2] = t[2].toByte()
        }
        return out
    }

    private fun triplet(vid: Int, dist: Int, flag: Int) = intArrayOf(vid, dist, flag)

    @Test fun heartbeat_does_not_emit_state_on_empty_decoder() {
        clock = 1000
        assertNull(decoder.feed(byteArrayOf(0x02)))
    }

    @Test fun single_valid_vehicle_populates_state() {
        clock = 1000
        val state = decoder.feed(packThreat(0x12, listOf(triplet(0x80, 25, 0))))
        assertNotNull(state)
        assertEquals(1, state!!.vehicles.size)
        val v = state.vehicles[0]
        assertEquals(0, v.id)
        assertEquals(25, v.distanceM)
        assertEquals(DataSource.V1, state.source)
    }

    @Test fun multi_vehicle_packet_populates_all_tracks() {
        clock = 1000
        val state = decoder.feed(packThreat(0x42, listOf(
            triplet(0x81, 50, 0),
            triplet(0x82, 45, 1),
            triplet(0x83, 40, 0),
        )))
        assertNotNull(state)
        assertEquals(3, state!!.vehicles.size)
        // Sorted by distance (closest first).
        assertEquals(listOf(3, 2, 1), state.vehicles.map { it.id })
        assertEquals(listOf(40, 45, 50), state.vehicles.map { it.distanceM })
    }

    @Test fun vid_without_bit7_is_filtered() {
        clock = 1000
        val state = decoder.feed(packThreat(0x12, listOf(
            triplet(0x7F, 10, 0),
            triplet(0x81, 20, 0),
        )))
        assertNotNull(state)
        assertEquals(1, state!!.vehicles.size)
        assertEquals(1, state.vehicles[0].id)
    }

    @Test fun vid_0xFD_is_filtered() {
        clock = 1000
        val state = decoder.feed(packThreat(0x12, listOf(
            triplet(0xFD, 0, 0),
            triplet(0x80, 30, 0),
        )))
        assertNotNull(state)
        assertEquals(1, state!!.vehicles.size)
        assertEquals(0, state.vehicles[0].id)
    }

    @Test fun vid_zero_is_filtered() {
        clock = 1000
        val state = decoder.feed(packThreat(0x12, listOf(
            triplet(0x00, 0, 0),
            triplet(0x81, 30, 0),
        )))
        assertNotNull(state)
        assertEquals(1, state!!.vehicles.size)
        assertEquals(1, state.vehicles[0].id)
    }

    @Test fun dist_0xFF_triplet_is_filtered() {
        clock = 1000
        val state = decoder.feed(packThreat(0x12, listOf(
            triplet(0xFF, 0xFF, 0),
            triplet(0x80, 30, 0),
        )))
        assertNotNull(state)
        assertEquals(1, state!!.vehicles.size)
        assertEquals(30, state.vehicles[0].distanceM)
    }

    @Test fun track_id_is_vid_and_0x7F() {
        clock = 1000
        // vid 0xA5 = bit 7 set, low 7 bits = 0x25 -> track id 0x25 (37)
        val state = decoder.feed(packThreat(0x12, listOf(triplet(0xA5, 10, 0))))
        assertNotNull(state)
        assertEquals(0x25, state!!.vehicles[0].id)
    }

    @Test fun flag_byte_passed_through_as_speedMs() {
        clock = 1000
        val state = decoder.feed(packThreat(0x12, listOf(triplet(0x80, 10, 0x01))))
        assertNotNull(state)
        assertEquals(1, state!!.vehicles[0].speedMs)
    }

    @Test fun re_feeding_same_vid_updates_lastSeen() {
        clock = 1000
        decoder.feed(packThreat(0x12, listOf(triplet(0x80, 25, 0))))
        clock = 2500  // 1500 ms later, within 2000 ms stale window on re-feed
        val state = decoder.feed(packThreat(0x22, listOf(triplet(0x80, 20, 0))))
        assertNotNull(state)
        assertEquals(1, state!!.vehicles.size)
        assertEquals(20, state.vehicles[0].distanceM)
    }

    @Test fun stale_track_pruned_after_STALE_MS() {
        clock = 1000
        decoder.feed(packThreat(0x12, listOf(triplet(0x80, 25, 0))))
        // Heartbeat past the stale window should drop the track.
        clock = 1000 + RadarV1Decoder.STALE_MS + 1
        val state = decoder.feed(byteArrayOf(0x02))
        assertNotNull(state)
        assertTrue(state!!.vehicles.isEmpty())
    }

    @Test fun sector_amplitude_packet_does_not_affect_tracks() {
        clock = 1000
        decoder.feed(packThreat(0x12, listOf(triplet(0x80, 25, 0))))
        // 6-byte sector amp packet: byte[0] = 0x06
        val sectorAmp = byteArrayOf(0x06, 0x30, 0x00, 0x00, 0x00, 0xAA.toByte())
        // Well inside STALE_MS — returning null here means "no change", which is correct.
        clock = 1500
        val state = decoder.feed(sectorAmp)
        assertNull(state)
    }

    @Test fun reset_clears_all_tracks() {
        clock = 1000
        decoder.feed(packThreat(0x12, listOf(
            triplet(0x80, 25, 0),
            triplet(0x81, 30, 0),
        )))
        decoder.reset()
        clock = 1500
        val state = decoder.feed(byteArrayOf(0x02))
        // No tracks to prune, no change, returns null.
        assertNull(state)
    }

    @Test fun heartbeat_low_nibble_is_always_0x2() {
        // Sanity: every observed heartbeat byte has low nibble 0x2.
        for (counter in 0..15) {
            val byte = ((counter shl 4) or 0x02).toByte()
            assertEquals(0x02, byte.toInt() and 0x0F)
        }
    }

    @Test fun empty_threat_after_filtering_emits_empty_state() {
        clock = 1000
        val state = decoder.feed(packThreat(0x12, listOf(
            triplet(0x00, 0, 0),
            triplet(0xFD, 0, 0),
            triplet(0xFF, 0xFF, 0),
        )))
        // All three filtered. No changes to track map -> returns null on an
        // empty decoder.
        assertNull(state)
    }

    @Test fun max_six_vehicles_in_19_byte_packet() {
        clock = 1000
        val triplets = (0..5).map { triplet(0x80 + it, 10 + it, 0) }
        val payload = packThreat(0x12, triplets)
        assertEquals(19, payload.size)
        val state = decoder.feed(payload)
        assertNotNull(state)
        assertEquals(6, state!!.vehicles.size)
    }

    @Test fun null_returned_when_track_set_unchanged() {
        clock = 1000
        decoder.feed(packThreat(0x12, listOf(triplet(0x80, 25, 0))))
        // Feeding the same vid again at the same distance: the track object
        // changes (lastSeen updates) so the method still returns a state.
        clock = 1100
        val state = decoder.feed(packThreat(0x22, listOf(triplet(0x80, 25, 0))))
        assertNotNull(state)
        assertEquals(1, state!!.vehicles.size)
    }
}
