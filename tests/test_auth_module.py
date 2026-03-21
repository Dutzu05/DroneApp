from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from modules.auth.module import build_auth_module


class AuthModuleTest(unittest.TestCase):
    def _workspace_temp_dir(self) -> Path:
        path = Path.cwd() / '.tmp' / f'auth-module-test-{uuid.uuid4().hex}'
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_register_google_session_persists_login_and_sets_cookie(self):
        tmp_dir = self._workspace_temp_dir()
        upserts = []
        module = build_auth_module(
            logged_accounts_file=tmp_dir / 'logged_accounts.json',
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

    def test_register_google_session_uses_request_fallbacks_and_ignores_configured_upsert_error(self):
        tmp_dir = self._workspace_temp_dir()
        upserts = []

        def _raise_upsert_error(user, app):
            upserts.append((user, app))
            raise RuntimeError('db unavailable')

        module = build_auth_module(
            logged_accounts_file=tmp_dir / 'logged_accounts.json',
            upsert_user=_raise_upsert_error,
            create_token=lambda user: 'token-' + user['google_user_id'],
            cookie_header=lambda token: 'cookie=' + token,
            clear_cookie_header=lambda: 'cookie=;',
            session_user_from_headers=lambda headers: None,
            token_payload_decoder=lambda token: {},
            app_user_upsert_errors=(RuntimeError,),
        )

        result = module.register_google_session(
            {
                'email': ' Pilot@Example.com ',
                'display_name': ' Pilot Example ',
                'google_user_id': 'google-456',
            },
            '127.0.0.2',
        )

        self.assertEqual(result['user']['email'], 'pilot@example.com')
        self.assertEqual(result['user']['display_name'], 'Pilot Example')
        self.assertEqual(result['user']['google_user_id'], 'google-456')
        self.assertEqual(result['set_cookie'], 'cookie=token-google-456')
        self.assertEqual(upserts[0][1], 'drone_frontend')
        self.assertEqual(module.list_logged_accounts()[0]['last_ip'], '127.0.0.2')

    def test_register_google_session_requires_email(self):
        tmp_dir = self._workspace_temp_dir()
        module = build_auth_module(
            logged_accounts_file=tmp_dir / 'logged_accounts.json',
            upsert_user=lambda user, app: user,
            create_token=lambda user: 'token',
            cookie_header=lambda token: 'cookie=' + token,
            clear_cookie_header=lambda: 'cookie=;',
            session_user_from_headers=lambda headers: None,
            token_payload_decoder=lambda token: {},
            app_user_upsert_errors=(RuntimeError,),
        )

        with self.assertRaisesRegex(ValueError, 'email is required'):
            module.register_google_session({}, '127.0.0.3')

    def test_current_user_normalizes_session_values_and_clear_cookie_header(self):
        tmp_dir = self._workspace_temp_dir()
        module = build_auth_module(
            logged_accounts_file=tmp_dir / 'logged_accounts.json',
            upsert_user=lambda user, app: user,
            create_token=lambda user: 'token',
            cookie_header=lambda token: 'cookie=' + token,
            clear_cookie_header=lambda: 'cookie=;',
            session_user_from_headers=lambda headers: {
                'email': ' Pilot@Example.com ',
                'display_name': None,
                'google_user_id': None,
            },
            token_payload_decoder=lambda token: {},
            app_user_upsert_errors=(RuntimeError,),
        )

        current_user = module.current_user({'cookie': 'session=abc'})

        self.assertEqual(
            current_user,
            {
                'email': 'pilot@example.com',
                'display_name': '',
                'google_user_id': '',
            },
        )
        self.assertEqual(module.clear_cookie_header(), 'cookie=;')

    def test_current_user_returns_none_when_session_is_missing(self):
        tmp_dir = self._workspace_temp_dir()
        module = build_auth_module(
            logged_accounts_file=tmp_dir / 'logged_accounts.json',
            upsert_user=lambda user, app: user,
            create_token=lambda user: 'token',
            cookie_header=lambda token: 'cookie=' + token,
            clear_cookie_header=lambda: 'cookie=;',
            session_user_from_headers=lambda headers: None,
            token_payload_decoder=lambda token: {},
            app_user_upsert_errors=(RuntimeError,),
        )

        self.assertIsNone(module.current_user({}))


if __name__ == '__main__':
    unittest.main()
