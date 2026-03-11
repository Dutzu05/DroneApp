import 'dart:convert';

import 'package:flutter/services.dart' show rootBundle;

import '../models/aero_feature.dart';

/// Layer configuration: asset path, display label, default visibility per mode.
class _LayerConfig {
  const _LayerConfig({
    required this.key,
    required this.assetPath,
    required this.label,
    required this.droneDefault,
    required this.gaDefault,
  });

  final String key;
  final String assetPath;
  final String label;
  final bool droneDefault;
  final bool gaDefault;
}

/// Loads and provides access to all seven ROMATSA aeronautical data layers.
class AeroDataService {
  AeroDataService._();
  static final AeroDataService instance = AeroDataService._();

  static const _layers = <_LayerConfig>[
    _LayerConfig(
      key: 'uas_zones',
      assetPath: 'assets/restriction_zones.geojson',
      label: 'UAS Zones',
      droneDefault: true,
      gaDefault: false,
    ),
    _LayerConfig(
      key: 'notam',
      assetPath: 'assets/notam_zones.geojson',
      label: 'NOTAM UAS',
      droneDefault: true,
      gaDefault: false,
    ),
    _LayerConfig(
      key: 'notam_all',
      assetPath: 'assets/notam_all.geojson',
      label: 'All NOTAMs',
      droneDefault: false,
      gaDefault: true,
    ),
    _LayerConfig(
      key: 'ctr',
      assetPath: 'assets/airspace_ctr.geojson',
      label: 'CTR Airspace',
      droneDefault: true,
      gaDefault: true,
    ),
    _LayerConfig(
      key: 'tma',
      assetPath: 'assets/airspace_tma.geojson',
      label: 'TMA Airspace',
      droneDefault: false,
      gaDefault: true,
    ),
    _LayerConfig(
      key: 'airports',
      assetPath: 'assets/airports.geojson',
      label: 'Airports',
      droneDefault: true,
      gaDefault: true,
    ),
    _LayerConfig(
      key: 'lower_routes',
      assetPath: 'assets/lower_routes.geojson',
      label: 'ATS Routes',
      droneDefault: false,
      gaDefault: true,
    ),
  ];

  /// All loaded layers keyed by layer key.
  final Map<String, LayerInfo> _data = {};

  bool _loaded = false;

  /// Whether all layers have been loaded.
  bool get isLoaded => _loaded;

  /// All layer keys in display order.
  List<String> get layerKeys => _layers.map((l) => l.key).toList();

  /// Get a specific layer's info (null if not loaded).
  LayerInfo? operator [](String key) => _data[key];

  /// Display label for a layer key.
  String labelFor(String key) =>
      _layers.firstWhere((l) => l.key == key, orElse: () => _layers.first).label;

  /// Whether a layer should be visible by default in the given mode.
  bool defaultVisibility(String key, {required bool droneMode}) {
    final cfg = _layers.firstWhere((l) => l.key == key,
        orElse: () => _layers.first);
    return droneMode ? cfg.droneDefault : cfg.gaDefault;
  }

  /// Load all layers from bundled assets.  Safe to call multiple times.
  Future<void> loadAll() async {
    if (_loaded) return;

    for (final cfg in _layers) {
      try {
        final raw = await rootBundle.loadString(cfg.assetPath);
        final geojson = jsonDecode(raw) as Map<String, dynamic>;
        final features = (geojson['features'] as List<dynamic>)
            .map((f) => AeroFeature.fromGeoJson(
                cfg.key, f as Map<String, dynamic>))
            .toList(growable: false);

        _data[cfg.key] = LayerInfo(
          key: cfg.key,
          label: cfg.label,
          featureCount: features.length,
          features: features,
        );
      } catch (e) {
        // Layer missing or corrupt — skip silently
        _data[cfg.key] = LayerInfo(
          key: cfg.key,
          label: cfg.label,
          featureCount: 0,
          features: const [],
        );
      }
    }

    _loaded = true;
  }

  /// All features across all layers.
  List<AeroFeature> get allFeatures =>
      _data.values.expand((l) => l.features).toList();

  /// Filter features from a specific layer by altitude.
  List<AeroFeature> filterByAltitude(String layerKey, double altitudeM) {
    final layer = _data[layerKey];
    if (layer == null) return const [];
    return layer.features
        .where((f) => f.isRelevantAtAltitude(altitudeM))
        .toList(growable: false);
  }

  /// All features relevant at a given altitude across all layers.
  List<AeroFeature> allRelevantAt(double altitudeM) {
    return allFeatures
        .where((f) => f.isRelevantAtAltitude(altitudeM))
        .toList(growable: false);
  }

  /// Search features by name/text across all layers.
  List<AeroFeature> search(String query) {
    if (query.isEmpty) return const [];
    final q = query.toUpperCase();
    return allFeatures
        .where((f) => f.name.toUpperCase().contains(q))
        .take(50)
        .toList();
  }

  /// Force-reload all layers.
  Future<void> reload() async {
    _data.clear();
    _loaded = false;
    await loadAll();
  }
}
