from __future__ import annotations

import os
import shutil
import unittest
import uuid
from pathlib import Path

from scripts import backend_auth


class BackendAuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path.cwd() / '.tmp' / f'backend-auth-test-{uuid.uuid4().hex}'
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.original_data_dir = backend_auth.DATA_DIR
        self.original_secret_file = backend_auth.SESSION_SECRET_FILE
        self.original_env = os.environ.copy()
        backend_auth.DATA_DIR = self.tmp_dir
        backend_auth.SESSION_SECRET_FILE = backend_auth.DATA_DIR / 'session_secret'

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_env)
        backend_auth.DATA_DIR = self.original_data_dir
        backend_auth.SESSION_SECRET_FILE = self.original_secret_file
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

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

    def test_cookie_header_includes_secure_and_domain_when_configured(self):
        os.environ['DRONE_ENV'] = 'production'
        os.environ['DRONE_SESSION_SECRET'] = 'secret-for-tests'
        os.environ['DRONE_COOKIE_DOMAIN'] = 'app.example.com'

        cookie_header = backend_auth.session_cookie_header('abc123')
        cleared_header = backend_auth.clear_session_cookie_header()

        self.assertIn('Secure', cookie_header)
        self.assertIn('Domain=app.example.com', cookie_header)
        self.assertIn('Secure', cleared_header)

    def test_production_requires_configured_session_secret(self):
        os.environ['DRONE_ENV'] = 'production'
        os.environ.pop('DRONE_SESSION_SECRET', None)

        with self.assertRaisesRegex(RuntimeError, 'DRONE_SESSION_SECRET is required'):
            backend_auth.create_session_token({
                'email': 'pilot@example.com',
                'display_name': 'Pilot',
                'google_user_id': 'gid-1',
            })


if __name__ == '__main__':
    unittest.main()
