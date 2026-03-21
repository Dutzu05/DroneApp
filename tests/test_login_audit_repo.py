from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

from modules.auth.repo.login_audit_repo import LoginAuditRepository


class LoginAuditRepositoryTest(unittest.TestCase):
    def _workspace_temp_dir(self) -> Path:
        path = Path.cwd() / '.tmp' / f'login-audit-test-{uuid.uuid4().hex}'
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_record_login_persists_and_sorts_accounts(self):
        tmp_dir = self._workspace_temp_dir()
        repo = LoginAuditRepository(tmp_dir / 'logged_accounts.json')
        repo.record_login(
            email='z@example.com',
            display_name='Zed',
            google_user_id='gid-z',
            source_ip='127.0.0.1',
            app_name='web',
            now_utc_iso='2026-03-16T10:00:00Z',
        )
        repo.record_login(
            email='a@example.com',
            display_name='Ada',
            google_user_id='gid-a',
            source_ip='127.0.0.2',
            app_name='web',
            now_utc_iso='2026-03-16T11:00:00Z',
        )

        rows = repo.list_accounts()

        self.assertEqual(rows[0]['email'], 'a@example.com')
        self.assertEqual(rows[1]['email'], 'z@example.com')
        self.assertTrue((tmp_dir / 'logged_accounts.json').exists())

    def test_load_ignores_invalid_json_and_invalid_rows(self):
        tmp_dir = self._workspace_temp_dir()
        file_path = tmp_dir / 'logged_accounts.json'
        file_path.write_text('{not valid json', encoding='utf-8')

        repo = LoginAuditRepository(file_path)

        self.assertEqual(repo.list_accounts(), [])

        file_path.write_text(
            json.dumps(
                {
                    'accounts': [
                        'bad-row',
                        {'email': '  ', 'display_name': 'Missing'},
                        {'email': 'Pilot@Example.com', 'display_name': 'Pilot'},
                    ]
                }
            ),
            encoding='utf-8',
        )

        reloaded_repo = LoginAuditRepository(file_path)
        rows = reloaded_repo.list_accounts()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['email'], 'Pilot@Example.com')
        self.assertEqual(rows[0]['display_name'], 'Pilot')

    def test_record_login_updates_existing_row_without_overwriting_with_blank_values(self):
        tmp_dir = self._workspace_temp_dir()
        file_path = tmp_dir / 'logged_accounts.json'
        file_path.write_text(
            json.dumps(
                [
                    {
                        'email': 'Pilot@Example.com',
                        'display_name': 'Pilot One',
                        'google_user_id': 'gid-1',
                        'first_seen': '2026-03-16T10:00:00Z',
                        'last_seen': '2026-03-16T10:00:00Z',
                    }
                ]
            ),
            encoding='utf-8',
        )
        repo = LoginAuditRepository(file_path)

        row = repo.record_login(
            email=' pilot@example.com ',
            display_name='',
            google_user_id='',
            source_ip='127.0.0.9',
            app_name='admin',
            now_utc_iso='2026-03-16T12:00:00Z',
        )

        self.assertEqual(row['display_name'], 'Pilot One')
        self.assertEqual(row['google_user_id'], 'gid-1')
        self.assertEqual(row['last_ip'], '127.0.0.9')
        self.assertEqual(repo.list_accounts()[0]['last_app'], 'admin')

    def test_record_login_requires_email(self):
        tmp_dir = self._workspace_temp_dir()
        repo = LoginAuditRepository(tmp_dir / 'logged_accounts.json')

        with self.assertRaisesRegex(ValueError, 'email is required'):
            repo.record_login(
                email='  ',
                display_name='Pilot',
                google_user_id='gid-1',
                source_ip='127.0.0.1',
                app_name='web',
                now_utc_iso='2026-03-16T10:00:00Z',
            )


if __name__ == '__main__':
    unittest.main()
