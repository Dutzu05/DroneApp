from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from modules.flight_plans.gateways.pdf_gateway import FlightPlanGateway
from modules.flight_plans.repo.flight_plans_repo import FlightPlansRepository


class FlightPlanGatewayAndRepositoryTest(unittest.TestCase):
    def _workspace_temp_dir(self) -> Path:
        path = Path.cwd() / '.tmp' / f'flight-plan-gateway-test-{uuid.uuid4().hex}'
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_gateway_delegates_build_pdf_and_twr_options(self):
        calls: list[tuple] = []

        gateway = FlightPlanGateway(
            build_flight_plan=lambda payload, owner: calls.append(('build', payload, owner)) or {'public_id': 'FP-1'},
            build_anexa_payload=lambda plan: calls.append(('payload', plan['public_id'])) or {'id': plan['public_id']},
            generate_pdf=lambda plan, output_path: calls.append(('pdf', plan['public_id'], output_path)) or output_path,
            assess_flight_area=lambda area, alt: calls.append(('assess', area, alt)) or {'risk_level': 'LOW'},
            build_circle_area=lambda lon, lat, radius: {'kind': 'circle', 'lon': lon, 'lat': lat, 'radius': radius},
            build_polygon_area=lambda points: {'kind': 'polygon', 'points': points},
            twr_options=lambda: [{'icao': 'LROP'}],
        )

        plan = gateway.build_plan({'location_name': 'Zone A'}, {'email': 'pilot@example.com'})
        pdf_payload = gateway.build_pdf_payload({'public_id': 'FP-1'})
        pdf_path = gateway.generate_pdf({'public_id': 'FP-1'}, Path('out.pdf'))

        self.assertEqual(plan['public_id'], 'FP-1')
        self.assertEqual(pdf_payload, {'id': 'FP-1'})
        self.assertEqual(pdf_path, Path('out.pdf'))
        self.assertEqual(gateway.twr_options(), [{'icao': 'LROP'}])
        self.assertEqual(calls[0][0], 'build')
        self.assertEqual(calls[1], ('payload', 'FP-1'))
        self.assertEqual(calls[2][0], 'pdf')

    def test_gateway_assess_builds_circle_and_polygon_areas(self):
        calls: list[tuple] = []

        gateway = FlightPlanGateway(
            build_flight_plan=lambda payload, owner: payload,
            build_anexa_payload=lambda plan: plan,
            generate_pdf=lambda plan, output_path: output_path,
            assess_flight_area=lambda area, alt: calls.append((area, alt)) or {'area': area, 'alt': alt},
            build_circle_area=lambda lon, lat, radius: {'kind': 'circle', 'center': [lon, lat], 'radius_m': radius},
            build_polygon_area=lambda points: {'kind': 'polygon', 'points': points},
            twr_options=lambda: [],
        )

        circle_result = gateway.assess(
            {
                'center_lon': '26.1',
                'center_lat': '44.4',
                'radius_m': '250',
                'max_altitude_m': '120',
            }
        )
        polygon_result = gateway.assess(
            {
                'area_kind': 'polygon',
                'polygon_points': [[26.1, 44.4], [26.2, 44.5], [26.1, 44.6]],
                'max_altitude_m': '80',
            }
        )

        self.assertEqual(circle_result['area']['kind'], 'circle')
        self.assertEqual(circle_result['alt'], 120.0)
        self.assertEqual(polygon_result['area']['kind'], 'polygon')
        self.assertEqual(polygon_result['alt'], 80.0)
        self.assertEqual(calls[0][0]['radius_m'], 250.0)
        self.assertEqual(calls[1][0]['points'][1], [26.2, 44.5])

    def test_repository_delegates_all_operations(self):
        calls: list[tuple] = []

        repo = FlightPlansRepository(
            create_plan=lambda owner, plan: calls.append(('create', owner['email'], plan['public_id'])) or {'created': True},
            list_plans=lambda **kwargs: calls.append(('list', kwargs)) or [{'public_id': 'FP-1'}],
            get_plan=lambda public_id, **kwargs: calls.append(('get', public_id, kwargs)) or {'public_id': public_id},
            cancel_plan=lambda public_id, **kwargs: calls.append(('cancel', public_id, kwargs)) or {'public_id': public_id, 'workflow_status': 'cancelled'},
            approve_plan=lambda public_id, **kwargs: calls.append(('approve', public_id, kwargs)) or {'public_id': public_id, 'approval_status': 'approved'},
        )

        self.assertEqual(repo.create({'email': 'pilot@example.com'}, {'public_id': 'FP-1'}), {'created': True})
        self.assertEqual(repo.list(owner_email='pilot@example.com', include_past=True, include_cancelled=False)[0]['public_id'], 'FP-1')
        self.assertEqual(repo.get('FP-1', owner_email='pilot@example.com')['public_id'], 'FP-1')
        self.assertEqual(repo.cancel('FP-1', owner_email='pilot@example.com')['workflow_status'], 'cancelled')
        self.assertEqual(repo.approve('FP-1', approver_email='ops@example.com', note='ok')['approval_status'], 'approved')
        self.assertEqual([call[0] for call in calls], ['create', 'list', 'get', 'cancel', 'approve'])
