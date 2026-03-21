from __future__ import annotations

import unittest

from backend.drone_tracking.services.scene_3d_service import Drone3DSceneService


class _DroneRepoStub:
    def get_live_drone(self, drone_id, *, owner_email=None, include_upcoming=False, only_ongoing=False):
        if drone_id != 'MOCK-ALPHA':
            return None
        return {
            'drone_id': 'MOCK-ALPHA',
            'latitude': 46.7712,
            'longitude': 23.6236,
            'altitude': 80.0,
            'heading': 120.0,
            'pitch': 2.0,
            'roll': 1.0,
            'speed': 12.4,
            'battery_level': 76.0,
            'status': 'flying',
            'flight_plan_public_id': 'FP-ALPHA',
            'location_name': 'Cluj',
            'owner_email': 'pilot@example.com',
        }

    def list_live_drones(self, *, owner_email=None, include_upcoming=False, only_ongoing=False):
        return [
            {
                'drone_id': 'MOCK-ALPHA',
                'latitude': 46.7712,
                'longitude': 23.6236,
                'altitude': 80.0,
                'heading': 120.0,
                'status': 'flying',
                'flight_plan_public_id': 'FP-ALPHA',
            },
            {
                'drone_id': 'MOCK-BRAVO',
                'latitude': 46.7750,
                'longitude': 23.6300,
                'altitude': 95.0,
                'heading': 40.0,
                'status': 'flying',
                'flight_plan_public_id': 'FP-BRAVO',
            },
        ]

    def telemetry_history(self, drone_id, *, limit=30):
        self.last_history_limit = limit
        return [
            {
                'drone_id': drone_id,
                'latitude': 46.7700,
                'longitude': 23.6200,
                'altitude': 70.0,
                'timestamp': '2026-03-17T09:00:00Z',
            },
            {
                'drone_id': drone_id,
                'latitude': 46.7712,
                'longitude': 23.6236,
                'altitude': 80.0,
                'timestamp': '2026-03-17T09:00:03Z',
            },
        ]


class _AirspaceQueryStub:
    def __init__(self, zones=None):
        self.zones = zones or [
            {
                'zone_id': 'ctr-clj',
                'source': 'romatsa_wfs_ctr',
                'name': 'Cluj CTR',
                'category': 'ctr',
                'lower_altitude_m': 0.0,
                'upper_altitude_m': 1500.0,
                'distance_m': 1800.0,
                'geometry': {
                    'type': 'Polygon',
                    'coordinates': [[[23.60, 46.76], [23.64, 46.76], [23.64, 46.79], [23.60, 46.79], [23.60, 46.76]]],
                },
            }
        ]

    def get_zones_near(self, *, lat, lon, radius_km, categories=None):
        self.last_call = {
            'lat': lat,
            'lon': lon,
            'radius_km': radius_km,
            'categories': categories,
        }
        return {
            'zones': self.zones,
            'count': len(self.zones),
        }


class Drone3DSceneServiceTests(unittest.TestCase):
    def test_build_scene_contains_focus_drone_track_nearby_aircraft_and_zones(self):
        service = Drone3DSceneService(
            drone_repo=_DroneRepoStub(),
            airspace_query_service=_AirspaceQueryStub(),
            cesium_ion_token='token-123',
        )

        scene = service.build_scene('MOCK-ALPHA', owner_email='pilot@example.com', radius_km=10.0, admin_view=False)

        self.assertEqual(scene['drone']['drone_id'], 'MOCK-ALPHA')
        self.assertEqual(len(scene['drone']['track']), 2)
        self.assertEqual(len(scene['nearby_aircraft']), 1)
        self.assertEqual(scene['nearby_aircraft'][0]['drone_id'], 'MOCK-BRAVO')
        self.assertEqual(scene['nearby_aircraft'][0]['traffic_severity'], 'monitor')
        self.assertEqual(len(scene['traffic_alerts']), 0)
        self.assertEqual(len(scene['zones']), 1)
        self.assertEqual(scene['zones'][0]['zone_id'], 'ctr-clj')
        self.assertEqual(scene['zones'][0]['color'], '#58a6ff')
        self.assertEqual(scene['scene']['radius_km'], 10.0)
        self.assertEqual(scene['scene']['focus_region']['radius_m'], 10_000.0)
        self.assertEqual(scene['scene']['terrain']['provider'], 'ion')
        self.assertEqual(scene['scene']['terrain']['ion_token'], 'token-123')
        self.assertEqual(scene['scene']['imagery']['provider'], 'google_photorealistic_3d_tiles')
        self.assertEqual(scene['scene']['imagery']['kind'], 'photorealistic_3d_tiles')
        self.assertEqual(scene['scene']['buildings']['provider'], 'google_photorealistic_3d_tiles')
        self.assertEqual(scene['scene']['follow']['refresh_interval_s'], 5)
        self.assertTrue(scene['scene']['rendering']['airspace_volumes_default_visible'])
        self.assertEqual(scene['scene']['rendering']['airspace_altitude_mode'], 'relative_to_ground_visual')
        self.assertEqual(scene['scene']['rendering']['airspace_volume_max_altitude_m'], 6000.0)
        self.assertEqual(len(scene['obstacles']), 6)

    def test_build_scene_raises_for_missing_drone(self):
        service = Drone3DSceneService(
            drone_repo=_DroneRepoStub(),
            airspace_query_service=_AirspaceQueryStub(),
        )

        with self.assertRaises(LookupError):
            service.build_scene('UNKNOWN', owner_email='pilot@example.com')

    def test_build_scene_converts_web_mercator_zone_geometry(self):
        service = Drone3DSceneService(
            drone_repo=_DroneRepoStub(),
            airspace_query_service=_AirspaceQueryStub(
                zones=[
                    {
                        'zone_id': 'tma-bucharest',
                        'source': 'romatsa_wfs_tma',
                        'name': 'Bucharest TMA',
                        'category': 'tma',
                        'lower_altitude_m': 300.0,
                        'upper_altitude_m': 2400.0,
                        'distance_m': 900.0,
                        'geometry': {
                            'type': 'Polygon',
                            'coordinates': [[
                                [2894303.63, 5543147.20],
                                [2898756.41, 5543147.20],
                                [2898756.41, 5546294.78],
                                [2894303.63, 5546294.78],
                            ]],
                        },
                    }
                ]
            ),
        )

        scene = service.build_scene('MOCK-ALPHA', owner_email='pilot@example.com', radius_km=10.0, admin_view=False)

        first = scene['zones'][0]['geometry']['coordinates'][0][0]
        self.assertAlmostEqual(first[0], 26.0, places=1)
        self.assertAlmostEqual(first[1], 44.5, places=1)

    def test_build_scene_includes_imminent_traffic_alerts_for_close_intruder(self):
        repo = _DroneRepoStub()
        repo.list_live_drones = lambda **kwargs: [
            {
                'drone_id': 'MOCK-ALPHA',
                'latitude': 46.7712,
                'longitude': 23.6236,
                'altitude': 80.0,
                'heading': 90.0,
                'pitch': 0.0,
                'roll': 0.0,
                'speed': 12.0,
                'status': 'flying',
                'flight_plan_public_id': 'FP-ALPHA',
                'owner_email': 'pilot@example.com',
            },
            {
                'drone_id': 'TRAFFIC-1',
                'latitude': 46.7712,
                'longitude': 23.6244,
                'altitude': 86.0,
                'heading': 270.0,
                'pitch': 0.0,
                'roll': 0.0,
                'speed': 12.0,
                'status': 'flying',
                'flight_plan_public_id': 'FP-TRAFFIC',
                'owner_email': 'traffic@example.com',
                'owner_display_name': 'Traffic Demo',
            },
        ]
        service = Drone3DSceneService(
            drone_repo=repo,
            airspace_query_service=_AirspaceQueryStub(),
        )

        scene = service.build_scene('MOCK-ALPHA', owner_email='pilot@example.com', radius_km=10.0, admin_view=False)

        self.assertEqual(scene['nearby_aircraft'][0]['traffic_severity'], 'imminent')
        self.assertEqual(scene['traffic_alerts'][0]['severity'], 'imminent')

    def test_build_scene_marks_distant_intruder_as_safe(self):
        repo = _DroneRepoStub()
        repo.list_live_drones = lambda **kwargs: [
            {
                'drone_id': 'MOCK-ALPHA',
                'latitude': 46.7712,
                'longitude': 23.6236,
                'altitude': 80.0,
                'heading': 90.0,
                'pitch': 0.0,
                'roll': 0.0,
                'speed': 12.0,
                'status': 'flying',
                'flight_plan_public_id': 'FP-ALPHA',
                'owner_email': 'pilot@example.com',
            },
            {
                'drone_id': 'TRAFFIC-LRCL-01',
                'latitude': 46.7810,
                'longitude': 23.6515,
                'altitude': 86.0,
                'heading': 210.0,
                'pitch': 0.0,
                'roll': 0.0,
                'speed': 10.0,
                'status': 'flying',
                'flight_plan_public_id': 'FP-TRAFFIC-SAFE',
                'owner_email': 'traffic-demo@romatsa.local',
                'owner_display_name': 'ROMATSA Traffic Demo',
            },
        ]
        service = Drone3DSceneService(
            drone_repo=repo,
            airspace_query_service=_AirspaceQueryStub(),
        )

        scene = service.build_scene('MOCK-ALPHA', owner_email='pilot@example.com', radius_km=5.0, admin_view=False)

        self.assertEqual(scene['nearby_aircraft'][0]['traffic_severity'], 'safe')
        self.assertEqual(scene['traffic_alerts'], [])


if __name__ == '__main__':
    unittest.main()
