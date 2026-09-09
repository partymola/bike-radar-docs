// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 JJ del Rio
// Permissive so another project can copy these types with the decoders.
// Licence text: LICENSES/Apache-2.0.txt
package es.jjrh.bikeradar

enum class VehicleSize { BIKE, CAR, TRUCK }

enum class DataSource { NONE, V1, V2 }

data class Vehicle(
    val id: Int,
    val distanceM: Int,
    /** Closing speed in m/s (negative = approaching). Null when unknown:
     *  the V1 stream carries no velocity, so V1 tracks leave this unset. */
    val speedMs: Int?,
    val size: VehicleSize = VehicleSize.CAR,
    /** -1.0 = full left, 0.0 = same lane / centre, +1.0 = full right */
    val lateralPos: Float = 0f,
) {
    val speedKmh: Int? get() = speedMs?.let { (it * 3.6).toInt() }
}

data class RadarState(
    val vehicles: List<Vehicle> = emptyList(),
    val timestamp: Long = System.currentTimeMillis(),
    val source: DataSource = DataSource.NONE,
) {
    val isClear: Boolean get() = vehicles.isEmpty()
}
