DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'postgis') THEN
    CREATE EXTENSION IF NOT EXISTS postgis;
  ELSE
    RAISE NOTICE 'PostGIS extension is not available in this database instance. Skipping airspace schema.';
    RETURN;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS raw_airspace_sources (
  id BIGSERIAL PRIMARY KEY,
  source TEXT NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL,
  payload_json JSONB NOT NULL,
  checksum TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_airspace_source_source_fetched_at
  ON raw_airspace_sources (source, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_raw_airspace_source_checksum
  ON raw_airspace_sources (checksum);

CREATE TABLE IF NOT EXISTS airspace_versions (
  version_id UUID PRIMARY KEY,
  source TEXT NOT NULL,
  imported_at TIMESTAMPTZ NOT NULL,
  record_count INTEGER NOT NULL,
  checksum TEXT NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_airspace_versions_source_active
  ON airspace_versions (source, is_active, imported_at DESC);
CREATE INDEX IF NOT EXISTS idx_airspace_versions_source_checksum
  ON airspace_versions (source, checksum);

CREATE TABLE IF NOT EXISTS airspace_zones (
  id BIGSERIAL PRIMARY KEY,
  zone_id TEXT NOT NULL,
  version_id UUID NOT NULL REFERENCES airspace_versions(version_id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  lower_altitude_m DOUBLE PRECISION,
  upper_altitude_m DOUBLE PRECISION,
  geometry geometry(Geometry, 4326) NOT NULL,
  valid_from TIMESTAMPTZ,
  valid_to TIMESTAMPTZ,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (zone_id, version_id)
);

CREATE INDEX IF NOT EXISTS idx_airspace_zones_geometry_gist
  ON airspace_zones USING GIST (geometry);
CREATE INDEX IF NOT EXISTS idx_airspace_zones_source_category
  ON airspace_zones (source, category);
CREATE INDEX IF NOT EXISTS idx_airspace_zones_valid_window
  ON airspace_zones (valid_from, valid_to);

CREATE OR REPLACE VIEW airspace_zones_active AS
SELECT z.*
FROM airspace_zones z
JOIN airspace_versions v ON v.version_id = z.version_id
WHERE v.is_active = TRUE;
