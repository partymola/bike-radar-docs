package es.jjrh.bikeradar

import kotlin.math.abs

/**
 * Stateful decoder for V2 stream notifications on characteristic 0x3204.
 *
 * Packet layout (see PROTOCOL.md §"V2 stream: characteristic 0x3204"):
 *   [2-byte LE header] + N * [9-byte target struct]
 *
 * Header bits:
 *   0x0001 -> status/ack frame, no target payload (skip).
 *   0x0004 -> device-status frame, no targets (skip).
 *   Anything else -> decode N targets from body.
 *
 * Target struct (9 bytes):
 *   [0]    uint8  targetId       radar-assigned track ID
 *   [1]    uint8  targetClass    enum (HIGH=36, NORMAL=23, NORMAL_STABLE=26,
 *                                 LOW=16, LOW_STABLE=13, UNKNOWN=4)
 *   [2]    uint8  rangeYLow      x0.1 m distance within current 25.6 m zone
 *   [3]    bits 0..2 = rangeYZone; bits 3..7 reserved (see below)
 *   [4]    int8   rangeX         lateral offset x0.1 m (+ve = right)
 *   [5]    uint8  length         x0.25 m
 *   [6]    uint8  width          x0.25 m
 *   [7]    int8   speedY         x0.5 m/s (longitudinal, -ve = approaching)
 *   [8]    uint8  0x80           observed constant sentinel
 *
 * Full rangeY reconstruction:
 *   zone     = ((byte[3] and 0x07) + 1) and 0x07   // 0..7
 *   rangeY_m = zone * 25.6 + byte[2] * 0.1
 *
 * Eight zones x 25.6 m covers 204.8 m, matching the RearVue 820's 175 m
 * detection range. byte[3] bits 3..7 change per packet but do not correlate
 * with position or motion; treat as reserved.
 *
 * The decoder projects each target into the shared [Vehicle]/[RadarState]
 * model:
 *   distanceM  = rangeY rounded (radar is rear-facing, values are always >= 0)
 *   speedMs    = speedY rounded (negative = approaching)
 *   size       = classified from length (BIKE/CAR/TRUCK)
 *   lateralPos = rangeX / LATERAL_FULL_M clamped to -1..+1
 *
 * Not thread-safe; call from a single coroutine.
 */
class RadarV2Decoder(
    private val nowMs: () -> Long = { System.currentTimeMillis() },
) {
    private data class Track(val vehicle: Vehicle, val lastSeen: Long, val staleMs: Long)

    private val tracks = HashMap<Int, Track>()

    /**
     * Feed one notification payload. Returns the new [RadarState] if the
     * packet changed anything visible, else null (pure-status frame).
     */
    fun feed(payload: ByteArray): RadarState? {
        val now = nowMs()
        if (payload.size < HEADER_SIZE) return if (pruneStale(now)) snapshot(now) else null
        val header = (payload[0].toInt() and 0xFF) or ((payload[1].toInt() and 0xFF) shl 8)
        val isStatus = header and STATUS_FRAME_BIT != 0
        val isDeviceStatus = header and DEVICE_STATUS_BIT != 0
        val changed = if (isStatus || isDeviceStatus) {
            pruneStale(now)
        } else {
            ingestTargets(payload, now)
        }
        return if (changed) snapshot(now) else null
    }

    private fun ingestTargets(payload: ByteArray, now: Long): Boolean {
        var changed = pruneStale(now)
        val bodyLen = payload.size - HEADER_SIZE
        val n = bodyLen / TARGET_SIZE
        for (i in 0 until n) {
            val off = HEADER_SIZE + i * TARGET_SIZE
            val tid = payload[off].toInt() and 0xFF
            val b2 = payload[off + 2].toInt() and 0xFF
            val b3 = payload[off + 3].toInt() and 0xFF
            val zone = ((b3 and 0x07) + 1) and 0x07
            val rangeY = zone * ZONE_SIZE_M + b2 * 0.1f
            val rangeX = payload[off + 4].toInt().toByte().toInt() * 0.1f
            val lengthM = (payload[off + 5].toInt() and 0xFF) * 0.25f
            val speedY = payload[off + 7].toInt() * 0.5f

            val distance = rangeY.toInt()
            val speedMs = speedY.toInt()
            val size = classifySize(lengthM)
            val lateralPos = (rangeX / LATERAL_FULL_M).coerceIn(-1f, 1f)

            // Split stale window: moving tracks must refresh quickly to avoid
            // ghost boxes after they overtake (Doppler resolves them until
            // they are abeam, then they vanish in a single frame). Stopped
            // tracks (a vehicle queuing behind the bike at a red light) are
            // Doppler-invisible so we keep them longer.
            val stale = if (abs(speedMs) > MOVING_SPEED_MS) STALE_MOVING_MS else STALE_PARKED_MS

            tracks[tid] = Track(
                vehicle = Vehicle(
                    id = tid,
                    distanceM = distance,
                    speedMs = speedMs,
                    size = size,
                    lateralPos = lateralPos,
                ),
                lastSeen = now,
                staleMs = stale,
            )
            changed = true
        }
        return changed
    }

    private fun pruneStale(now: Long): Boolean {
        val before = tracks.size
        val it = tracks.entries.iterator()
        while (it.hasNext()) {
            val t = it.next().value
            if (now - t.lastSeen > t.staleMs) it.remove()
        }
        return tracks.size != before
    }

    private fun snapshot(now: Long): RadarState =
        RadarState(
            vehicles = tracks.values.map { it.vehicle }.sortedBy { it.distanceM },
            timestamp = now,
            source = DataSource.V2,
        )

    /** Force-drop all tracks (e.g. on BLE disconnect). */
    fun reset() {
        tracks.clear()
    }

    private fun classifySize(lengthM: Float): VehicleSize = when {
        lengthM < BIKE_MAX_M -> VehicleSize.BIKE
        lengthM > TRUCK_MIN_M -> VehicleSize.TRUCK
        else -> VehicleSize.CAR
    }

    companion object {
        const val HEADER_SIZE = 2
        const val TARGET_SIZE = 9
        const val STATUS_FRAME_BIT = 0x0001
        const val DEVICE_STATUS_BIT = 0x0004

        /** Above this approach speed a track counts as "moving" for stale-window
         *  purposes. 1 m/s ~= 3.6 km/h. Below that we assume the target is
         *  queuing stationary and Doppler will briefly lose it. */
        const val MOVING_SPEED_MS = 1

        /** Moving-track stale window. Short so ghost boxes don't linger after
         *  the vehicle overtakes and vanishes from the rear-only beam. */
        const val STALE_MOVING_MS = 800L

        /** Parked-track stale window. Long enough to keep a stopped vehicle
         *  visible across the Doppler dropouts typical at traffic lights. */
        const val STALE_PARKED_MS = 5000L

        /** Lateral distance (m) that maps to RadarState's -1..+1 full deflection.
         *  Road-lane half-width is roughly 1.5 m; 3 m gives a bit of room to the
         *  kerb / adjacent lane without pegging immediately. */
        const val LATERAL_FULL_M = 3.0f

        /** Length thresholds for the BIKE / CAR / TRUCK size buckets (m). */
        const val BIKE_MAX_M = 2.5f
        const val TRUCK_MIN_M = 5.5f

        /** Width of one rangeY zone: 0..255 of byte[2] * 0.1 m. */
        const val ZONE_SIZE_M = 25.6f
    }
}
