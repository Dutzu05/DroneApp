-- Restriction zones sourced from ROMATSA (Romanian Air Traffic Services)
-- GeoJSON data fetched from: https://flightplan.romatsa.ro/init/drones
-- Converted by: scripts/fetch_restriction_zones.py

CREATE TABLE IF NOT EXISTS restriction_zones (
  id BIGSERIAL PRIMARY KEY,
  zone_code TEXT UNIQUE NOT NULL,            -- e.g. "RZ 1001"
  status TEXT NOT NULL DEFAULT 'RESTRICTED', -- RESTRICTED | BY NOTAM
  lower_lim_raw TEXT,                        -- original string  e.g. "GND"
  upper_lim_raw TEXT,                        -- original string  e.g. "120M AGL"
  lower_limit_m DOUBLE PRECISION,            -- metres AGL (NULL = unknown)
  upper_limit_m DOUBLE PRECISION,            -- metres AGL (NULL = unknown)
  contact TEXT,                              -- phone / email for the zone
  geometry_geojson JSONB NOT NULL,           -- GeoJSON Polygon geometry
  source TEXT DEFAULT 'ROMATSA',
  fetched_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (jsonb_typeof(geometry_geojson) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_rz_zone_code ON restriction_zones(zone_code);
CREATE INDEX IF NOT EXISTS idx_rz_geojson_gin ON restriction_zones USING GIN (geometry_geojson);

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'restriction_zones'
      AND column_name = 'lower_limit_m'
  ) THEN
    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_rz_lower_limit ON restriction_zones(lower_limit_m)';
  ELSIF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'restriction_zones'
      AND column_name = 'min_altitude_m'
  ) THEN
    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_rz_lower_limit ON restriction_zones(min_altitude_m)';
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'restriction_zones'
      AND column_name = 'upper_limit_m'
  ) THEN
    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_rz_upper_limit ON restriction_zones(upper_limit_m)';
  ELSIF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'restriction_zones'
      AND column_name = 'max_altitude_m'
  ) THEN
    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_rz_upper_limit ON restriction_zones(max_altitude_m)';
  END IF;
END $$;

-- Useful query: zones relevant for a flight at N metres AGL
-- SELECT * FROM restriction_zones
-- WHERE lower_limit_m IS NULL            -- always show unknowns
--    OR (lower_limit_m <= 50 AND (upper_limit_m IS NULL OR upper_limit_m >= 50));
