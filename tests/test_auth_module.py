from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from modules.auth.module import build_auth_module


class AuthModuleTest(unittest.TestCase):
    def test_register_google_session_persists_login_and_sets_cookie(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            upserts = []
            module = build_auth_module(
                logged_accounts_file=Path(tmp_dir) / 'logged_accounts.json',
                upsert_user=lambda user, app: upserts.append((user, app)) or user,
                create_token=lambda user: 'token-' + user['email'],
                cookie_header=lambda token: 'cookie=' + token,
                clear_cookie_header=lambda: 'cookie=;',
                session_user_from_headers=lambda headers: None,
                token_payload_decoder=lambda token: {
                    'email': 'pilot@example.com',
                    'name': 'Pilot Example',
                    'sub': 'google-123',
                },
                app_user_upsert_errors=(RuntimeError,),
            )

            result = module.register_google_session({'id_token': 'fake', 'app': 'web'}, '127.0.0.1')

            self.assertEqual(result['user']['email'], 'pilot@example.com')
            self.assertEqual(result['set_cookie'], 'cookie=token-pilot@example.com')
            self.assertEqual(len(module.list_logged_accounts()), 1)
            self.assertEqual(upserts[0][0]['display_name'], 'Pilot Example')


if __name__ == '__main__':
    unittest.main()
