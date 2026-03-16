from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import backend_auth


class BackendAuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = backend_auth.DATA_DIR
        self.original_secret_file = backend_auth.SESSION_SECRET_FILE
        backend_auth.DATA_DIR = Path(self.tmp_dir.name)
        backend_auth.SESSION_SECRET_FILE = backend_auth.DATA_DIR / 'session_secret'

    def tearDown(self):
        backend_auth.DATA_DIR = self.original_data_dir
        backend_auth.SESSION_SECRET_FILE = self.original_secret_file
        self.tmp_dir.cleanup()

    def test_create_and_decode_session_token(self):
        token = backend_auth.create_session_token({
            'email': 'pilot@example.com',
            'display_name': 'Pilot',
            'google_user_id': 'gid-1',
        })

        decoded = backend_auth.decode_session_token(token)

        self.assertIsNotNone(decoded)
        self.assertEqual(decoded['email'], 'pilot@example.com')
        self.assertEqual(decoded['display_name'], 'Pilot')

    def test_session_user_from_headers_handles_google_state_cookie(self):
        token = backend_auth.create_session_token({
            'email': 'pilot@example.com',
            'display_name': 'Pilot',
            'google_user_id': 'gid-1',
        })
        headers = {
            'Cookie': 'csrftoken=abc; g_state={"i_l":0,"i_e":{"enable_itp_optimization":0}}; '
            f'drone_session={token}'
        }

        user = backend_auth.session_user_from_headers(headers)

        self.assertIsNotNone(user)
        self.assertEqual(user['email'], 'pilot@example.com')

    def test_clear_cookie_header_expires_cookie(self):
        self.assertIn('Max-Age=0', backend_auth.clear_session_cookie_header())


if __name__ == '__main__':
    unittest.main()
