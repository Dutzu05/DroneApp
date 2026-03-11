class CachedMapTile {
  const CachedMapTile({
    required this.key,
    required this.z,
    required this.x,
    required this.y,
    required this.bytes,
    required this.updatedAt,
    this.expiresAt,
  });

  final String key;
  final int z;
  final int x;
  final int y;
  final List<int> bytes;
  final DateTime updatedAt;
  final DateTime? expiresAt;
}
