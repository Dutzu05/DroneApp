from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from modules.flight_plans.module import build_flight_plans_module


class FlightPlansModuleTest(unittest.TestCase):
    def _workspace_temp_dir(self) -> Path:
        path = Path.cwd() / '.tmp' / f'flight-plan-test-{uuid.uuid4().hex}'
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def _build_module(self, pdf_dir: Path, *, repo_should_fail: bool = False):
        class FakeRepo:
            def __init__(self):
                self.created = []
                self.cancelled = []

            def create(self, owner, plan):
                if repo_should_fail:
                    raise RuntimeError('repo failure')
                self.created.append((owner, plan))
                return {
                    'public_id': plan['public_id'],
                    'workflow_status': 'planned',
                    'runtime_state': 'upcoming',
                    'owner_email': owner['email'],
                    'owner_display_name': owner.get('display_name', ''),
                    'location_name': plan['location_name'],
                    'selected_twr': plan['selected_twr'],
                    'risk_level': plan['risk_level'],
                    'pdf_rel_path': plan['pdf_rel_path'],
                }

            def list(self, *, owner_email, include_past, include_cancelled):
                return [
                    {
                        'public_id': 'FP-1',
                        'workflow_status': 'planned',
                        'runtime_state': 'ongoing',
                        'owner_email': owner_email or 'pilot@example.com',
                    }
                ]

            def get(self, public_id, *, owner_email=None):
                return {'public_id': public_id, 'pdf_rel_path': '.data/flight_plans/test.pdf'}

            def cancel(self, public_id, *, owner_email):
                self.cancelled.append((public_id, owner_email))
                return {
                    'public_id': public_id,
                    'workflow_status': 'cancelled',
                    'runtime_state': 'cancelled',
                    'owner_email': owner_email,
                }

            def approve(self, public_id, *, approver_email, note=''):
                return {
                    'public_id': public_id,
                    'workflow_status': 'planned',
                    'runtime_state': 'upcoming',
                    'owner_email': 'pilot@example.com',
                    'approval_status': 'approved',
                    'approved_by_email': approver_email,
                    'approval_note': note,
                }

        class FakeGateway:
            def build_plan(self, payload, owner):
                return {
                    'public_id': 'FP-TEST-1',
                    'location_name': payload['location_name'],
                    'selected_twr': payload['selected_twr'],
                    'risk_level': 'LOW',
                    'airspace_assessment': {'summary': 'ok'},
                }

            def build_pdf_payload(self, plan):
                return {'public_id': plan['public_id']}

            def generate_pdf(self, plan, output_path):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b'%PDF-1.4\n%fake\n')
                return output_path

            def assess(self, payload):
                return {'risk_level': 'LOW'}

            def twr_options(self):
                return [{'icao': 'LROP'}]

        repo = FakeRepo()
        gateway = FakeGateway()
        module = build_flight_plans_module(
            pdf_dir=pdf_dir,
            create_plan_repo=repo.create,
            list_plans_repo=repo.list,
            get_plan_repo=repo.get,
            cancel_plan_repo=repo.cancel,
            approve_plan_repo=repo.approve,
            build_flight_plan=gateway.build_plan,
            build_anexa_payload=gateway.build_pdf_payload,
            generate_pdf=gateway.generate_pdf,
            assess_flight_area_fn=lambda area, alt: {'risk_level': 'LOW'},
            build_circle_area=lambda lon, lat, radius: {'kind': 'circle'},
            build_polygon_area=lambda points: {'kind': 'polygon'},
            twr_options=gateway.twr_options,
        )
        return module, repo

    def test_create_enriches_and_persists_plan(self):
        tmp_dir = self._workspace_temp_dir()
        module, repo = self._build_module(tmp_dir / 'data' / 'flight_plans')

        result = module.create({'location_name': 'Zone A', 'selected_twr': 'LROP'}, {'email': 'pilot@example.com'})

        self.assertEqual(result['public_id'], 'FP-TEST-1')
        self.assertTrue(result['download_url'].endswith('/api/flight-plans/FP-TEST-1/pdf'))
        self.assertTrue(result['can_cancel'])
        self.assertEqual(len(repo.created), 1)

    def test_create_removes_pdf_if_repo_fails(self):
        tmp_dir = self._workspace_temp_dir()
        pdf_dir = tmp_dir / 'data' / 'flight_plans'
        module, _ = self._build_module(pdf_dir, repo_should_fail=True)

        with self.assertRaises(RuntimeError):
            module.create({'location_name': 'Zone A', 'selected_twr': 'LROP'}, {'email': 'pilot@example.com'})

        self.assertFalse((pdf_dir / 'FP-TEST-1.pdf').exists())

    def test_list_and_cancel_enrich_results(self):
        tmp_dir = self._workspace_temp_dir()
        module, repo = self._build_module(tmp_dir / 'data' / 'flight_plans')

        listed = module.list(owner_email='pilot@example.com', include_past=True, include_cancelled=True)
        cancelled = module.cancel('FP-1', {'email': 'pilot@example.com'})

        self.assertEqual(listed[0]['download_url'], '/api/flight-plans/FP-1/pdf')
        self.assertTrue(listed[0]['can_cancel'])
        self.assertFalse(cancelled['can_cancel'])
        self.assertEqual(repo.cancelled[0], ('FP-1', 'pilot@example.com'))

    def test_assess_get_approve_and_twr_options_delegate(self):
        tmp_dir = self._workspace_temp_dir()
        module, _ = self._build_module(tmp_dir / 'data' / 'flight_plans')

        assessed = module.assess({'area_kind': 'polygon', 'polygon_points': [[1, 2], [3, 4]], 'max_altitude_m': 90})
        loaded = module.get('FP-42', owner_email='pilot@example.com')
        approved = module.approve('FP-42', approver_email='ops@example.com', note='ready')
        twr_options = module.twr_options()

        self.assertEqual(assessed['risk_level'], 'LOW')
        self.assertEqual(loaded['public_id'], 'FP-42')
        self.assertEqual(approved['approval_status'], 'approved')
        self.assertEqual(approved['approval_note'], 'ready')
        self.assertEqual(twr_options, [{'icao': 'LROP'}])

    def test_approve_raises_when_repo_returns_none(self):
        tmp_dir = self._workspace_temp_dir()
        module, _ = self._build_module(tmp_dir / 'data' / 'flight_plans')
        module.repo._approve_plan = lambda public_id, *, approver_email, note='': None

        with self.assertRaisesRegex(ValueError, 'Flight plan cannot be approved'):
            module.approve('FP-99', approver_email='ops@example.com')

    def test_cancel_raises_when_repo_returns_none(self):
        tmp_dir = self._workspace_temp_dir()
        module, _ = self._build_module(tmp_dir / 'data' / 'flight_plans')
        module.repo._cancel_plan = lambda public_id, *, owner_email: None

        with self.assertRaisesRegex(ValueError, 'Flight plan cannot be cancelled'):
            module.cancel('FP-99', {'email': 'pilot@example.com'})


if __name__ == '__main__':
    unittest.main()
