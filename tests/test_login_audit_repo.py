from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from modules.auth.repo.login_audit_repo import LoginAuditRepository


class LoginAuditRepositoryTest(unittest.TestCase):
    def test_record_login_persists_and_sorts_accounts(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = LoginAuditRepository(Path(tmp_dir) / 'logged_accounts.json')
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
            self.assertTrue((Path(tmp_dir) / 'logged_accounts.json').exists())


if __name__ == '__main__':
    unittest.main()
