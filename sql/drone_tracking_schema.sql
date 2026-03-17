CREATE TABLE IF NOT EXISTS drone_devices (
  id BIGSERIAL PRIMARY KEY,
  drone_id TEXT UNIQUE NOT NULL,
  owner_user_id BIGINT REFERENCES app_users(id) ON DELETE SET NULL,
  owner_email TEXT NOT NULL,
  owner_display_name TEXT NOT NULL DEFAULT '',
  flight_plan_public_id TEXT REFERENCES flight_plans(public_id) ON DELETE SET NULL,
  label TEXT NOT NULL DEFAULT '',
  is_mock BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_drone_devices_owner_email ON drone_devices(owner_email);
CREATE INDEX IF NOT EXISTS idx_drone_devices_flight_plan_public_id ON drone_devices(flight_plan_public_id);

CREATE TABLE IF NOT EXISTS drone_telemetry (
  id BIGSERIAL PRIMARY KEY,
  drone_device_id BIGINT NOT NULL REFERENCES drone_devices(id) ON DELETE CASCADE,
  drone_id TEXT NOT NULL,
  flight_plan_public_id TEXT REFERENCES flight_plans(public_id) ON DELETE SET NULL,
  latitude DOUBLE PRECISION NOT NULL,
  longitude DOUBLE PRECISION NOT NULL,
  altitude DOUBLE PRECISION NOT NULL,
  heading DOUBLE PRECISION NOT NULL,
  pitch DOUBLE PRECISION NOT NULL,
  roll DOUBLE PRECISION NOT NULL,
  speed DOUBLE PRECISION NOT NULL,
  telemetry_timestamp TIMESTAMPTZ NOT NULL,
  battery_level DOUBLE PRECISION NOT NULL CHECK (battery_level >= 0 AND battery_level <= 100),
  status TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'mock',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_drone_telemetry_drone_id_timestamp ON drone_telemetry(drone_id, telemetry_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_drone_telemetry_flight_plan_timestamp ON drone_telemetry(flight_plan_public_id, telemetry_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_drone_telemetry_timestamp ON drone_telemetry(telemetry_timestamp DESC);
