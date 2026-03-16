import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:http/http.dart' as http;

import 'data/local_cache_db.dart';
import 'models/aero_feature.dart';
import 'models/restriction_zone.dart';
import 'services/aero_data_service.dart';
import 'services/restriction_zone_service.dart';

void main() {
  runApp(const DroneApp());
}

const _googleServerClientId =
    '1082596673448-0k7mnlrj1vt9pkrs1vuh8ar68arsj6mt.apps.googleusercontent.com';

const _googleWebClientId = String.fromEnvironment(
  'DRONE_GOOGLE_WEB_CLIENT_ID',
  defaultValue: _googleServerClientId,
);

const _configuredBackendBaseUrl = String.fromEnvironment(
  'DRONE_BACKEND_BASE_URL',
);

String _resolveBackendBaseUrl() {
  if (_configuredBackendBaseUrl.isNotEmpty) {
    return _configuredBackendBaseUrl;
  }
  if (kIsWeb) {
    return 'http://localhost:5174';
  }
  if (defaultTargetPlatform == TargetPlatform.android) {
    return 'http://10.0.2.2:5174';
  }
  return 'http://localhost:5174';
}

GoogleSignIn _createGoogleSignIn() {
  return GoogleSignIn(
    scopes: const ['email', 'profile'],
    clientId: kIsWeb ? _googleWebClientId : null,
    serverClientId: kIsWeb ? null : _googleServerClientId,
  );
}

class DroneApp extends StatefulWidget {
  const DroneApp({super.key});

  @override
  State<DroneApp> createState() => _DroneAppState();
}

class _DroneAppState extends State<DroneApp> {
  final _googleSignIn = _createGoogleSignIn();
  final _backendBaseUrl = _resolveBackendBaseUrl();

  GoogleSignInAccount? _currentUser;
  String? _error;
  bool _initializing = true;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _restoreSession();
  }

  Future<void> _restoreSession() async {
    try {
      final user = await _googleSignIn.signInSilently();
      if (!mounted) return;
      setState(() {
        _currentUser = user;
        _error = null;
      });
      if (user != null) {
        await _reportSession(user);
      }
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    } finally {
      if (mounted) {
        setState(() => _initializing = false);
      }
    }
  }

  Future<void> _signIn() async {
    setState(() {
      _busy = true;
      _error = null;
    });

    try {
      final user = await _googleSignIn.signIn();
      if (user == null) {
        setState(() => _error = 'Google login cancelled.');
      } else {
        await _reportSession(user);
        if (!mounted) return;
        setState(() => _currentUser = user);
      }
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = 'Login failed: $e');
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _signOut() async {
    await _googleSignIn.signOut();
    if (!mounted) return;
    setState(() => _currentUser = null);
  }

  Future<void> _reportSession(GoogleSignInAccount user) async {
    final auth = await user.authentication;
    final payload = {
      'email': user.email,
      'display_name': user.displayName ?? '',
      'google_user_id': user.id,
      'id_token': auth.idToken,
      'app': kIsWeb ? 'mobile_app_web' : 'mobile_app',
    };

    try {
      await http
          .post(
            Uri.parse('$_backendBaseUrl/api/auth/google-session'),
            headers: const {'Content-Type': 'application/json'},
            body: jsonEncode(payload),
          )
          .timeout(const Duration(seconds: 3));
    } catch (_) {
      // Keep login flow alive even if backend tracking is unavailable.
    }
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Drone Offline Cache',
      theme: ThemeData(colorSchemeSeed: Colors.teal, useMaterial3: true),
      home: _initializing
          ? const Scaffold(body: Center(child: CircularProgressIndicator()))
          : _currentUser == null
              ? LoginScreen(
                  busy: _busy,
                  error: _error,
                  backendBaseUrl: _backendBaseUrl,
                  onGoogleSignIn: _signIn,
                )
              : HomePage(
                  user: _currentUser!,
                  onSignOut: _signOut,
                ),
    );
  }
}

class LoginScreen extends StatelessWidget {
  const LoginScreen({
    required this.busy,
    required this.error,
    required this.backendBaseUrl,
    required this.onGoogleSignIn,
    super.key,
  });

  final bool busy;
  final String? error;
  final String backendBaseUrl;
  final Future<void> Function() onGoogleSignIn;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 420),
          child: Card(
            margin: const EdgeInsets.all(20),
            child: Padding(
              padding: const EdgeInsets.all(20),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(
                    'Sign in to continue',
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.headlineSmall,
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Please log in with Google before accessing map and airspace details.',
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.bodyMedium,
                  ),
                  const SizedBox(height: 16),
                  FilledButton.icon(
                    onPressed: busy ? null : onGoogleSignIn,
                    icon: const Icon(Icons.login),
                    label: Text(busy ? 'Signing in...' : 'Continue with Google'),
                  ),
                  const SizedBox(height: 10),
                  Text(
                    'Backend session viewer: $backendBaseUrl/admin/logged-accounts',
                    style: Theme.of(context).textTheme.bodySmall,
                    textAlign: TextAlign.center,
                  ),
                  if (error != null) ...[
                    const SizedBox(height: 10),
                    Text(
                      error!,
                      style: TextStyle(color: Theme.of(context).colorScheme.error),
                      textAlign: TextAlign.center,
                    ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

// ──────────────────────────────────────────────────────────────────────────
// Home – tab navigation between original cache demo and restriction zones
// ──────────────────────────────────────────────────────────────────────────

class HomePage extends StatefulWidget {
  const HomePage({
    required this.user,
    required this.onSignOut,
    super.key,
  });

  final GoogleSignInAccount user;
  final Future<void> Function() onSignOut;

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  int _tabIndex = 0;

  static const _tabs = <Widget>[
    CacheDemoScreen(),
    RestrictionZonesScreen(),
    AirspaceScreen(),
  ];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('Hi, ${widget.user.displayName ?? widget.user.email}'),
        actions: [
          IconButton(
            tooltip: 'Sign out',
            onPressed: widget.onSignOut,
            icon: const Icon(Icons.logout),
          ),
        ],
      ),
      body: _tabs[_tabIndex],
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tabIndex,
        onDestinationSelected: (i) => setState(() => _tabIndex = i),
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.storage),
            label: 'Cache',
          ),
          NavigationDestination(
            icon: Icon(Icons.airplanemode_active),
            label: 'Restriction Zones',
          ),
          NavigationDestination(
            icon: Icon(Icons.layers),
            label: 'Airspace',
          ),
        ],
      ),
    );
  }
}

class CacheDemoScreen extends StatefulWidget {
  const CacheDemoScreen({super.key});

  @override
  State<CacheDemoScreen> createState() => _CacheDemoScreenState();
}

class _CacheDemoScreenState extends State<CacheDemoScreen> {
  String _status = 'Ready';

  Future<void> _cacheSampleData() async {
    final db = LocalCacheDb.instance;

    await db.putTile(
      z: 12,
      x: 2201,
      y: 1344,
      bytes: utf8.encode('sample-tile-data'),
      expiresAt: DateTime.now().toUtc().add(const Duration(days: 14)),
    );

    await db.putRestrictionZones(
      key: 'ro_bucharest_sample',
      zones: const [
        {
          'zone_code': 'LFR-001',
          'name': 'Temporary no-fly area',
          'severity': 'restricted',
          'polygon': [
            [26.03, 44.43],
            [26.11, 44.43],
            [26.11, 44.48],
            [26.03, 44.48]
          ]
        }
      ],
      expiresAt: DateTime.now().toUtc().add(const Duration(hours: 6)),
    );

    setState(() => _status = 'Cached sample tile + restriction zone data');
  }

  Future<void> _loadSampleData() async {
    final db = LocalCacheDb.instance;
    final tile = await db.getTile(z: 12, x: 2201, y: 1344);
    final zones = await db.getRestrictionZones(key: 'ro_bucharest_sample');

    final tileMsg = tile == null
        ? 'Tile: missing'
        : 'Tile: present (${tile.bytes.length} bytes)';
    final zoneMsg = zones == null ? 'Zones: missing' : 'Zones: ${zones.length}';

    setState(() => _status = '$tileMsg | $zoneMsg');
  }

  Future<void> _pruneExpired() async {
    final deleted = await LocalCacheDb.instance.pruneExpiredTiles();
    setState(() => _status = 'Pruned expired tiles: $deleted');
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Offline Map Cache')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(_status),
            const SizedBox(height: 16),
            FilledButton(
              onPressed: _cacheSampleData,
              child: const Text('Cache sample data'),
            ),
            const SizedBox(height: 8),
            FilledButton.tonal(
              onPressed: _loadSampleData,
              child: const Text('Load from local cache'),
            ),
            const SizedBox(height: 8),
            OutlinedButton(
              onPressed: _pruneExpired,
              child: const Text('Prune expired tiles'),
            ),
          ],
        ),
      ),
    );
  }
}

// ──────────────────────────────────────────────────────────────────────────
// Restriction Zones screen – load GeoJSON + altitude slider filter
// ──────────────────────────────────────────────────────────────────────────

class RestrictionZonesScreen extends StatefulWidget {
  const RestrictionZonesScreen({super.key});

  @override
  State<RestrictionZonesScreen> createState() => _RestrictionZonesScreenState();
}

class _RestrictionZonesScreenState extends State<RestrictionZonesScreen> {
  final _svc = RestrictionZoneService.instance;

  bool _loading = true;
  String? _error;

  /// Flight altitude in metres set by the user via the slider.
  double _flightAltitudeM = 50;

  List<RestrictionZone> _filtered = const [];

  @override
  void initState() {
    super.initState();
    _loadZones();
  }

  Future<void> _loadZones() async {
    try {
      await _svc.load();
      _applyFilter();
    } catch (e) {
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  void _applyFilter() {
    setState(() {
      _loading = false;
      _error = null;
      _filtered = _svc.filterByAltitude(_flightAltitudeM);
    });
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final meta = _svc.metadata;

    return Scaffold(
      appBar: AppBar(title: const Text('UAS Restriction Zones')),
      body: Column(
        children: [
          // ── altitude slider ──
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Flight altitude: ${_flightAltitudeM.round()} m AGL',
                  style: theme.textTheme.titleMedium,
                ),
                Slider(
                  min: 0,
                  max: 500,
                  divisions: 100,
                  value: _flightAltitudeM,
                  label: '${_flightAltitudeM.round()} m',
                  onChanged: (v) {
                    _flightAltitudeM = v;
                    _applyFilter();
                  },
                ),
                if (!_loading && _error == null)
                  Text(
                    '${_filtered.length} of ${_svc.allZones.length} zones '
                    'relevant at ${_flightAltitudeM.round()} m',
                    style: theme.textTheme.bodySmall,
                  ),
                if (meta['fetched_at'] != null)
                  Text(
                    'Data fetched: ${meta['fetched_at']}',
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: theme.colorScheme.outline,
                    ),
                  ),
              ],
            ),
          ),
          const Divider(height: 1),

          // ── zone list ──
          if (_loading)
            const Expanded(
              child: Center(child: CircularProgressIndicator()),
            )
          else if (_error != null)
            Expanded(
              child: Center(
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Text('Error: $_error',
                      style: TextStyle(color: theme.colorScheme.error)),
                ),
              ),
            )
          else
            Expanded(
              child: ListView.builder(
                itemCount: _filtered.length,
                itemBuilder: (context, index) {
                  final zone = _filtered[index];
                  return _ZoneTile(zone: zone);
                },
              ),
            ),
        ],
      ),
    );
  }
}

class _ZoneTile extends StatelessWidget {
  const _ZoneTile({required this.zone});
  final RestrictionZone zone;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final hasUnknown = zone.lowerLimitM == null || zone.upperLimitM == null;

    return ListTile(
      leading: Icon(
        hasUnknown ? Icons.warning_amber_rounded : Icons.shield,
        color: hasUnknown
            ? theme.colorScheme.error
            : theme.colorScheme.primary,
      ),
      title: Text(zone.zoneId),
      subtitle: Text(
        '${zone.lowerLimitRaw} → ${zone.upperLimitRaw}\n${zone.contact}',
      ),
      isThreeLine: true,
      trailing: zone.upperLimitM != null
          ? Text('≤${zone.upperLimitM!.round()} m')
          : const Text('BY NOTAM', style: TextStyle(fontSize: 11)),
      onTap: () => _showZoneDetail(context),
    );
  }

  void _showZoneDetail(BuildContext context) {
    showModalBottomSheet(
      context: context,
      builder: (_) => Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(zone.zoneId,
                style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: 8),
            _detailRow('Status', zone.status),
            _detailRow('Lower limit',
                '${zone.lowerLimitRaw}  →  ${zone.lowerLimitM ?? "?"} m'),
            _detailRow('Upper limit',
                '${zone.upperLimitRaw}  →  ${zone.upperLimitM ?? "?"} m'),
            _detailRow('Contact', zone.contact),
            _detailRow('Vertices',
                '${zone.polygonCoordinates.first.length}'),
            const SizedBox(height: 12),
          ],
        ),
      ),
    );
  }

  Widget _detailRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 100,
            child: Text('$label:',
                style: const TextStyle(fontWeight: FontWeight.bold)),
          ),
          Expanded(child: Text(value)),
        ],
      ),
    );
  }
}

// ──────────────────────────────────────────────────────────────────────────
// Airspace screen – multi-layer view with Drone/GA mode toggle
// ──────────────────────────────────────────────────────────────────────────

/// Layer colour mapping matching the web visualiser.
const _layerColors = <String, Color>{
  'uas_zones': Color(0xFFE94560),
  'notam': Color(0xFFFF9800),
  'notam_all': Color(0xFFD29922),
  'ctr': Color(0xFF58A6FF),
  'tma': Color(0xFF3FB950),
  'airports': Color(0xFF39D2C0),
  'lower_routes': Color(0xFFBC8CFF),
};

class AirspaceScreen extends StatefulWidget {
  const AirspaceScreen({super.key});

  @override
  State<AirspaceScreen> createState() => _AirspaceScreenState();
}

class _AirspaceScreenState extends State<AirspaceScreen> {
  final _svc = AeroDataService.instance;

  bool _loading = true;
  String? _error;
  bool _droneMode = true;
  double _altitudeM = 120;
  String _searchQuery = '';

  /// Visible layer keys (toggled on/off)
  final Set<String> _visibleLayers = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      await _svc.loadAll();
      // Set default visibility
      for (final key in _svc.layerKeys) {
        if (_svc.defaultVisibility(key, droneMode: _droneMode)) {
          _visibleLayers.add(key);
        }
      }
      setState(() => _loading = false);
    } catch (e) {
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  void _setMode(bool drone) {
    _visibleLayers.clear();
    for (final key in _svc.layerKeys) {
      if (_svc.defaultVisibility(key, droneMode: drone)) {
        _visibleLayers.add(key);
      }
    }
    setState(() => _droneMode = drone);
  }

  List<AeroFeature> get _filteredFeatures {
    if (_searchQuery.isNotEmpty) return _svc.search(_searchQuery);

    final features = <AeroFeature>[];
    for (final key in _visibleLayers) {
      features.addAll(_svc.filterByAltitude(key, _altitudeM));
    }
    return features;
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    if (_loading) {
      return Scaffold(
        appBar: AppBar(title: const Text('Airspace Layers')),
        body: const Center(child: CircularProgressIndicator()),
      );
    }
    if (_error != null) {
      return Scaffold(
        appBar: AppBar(title: const Text('Airspace Layers')),
        body: Center(child: Text('Error: $_error')),
      );
    }

    final features = _filteredFeatures;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Airspace Layers'),
        actions: [
          // Drone / GA mode toggle
          SegmentedButton<bool>(
            segments: const [
              ButtonSegment(value: true, label: Text('Drone'), icon: Icon(Icons.flight_takeoff, size: 16)),
              ButtonSegment(value: false, label: Text('GA'), icon: Icon(Icons.airplanemode_active, size: 16)),
            ],
            selected: {_droneMode},
            onSelectionChanged: (s) => _setMode(s.first),
            style: ButtonStyle(
              visualDensity: VisualDensity.compact,
              textStyle: WidgetStatePropertyAll(theme.textTheme.labelSmall),
            ),
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: Column(
        children: [
          // ── Layer chips ──
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            child: Row(
              children: _svc.layerKeys.map((key) {
                final info = _svc[key];
                final color = _layerColors[key] ?? Colors.grey;
                final active = _visibleLayers.contains(key);
                return Padding(
                  padding: const EdgeInsets.only(right: 6),
                  child: FilterChip(
                    label: Text(
                      '${_svc.labelFor(key)} (${info?.featureCount ?? 0})',
                      style: TextStyle(fontSize: 11, color: active ? Colors.white : color),
                    ),
                    selected: active,
                    selectedColor: color.withAlpha(180),
                    checkmarkColor: Colors.white,
                    onSelected: (on) {
                      setState(() {
                        if (on) {
                          _visibleLayers.add(key);
                        } else {
                          _visibleLayers.remove(key);
                        }
                      });
                    },
                  ),
                );
              }).toList(),
            ),
          ),

          // ── Altitude slider ──
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(
              children: [
                Text('Alt: ${_altitudeM.round()} m', style: theme.textTheme.bodySmall),
                Expanded(
                  child: Slider(
                    min: 0,
                    max: _droneMode ? 500 : 15000,
                    divisions: _droneMode ? 100 : 150,
                    value: _altitudeM.clamp(0, _droneMode ? 500 : 15000),
                    label: '${_altitudeM.round()} m',
                    onChanged: (v) => setState(() => _altitudeM = v),
                  ),
                ),
                Text('${features.length} hits', style: theme.textTheme.bodySmall),
              ],
            ),
          ),

          // ── Search ──
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
            child: TextField(
              decoration: InputDecoration(
                hintText: 'Search zone, ICAO, NOTAM...',
                prefixIcon: const Icon(Icons.search, size: 20),
                isDense: true,
                contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
              ),
              style: const TextStyle(fontSize: 13),
              onChanged: (v) => setState(() => _searchQuery = v.trim()),
            ),
          ),
          const Divider(height: 1),

          // ── Feature list ──
          Expanded(
            child: features.isEmpty
                ? const Center(child: Text('No features match'))
                : ListView.builder(
                    itemCount: features.length,
                    itemBuilder: (_, i) => _AeroTile(feature: features[i]),
                  ),
          ),
        ],
      ),
    );
  }
}

class _AeroTile extends StatelessWidget {
  const _AeroTile({required this.feature});
  final AeroFeature feature;

  @override
  Widget build(BuildContext context) {
    final color = _layerColors[feature.layer] ?? Colors.grey;
    return ListTile(
      leading: CircleAvatar(
        radius: 16,
        backgroundColor: color.withAlpha(50),
        child: Icon(_iconFor(feature.layer), size: 18, color: color),
      ),
      title: Text(feature.name, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
      subtitle: Text(
        feature.subtitle,
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
        style: const TextStyle(fontSize: 11),
      ),
      trailing: feature.upperLimitM != null
          ? Text('${feature.upperLimitM!.round()} m', style: TextStyle(fontSize: 11, color: color))
          : null,
      dense: true,
      onTap: () => _showDetail(context),
    );
  }

  IconData _iconFor(String layer) {
    switch (layer) {
      case 'uas_zones':    return Icons.shield;
      case 'notam':        return Icons.warning_amber;
      case 'notam_all':    return Icons.article;
      case 'ctr':          return Icons.radar;
      case 'tma':          return Icons.blur_circular;
      case 'airports':     return Icons.local_airport;
      case 'lower_routes': return Icons.route;
      default:             return Icons.layers;
    }
  }

  void _showDetail(BuildContext context) {
    final p = feature.properties;
    showModalBottomSheet(
      context: context,
      builder: (_) => Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              Icon(_iconFor(feature.layer), color: _layerColors[feature.layer]),
              const SizedBox(width: 8),
              Expanded(child: Text(feature.name, style: Theme.of(context).textTheme.headlineSmall)),
            ]),
            const SizedBox(height: 8),
            _row('Layer', AeroDataService.instance.labelFor(feature.layer)),
            if (feature.lowerLimitRaw.isNotEmpty)
              _row('Lower', '${feature.lowerLimitRaw}  (${feature.lowerLimitM ?? "?"}m)'),
            if (feature.upperLimitRaw.isNotEmpty)
              _row('Upper', '${feature.upperLimitRaw}  (${feature.upperLimitM ?? "?"}m)'),
            if (p['contact'] != null) _row('Contact', p['contact'] as String),
            if (p['status'] != null) _row('Status', p['status'] as String),
            if (p['airport'] != null) _row('Airport', p['airport'] as String),
            if (p['icao'] != null) _row('ICAO', p['icao'] as String),
            if (p['valid_from'] != null) _row('From', p['valid_from'] as String),
            if (p['valid_to'] != null) _row('To', p['valid_to'] as String),
            if (p['route_designator'] != null) _row('Route', p['route_designator'] as String),
            if (p['from_fix'] != null) _row('From fix', p['from_fix'] as String),
            if (p['to_fix'] != null) _row('To fix', p['to_fix'] as String),
            const SizedBox(height: 12),
          ],
        ),
      ),
    );
  }

  Widget _row(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 90,
            child: Text('$label:', style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 12)),
          ),
          Expanded(child: Text(value, style: const TextStyle(fontSize: 12))),
        ],
      ),
    );
  }
}
