import 'dart:convert';

import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqflite/sqflite.dart';

import '../models/cached_map_tile.dart';

class LocalCacheDb {
  LocalCacheDb._();
  static final LocalCacheDb instance = LocalCacheDb._();

  Database? _db;

  Future<Database> get db async {
    if (_db != null) return _db!;

    final dir = await getApplicationSupportDirectory();
    final dbPath = p.join(dir.path, 'drone_cache.db');

    _db = await openDatabase(
      dbPath,
      version: 1,
      onCreate: (database, version) async {
        await database.execute('''
          CREATE TABLE map_tiles (
            tile_key TEXT PRIMARY KEY,
            z INTEGER NOT NULL,
            x INTEGER NOT NULL,
            y INTEGER NOT NULL,
            bytes BLOB NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT
          )
        ''');

        await database.execute('''
          CREATE TABLE zone_cache (
            cache_key TEXT PRIMARY KEY,
            json_payload TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT
          )
        ''');

        await database.execute(
          'CREATE INDEX idx_map_tiles_coords ON map_tiles(z, x, y)',
        );
      },
    );

    return _db!;
  }

  static String tileKey(int z, int x, int y) => '$z/$x/$y';

  Future<void> putTile({
    required int z,
    required int x,
    required int y,
    required List<int> bytes,
    DateTime? expiresAt,
  }) async {
    final database = await db;
    final now = DateTime.now().toUtc().toIso8601String();

    await database.insert(
      'map_tiles',
      {
        'tile_key': tileKey(z, x, y),
        'z': z,
        'x': x,
        'y': y,
        'bytes': bytes,
        'updated_at': now,
        'expires_at': expiresAt?.toUtc().toIso8601String(),
      },
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  Future<CachedMapTile?> getTile({
    required int z,
    required int x,
    required int y,
  }) async {
    final database = await db;
    final rows = await database.query(
      'map_tiles',
      where: 'tile_key = ?',
      whereArgs: [tileKey(z, x, y)],
      limit: 1,
    );

    if (rows.isEmpty) return null;

    final row = rows.first;
    final expires = row['expires_at'] as String?;
    if (expires != null && DateTime.parse(expires).isBefore(DateTime.now().toUtc())) {
      await database.delete(
        'map_tiles',
        where: 'tile_key = ?',
        whereArgs: [row['tile_key']],
      );
      return null;
    }

    return CachedMapTile(
      key: row['tile_key'] as String,
      z: row['z'] as int,
      x: row['x'] as int,
      y: row['y'] as int,
      bytes: row['bytes'] as List<int>,
      updatedAt: DateTime.parse(row['updated_at'] as String),
      expiresAt: expires == null ? null : DateTime.parse(expires),
    );
  }

  Future<int> pruneExpiredTiles() async {
    final database = await db;
    final now = DateTime.now().toUtc().toIso8601String();
    return database.delete(
      'map_tiles',
      where: 'expires_at IS NOT NULL AND expires_at < ?',
      whereArgs: [now],
    );
  }

  Future<void> putRestrictionZones({
    required String key,
    required List<Map<String, dynamic>> zones,
    DateTime? expiresAt,
  }) async {
    final database = await db;
    final now = DateTime.now().toUtc().toIso8601String();
    await database.insert(
      'zone_cache',
      {
        'cache_key': key,
        'json_payload': jsonEncode(zones),
        'updated_at': now,
        'expires_at': expiresAt?.toUtc().toIso8601String(),
      },
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  Future<List<Map<String, dynamic>>?> getRestrictionZones({
    required String key,
  }) async {
    final database = await db;
    final rows = await database.query(
      'zone_cache',
      where: 'cache_key = ?',
      whereArgs: [key],
      limit: 1,
    );

    if (rows.isEmpty) return null;

    final row = rows.first;
    final expires = row['expires_at'] as String?;
    if (expires != null && DateTime.parse(expires).isBefore(DateTime.now().toUtc())) {
      await database.delete(
        'zone_cache',
        where: 'cache_key = ?',
        whereArgs: [key],
      );
      return null;
    }

    final decoded = jsonDecode(row['json_payload'] as String) as List<dynamic>;
    return decoded
        .map((e) => Map<String, dynamic>.from(e as Map))
        .toList(growable: false);
  }
}
