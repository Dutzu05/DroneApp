import 'dart:convert';

import 'package:flutter/services.dart' show rootBundle;

import '../models/restriction_zone.dart';

/// Loads and filters UAS restriction zones from the bundled GeoJSON asset
/// produced by `scripts/fetch_restriction_zones.py`.
class RestrictionZoneService {
  RestrictionZoneService._();
  static final RestrictionZoneService instance = RestrictionZoneService._();

  static const _assetPath = 'assets/restriction_zones.geojson';

  List<RestrictionZone>? _allZones;
  Map<String, dynamic>? _metadata;

  /// All zones loaded from the asset.
  List<RestrictionZone> get allZones => _allZones ?? const [];

  /// Metadata from the GeoJSON (source URL, fetch timestamp, etc.)
  Map<String, dynamic> get metadata => _metadata ?? const {};

  /// Load zones from the bundled asset.  Safe to call multiple times –
  /// subsequent calls return the cached list.
  Future<List<RestrictionZone>> load() async {
    if (_allZones != null) return _allZones!;

    final raw = await rootBundle.loadString(_assetPath);
    final geojson = jsonDecode(raw) as Map<String, dynamic>;

    _metadata = geojson['metadata'] as Map<String, dynamic>? ?? {};

    final features = geojson['features'] as List<dynamic>;
    _allZones = features
        .map((f) =>
            RestrictionZone.fromGeoJsonFeature(f as Map<String, dynamic>))
        .toList(growable: false);

    return _allZones!;
  }

  /// Return only zones that are relevant for a flight at [altitudeM] metres AGL.
  ///
  /// Zones whose lower limit is **above** the flight altitude are excluded
  /// (they don't apply at that height).  Zones with unknown limits
  /// ("BY NOTAM") are always included as a safety precaution.
  List<RestrictionZone> filterByAltitude(double altitudeM) {
    return allZones
        .where((z) => z.isRelevantAtAltitude(altitudeM))
        .toList(growable: false);
  }

  /// Force-reload from asset (useful after an in-app update).
  Future<List<RestrictionZone>> reload() async {
    _allZones = null;
    _metadata = null;
    return load();
  }
}
