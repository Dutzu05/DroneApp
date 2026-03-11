/// Unified model for aeronautical features from all ROMATSA data layers.
///
/// Each feature carries its layer key (e.g. 'uas_zones', 'ctr', 'airports')
/// plus normalised altitude limits and the raw GeoJSON properties.
class AeroFeature {
  const AeroFeature({
    required this.layer,
    required this.name,
    required this.lowerLimitM,
    required this.upperLimitM,
    required this.lowerLimitRaw,
    required this.upperLimitRaw,
    required this.properties,
    required this.geometryType,
  });

  /// Layer key: uas_zones, notam, notam_all, ctr, tma, airports, lower_routes
  final String layer;

  /// Human-readable name (zone_id, NOTAM ID, airport ICAO, route designator, etc.)
  final String name;

  /// Normalised altitude limits in metres (null = unknown / N/A)
  final double? lowerLimitM;
  final double? upperLimitM;

  /// Original limit strings from the data source
  final String lowerLimitRaw;
  final String upperLimitRaw;

  /// All raw GeoJSON properties for this feature
  final Map<String, dynamic> properties;

  /// GeoJSON geometry type: Point, Polygon, MultiPolygon, LineString, etc.
  final String geometryType;

  /// Whether a flight at [altitudeM] would be within this feature's vertical extent.
  bool isRelevantAtAltitude(double altitudeM) {
    if (lowerLimitM == null || upperLimitM == null) return true;
    return altitudeM >= lowerLimitM! && altitudeM <= upperLimitM!;
  }

  /// Best-effort subtitle for display
  String get subtitle {
    final parts = <String>[];
    if (lowerLimitRaw.isNotEmpty) parts.add('$lowerLimitRaw → $upperLimitRaw');
    final contact = properties['contact'] as String?;
    if (contact != null && contact.isNotEmpty) parts.add(contact);
    final airport = properties['airport'] as String?;
    if (airport != null && airport.isNotEmpty) parts.add(airport);
    final icao = properties['icao'] as String?;
    if (icao != null && icao.isNotEmpty) parts.add(icao);
    final msg = properties['message'] as String?;
    if (msg != null && msg.isNotEmpty) {
      final clean = msg.replaceAll(RegExp(r'\s+'), ' ').trim();
      parts.add(clean.length > 120 ? '${clean.substring(0, 120)}...' : clean);
    }
    return parts.join('\n');
  }

  /// Parse a GeoJSON feature dict into an [AeroFeature].
  factory AeroFeature.fromGeoJson(String layer, Map<String, dynamic> feature) {
    final props = feature['properties'] as Map<String, dynamic>? ?? {};
    final geom = feature['geometry'] as Map<String, dynamic>? ?? {};

    return AeroFeature(
      layer: layer,
      name: _extractName(layer, props),
      lowerLimitM: (props['lower_limit_m'] as num?)?.toDouble(),
      upperLimitM: (props['upper_limit_m'] as num?)?.toDouble(),
      lowerLimitRaw: (props['lower_lim_raw'] as String?) ?? '',
      upperLimitRaw: (props['upper_lim_raw'] as String?) ?? '',
      properties: props,
      geometryType: (geom['type'] as String?) ?? 'Unknown',
    );
  }

  static String _extractName(String layer, Map<String, dynamic> p) {
    switch (layer) {
      case 'uas_zones':
        return (p['zone_id'] as String?) ?? 'UAS Zone';
      case 'notam':
        return (p['notam_id'] as String?) ??
            (p['zone_id'] as String?) ??
            'NOTAM UAS';
      case 'notam_all':
        return (p['notam_id'] as String?) ??
            (p['serie'] as String?) ??
            'NOTAM';
      case 'ctr':
        return (p['name'] as String?) ??
            (p['arsp_name'] as String?) ??
            'CTR';
      case 'tma':
        return (p['name'] as String?) ??
            (p['arsp_name'] as String?) ??
            'TMA';
      case 'airports':
        final name = (p['name'] as String?) ?? '';
        final icao = (p['icao'] as String?) ?? (p['ident'] as String?) ?? '';
        return icao.isNotEmpty ? '$name ($icao)' : name;
      case 'lower_routes':
        return (p['route_designator'] as String?) ?? 'Route';
      default:
        return layer;
    }
  }

  @override
  String toString() => 'AeroFeature($layer: $name)';
}

/// Metadata about a loaded layer.
class LayerInfo {
  const LayerInfo({
    required this.key,
    required this.label,
    required this.featureCount,
    required this.features,
  });

  final String key;
  final String label;
  final int featureCount;
  final List<AeroFeature> features;
}
