CREATE TABLE IF NOT EXISTS app_users (
  id BIGSERIAL PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  display_name TEXT NOT NULL DEFAULT '',
  google_user_id TEXT NOT NULL DEFAULT '',
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_app TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_app_users_last_seen ON app_users(last_seen_at DESC);

CREATE TABLE IF NOT EXISTS flight_plans (
  id BIGSERIAL PRIMARY KEY,
  public_id TEXT UNIQUE NOT NULL,
  owner_user_id BIGINT NOT NULL REFERENCES app_users(id) ON DELETE RESTRICT,
  owner_email TEXT NOT NULL,
  owner_display_name TEXT NOT NULL DEFAULT '',
  operator_name TEXT NOT NULL,
  operator_contact TEXT NOT NULL,
  contact_person TEXT NOT NULL,
  phone_landline TEXT,
  phone_mobile TEXT NOT NULL,
  fax TEXT,
  operator_email TEXT NOT NULL,
  uas_registration TEXT NOT NULL,
  uas_class_code TEXT NOT NULL,
  uas_class_label TEXT NOT NULL,
  category TEXT NOT NULL CHECK (category IN ('A1', 'A2', 'A3')),
  operation_mode TEXT NOT NULL CHECK (operation_mode IN ('VLOS', 'VBLOS')),
  mtom_kg NUMERIC(10,3) NOT NULL CHECK (mtom_kg > 0),
  pilot_name TEXT NOT NULL,
  pilot_phone TEXT NOT NULL,
  purpose TEXT NOT NULL,
  local_timezone TEXT NOT NULL DEFAULT 'Europe/Bucharest',
  scheduled_start_at TIMESTAMPTZ NOT NULL,
  scheduled_end_at TIMESTAMPTZ NOT NULL,
  location_name TEXT NOT NULL,
  area_kind TEXT NOT NULL CHECK (area_kind IN ('circle', 'polygon')),
  center_lon DOUBLE PRECISION,
  center_lat DOUBLE PRECISION,
  radius_m DOUBLE PRECISION,
  polygon_points JSONB,
  area_geojson JSONB NOT NULL,
  max_altitude_m DOUBLE PRECISION NOT NULL CHECK (max_altitude_m >= 0 AND max_altitude_m <= 120),
  selected_twr TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  risk_summary TEXT NOT NULL,
  airspace_assessment JSONB NOT NULL,
  anexa_payload JSONB NOT NULL,
  pdf_rel_path TEXT NOT NULL,
  approval_status TEXT NOT NULL DEFAULT 'not_required' CHECK (approval_status IN ('not_required', 'pending', 'approved', 'rejected')),
  approved_by_email TEXT,
  approval_note TEXT,
  approved_at TIMESTAMPTZ,
  workflow_status TEXT NOT NULL DEFAULT 'planned' CHECK (workflow_status IN ('planned', 'cancelled')),
  cancelled_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (scheduled_end_at > scheduled_start_at),
  CHECK (jsonb_typeof(area_geojson) = 'object'),
  CHECK (
    (area_kind = 'circle'
      AND center_lon IS NOT NULL
      AND center_lat IS NOT NULL
      AND radius_m IS NOT NULL
      AND polygon_points IS NULL)
    OR
    (area_kind = 'polygon'
      AND polygon_points IS NOT NULL
      AND radius_m IS NULL)
  )
);

CREATE INDEX IF NOT EXISTS idx_flight_plans_owner_email ON flight_plans(owner_email);
CREATE INDEX IF NOT EXISTS idx_flight_plans_schedule ON flight_plans(scheduled_start_at, scheduled_end_at);
CREATE INDEX IF NOT EXISTS idx_flight_plans_workflow_status ON flight_plans(workflow_status);
CREATE INDEX IF NOT EXISTS idx_flight_plans_approval_status ON flight_plans(approval_status);
CREATE INDEX IF NOT EXISTS idx_flight_plans_selected_twr ON flight_plans(selected_twr);
CREATE INDEX IF NOT EXISTS idx_flight_plans_area_geojson_gin ON flight_plans USING GIN (area_geojson);

ALTER TABLE flight_plans
  ADD COLUMN IF NOT EXISTS approval_status TEXT NOT NULL DEFAULT 'not_required';

ALTER TABLE flight_plans
  ADD COLUMN IF NOT EXISTS approved_by_email TEXT;

ALTER TABLE flight_plans
  ADD COLUMN IF NOT EXISTS approval_note TEXT;

ALTER TABLE flight_plans
  ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'flight_plans_approval_status_check'
  ) THEN
    ALTER TABLE flight_plans
      ADD CONSTRAINT flight_plans_approval_status_check
      CHECK (approval_status IN ('not_required', 'pending', 'approved', 'rejected'));
  END IF;
END $$;
