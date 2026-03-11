/// Data model for a single UAS restriction zone parsed from the
/// enriched ROMATSA GeoJSON produced by `scripts/fetch_restriction_zones.py`.
class RestrictionZone {
  const RestrictionZone({
    required this.zoneId,
    required this.lowerLimitRaw,
    required this.upperLimitRaw,
    required this.lowerLimitM,
    required this.upperLimitM,
    required this.contact,
    required this.status,
    required this.polygonCoordinates,
  });

  /// e.g. "RZ 1001"
  final String zoneId;

  /// Original strings from ROMATSA ("GND", "120M AGL", "2500FT AMSL", …)
  final String lowerLimitRaw;
  final String upperLimitRaw;

  /// Normalised altitude in **metres AGL** (null when unknown, e.g. "BY NOTAM")
  final double? lowerLimitM;
  final double? upperLimitM;

  /// Contact info for the zone
  final String contact;

  /// e.g. "RESTRICTED"
  final String status;

  /// Polygon rings – list of [lng, lat] pairs.
  /// Outer ring first, then any holes (standard GeoJSON winding).
  final List<List<List<double>>> polygonCoordinates;

  /// Whether a flight at [altitudeM] metres AGL would enter this zone.
  ///
  /// Logic:
  ///   • If limits are unknown (BY NOTAM) → always considered relevant (safe side).
  ///   • A zone is relevant when the flight altitude falls between lower and upper.
  ///   • Example: zone lower=GND(0), upper=120 m  →  flight at 50 m → **relevant**
  ///   • Example: zone lower=120 m, upper=2500 ft  →  flight at 50 m → **not relevant**
  bool isRelevantAtAltitude(double altitudeM) {
    // Unknown limits → always show (precautionary)
    if (lowerLimitM == null || upperLimitM == null) return true;

    return altitudeM >= lowerLimitM! && altitudeM <= upperLimitM!;
  }

  factory RestrictionZone.fromGeoJsonFeature(Map<String, dynamic> feature) {
    final props = feature['properties'] as Map<String, dynamic>;
    final geometry = feature['geometry'] as Map<String, dynamic>;

    // GeoJSON Polygon coordinates: [ [ [lng,lat], … ] ]
    final rawCoords = geometry['coordinates'] as List<dynamic>;
    final coords = rawCoords
        .map((ring) => (ring as List<dynamic>)
            .map((pt) =>
                (pt as List<dynamic>).map((v) => (v as num).toDouble()).toList())
            .toList())
        .toList();

    return RestrictionZone(
      zoneId: props['zone_id'] as String? ?? '',
      lowerLimitRaw: props['lower_lim_raw'] as String? ?? '',
      upperLimitRaw: props['upper_lim_raw'] as String? ?? '',
      lowerLimitM: (props['lower_limit_m'] as num?)?.toDouble(),
      upperLimitM: (props['upper_limit_m'] as num?)?.toDouble(),
      contact: props['contact'] as String? ?? '',
      status: props['status'] as String? ?? '',
      polygonCoordinates: coords,
    );
  }

  @override
  String toString() =>
      'RestrictionZone($zoneId, $lowerLimitRaw→${lowerLimitM}m – $upperLimitRaw→${upperLimitM}m)';
}
