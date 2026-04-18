package es.jjrh.bikeradar

/**
 * Stateful decoder for V1 stream notifications on characteristic 0x3203.
 *
 * Maintains a set of active vehicle tracks keyed by track id; each new threat
 * packet refreshes the tracks it mentions, and any track unseen for [STALE_MS]
 * is dropped. Not thread-safe; call from a single coroutine.
 *
 * Packet layout (see PROTOCOL.md §"V1 stream: characteristic 0x3203"):
 *   1 byte, low nibble = 0x2:         heartbeat (ignored here)
 *   6 bytes, byte[0] == 0x06:         sector amplitude (ignored here)
 *   1 + 3N bytes (N = 1..6):          [seq][vid, dist, flag]*N
 *     vid == 0x00                     "no vehicle" placeholder; skip
 *     vid == 0xFD                     header/status marker; skip
 *     vid  < 0x80                     bit 7 = vehicle-present flag; skip
 *     otherwise track id = vid & 0x7F
 *     dist = uint8 metres (0xFF = "far/uncertain" sentinel; skip)
 *     flag = uint8 state flag (takes values 0 or 1; meaning unconfirmed)
 */
class RadarV1Decoder(
    private val nowMs: () -> Long = { System.currentTimeMillis() },
    private val staleMs: Long = STALE_MS,
) {
    private data class Track(val vehicle: Vehicle, val lastSeen: Long)

    private val tracks = HashMap<Int, Track>()

    /**
     * Feed a single notification payload. Returns the new [RadarState] if the
     * packet changed anything visible (new/updated/dropped track), else null.
     */
    fun feed(payload: ByteArray): RadarState? {
        val now = nowMs()
        val changed = when {
            payload.size == 1 -> pruneStale(now)
            payload.size == 6 && payload[0] == 0x06.toByte() -> pruneStale(now)
            payload.size >= 4 && (payload.size - 1) % 3 == 0 -> ingestThreat(payload, now)
            else -> pruneStale(now)
        }
        return if (changed) snapshot(now) else null
    }

    private fun ingestThreat(payload: ByteArray, now: Long): Boolean {
        var changed = pruneStale(now)
        val n = (payload.size - 1) / 3
        for (i in 0 until n) {
            val vid = payload[1 + 3 * i].toInt() and 0xFF
            val dist = payload[1 + 3 * i + 1].toInt() and 0xFF
            val flag = payload[1 + 3 * i + 2].toInt() and 0xFF
            if (vid == 0x00 || vid == 0xFD || vid < 0x80) continue
            if (dist == 0xFF) continue
            val id = vid and 0x7F
            val existing = tracks[id]?.vehicle
            val size = existing?.size ?: VehicleSize.CAR
            val lateral = existing?.lateralPos ?: 0f
            tracks[id] = Track(
                vehicle = Vehicle(id = id, distanceM = dist, speedMs = flag, size = size, lateralPos = lateral),
                lastSeen = now,
            )
            changed = true
        }
        return changed
    }

    private fun pruneStale(now: Long): Boolean {
        val before = tracks.size
        val it = tracks.entries.iterator()
        while (it.hasNext()) {
            if (now - it.next().value.lastSeen > staleMs) it.remove()
        }
        return tracks.size != before
    }

    private fun snapshot(now: Long): RadarState =
        RadarState(
            vehicles = tracks.values.map { it.vehicle }.sortedBy { it.distanceM },
            timestamp = now,
            source = DataSource.V1,
        )

    /** Force-drop all tracks (e.g. on BLE disconnect). */
    fun reset() {
        tracks.clear()
    }

    companion object {
        /** Drop a track if unseen for longer than this. V1 threat cadence is
         *  sub-second per track in heavy traffic; 2 s gives good smoothing for
         *  sparse traffic without hanging onto vanished vehicles. */
        const val STALE_MS = 2000L
    }
}
