package es.jjrh.bikeradar

enum class VehicleSize { BIKE, CAR, TRUCK }

enum class DataSource { NONE, V1, V2 }

data class Vehicle(
    val id: Int,
    val distanceM: Int,
    val speedMs: Int,
    val size: VehicleSize = VehicleSize.CAR,
    /** -1.0 = full left, 0.0 = same lane / centre, +1.0 = full right */
    val lateralPos: Float = 0f,
) {
    val speedKmh: Int get() = (speedMs * 3.6).toInt()
}

data class RadarState(
    val vehicles: List<Vehicle> = emptyList(),
    val timestamp: Long = System.currentTimeMillis(),
    val source: DataSource = DataSource.NONE,
) {
    val isClear: Boolean get() = vehicles.isEmpty()
}
