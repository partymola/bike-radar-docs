// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (c) 2026 the bike-radar-docs contributors.
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
 *   [2..4] packed range field    24-bit little-endian (see decoding below)
 *   [5]    uint8  length         x0.25 m (class template; not a real measurement)
 *   [6]    uint8  width          x0.25 m (class template; not a real measurement)
 *   [7]    int8   speedY         x0.5 m/s (longitudinal closing speed; -ve = approaching)
 *   [8]    int8   speedX         x0.5 m/s (lateral; 0x80 = -64 m/s sentinel = "no data")
 *
 * Range decoding from bytes [2..4] (24-bit little-endian word):
 *   packed     = byte[2] | (byte[3] shl 8) | (byte[4] shl 16)
 *   rangeXBits = packed and 0x07FF                                  // 11-bit
 *   if (rangeXBits and 0x0400 != 0) rangeXBits -= 0x0800             // sign-extend
 *   rangeYBits = (packed shr 11) and 0x1FFF                          // 13-bit
 *   if (rangeYBits and 0x1000 != 0) rangeYBits -= 0x2000             // sign-extend
 *   rangeX_m   = rangeXBits * 0.1   // ±204.7 m theoretical
 *   rangeY_m   = rangeYBits * 0.1   // ±409.5 m theoretical, ~220 m in practice
 *
 * Sign convention (rear-radar coordinate system):
 *   rangeY > 0 -> target is BEHIND the rider (typical, ~99% of frames)
 *   rangeY < 0 -> target is AHEAD (post-overtake; rare, ~0.7%)
 *   rangeX > 0 -> right of rider
 *
 * The decoder projects each target into the shared [Vehicle]/[RadarState]
 * model:
 *   distanceM  = abs(rangeY) rounded
 *   isBehind   = rangeY > 0
 *   speedMs    = speedY rounded (negative = approaching)
 *   size       = classified from class enum (BIKE/CAR/TRUCK)
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
            val cls = payload[off + 1].toInt() and 0xFF

            // Bytes [2..4] are a 24-bit little-endian packed word:
            //   bits 0..10  = rangeX (11-bit signed, x0.1 m)
            //   bits 11..23 = rangeY (13-bit signed, x0.1 m)
            val b2 = payload[off + 2].toInt() and 0xFF
            val b3 = payload[off + 3].toInt() and 0xFF
            val b4 = payload[off + 4].toInt() and 0xFF
            val packed = b2 or (b3 shl 8) or (b4 shl 16)
            var rxBits = packed and 0x07FF
            if (rxBits and 0x0400 != 0) rxBits -= 0x0800
            var ryBits = (packed shr 11) and 0x1FFF
            if (ryBits and 0x1000 != 0) ryBits -= 0x2000
            val rangeX = rxBits * 0.1f
            val rangeY = ryBits * 0.1f       // > 0 = behind, < 0 = ahead

            val speedY = payload[off + 7].toInt() * 0.5f

            val distance = abs(rangeY).toInt()
            val isBehind = rangeY > 0f
            val speedMs = speedY.toInt()
            val size = classifySize(cls)
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

    /** Class-enum to size bucket. Class values are documented in PROTOCOL.md. */
    private fun classifySize(cls: Int): VehicleSize = when (cls) {
        CLASS_WEAK, CLASS_WEAK_HOLD -> VehicleSize.BIKE
        CLASS_STRONG -> VehicleSize.TRUCK
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

        /** Target class enum (observed values, project-native names).
         *  Higher numeric value = larger / more confident return signature. */
        const val CLASS_UNCLASSIFIED = 4
        const val CLASS_WEAK_HOLD = 13
        const val CLASS_WEAK = 16
        const val CLASS_MEDIUM = 23
        const val CLASS_MEDIUM_HOLD = 26
        const val CLASS_STRONG = 36
    }
}
