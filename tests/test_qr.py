import asyncio
import io
import tempfile
from pathlib import Path
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import click
from click.testing import CliRunner
from pyrogram import raw
from pyrogram.errors import SessionPasswordNeeded

from tgcli.cli import main
from tgcli.qr import login_qr, _migrate, _display


def app_with(*responses, connected=False):
    storage = SimpleNamespace(user_id=AsyncMock(), is_bot=AsyncMock(), save=AsyncMock(),
                              dc_id=AsyncMock(), auth_key=AsyncMock(), test_mode=AsyncMock(return_value=False))
    return SimpleNamespace(api_id=123, api_hash='hash', storage=storage,
                           connect=AsyncMock(return_value=connected), disconnect=AsyncMock(),
                           invoke=AsyncMock(side_effect=list(responses)), check_password=AsyncMock(),
                           session=SimpleNamespace(stop=AsyncMock()))


def success():
    return raw.types.auth.LoginTokenSuccess(authorization=SimpleNamespace(user=SimpleNamespace(id=42)))


class QRTests(unittest.TestCase):
    def run_login(self, app):
        with patch('sys.stdin.isatty', return_value=True), patch('sys.stdout.isatty', return_value=True):
            return asyncio.run(login_qr(app))

    def test_success_commits_and_disconnects(self):
        app = app_with(success())
        self.run_login(app)
        app.storage.user_id.assert_awaited_once_with(42)
        app.storage.is_bot.assert_awaited_once_with(False)
        app.storage.save.assert_awaited_once()
        app.disconnect.assert_awaited_once()
        query = app.invoke.await_args.args[0]
        self.assertIsInstance(query, raw.functions.auth.ExportLoginToken)
        self.assertEqual((query.api_id, query.api_hash, query.except_ids), (123, 'hash', []))

    def test_refresh_and_poll(self):
        token = raw.types.auth.LoginToken(expires=int(time.time()) + 20, token=b'a')
        token2 = raw.types.auth.LoginToken(expires=0, token=b'b')
        app = app_with(token, token, token2, success())
        with patch('tgcli.qr._display') as display, patch('tgcli.qr.asyncio.sleep', new_callable=AsyncMock) as sleep:
            self.run_login(app)
        self.assertEqual([x.args[0] for x in display.call_args_list], [b'a', b'b'])
        self.assertEqual(sleep.await_count, 3)
        self.assertEqual(sleep.await_args_list[-1].args[0], 1)

    def test_migration_uses_new_dc_then_import(self):
        app = app_with(raw.types.auth.LoginTokenMigrateTo(dc_id=4, token=b'migrate'), success())
        session = SimpleNamespace(start=AsyncMock())
        auth = SimpleNamespace(create=AsyncMock(return_value=b'newkey'))
        with patch('tgcli.qr.Auth', return_value=auth) as auth_type, patch('tgcli.qr.Session', return_value=session) as session_type:
            self.run_login(app)
        app.storage.dc_id.assert_awaited_once_with(4)
        app.storage.auth_key.assert_any_await(b'newkey')
        auth_type.assert_called_once_with(app, 4, False)
        session_type.assert_called_once()
        session.start.assert_awaited_once()
        request = app.invoke.await_args_list[1].args[0]
        self.assertIsInstance(request, raw.functions.auth.ImportLoginToken)
        self.assertEqual(request.token, b'migrate')
        app.storage.save.assert_awaited_once()

    def test_password_path_and_hidden_prompt(self):
        app = app_with(SessionPasswordNeeded())
        with patch('tgcli.qr.click.prompt', return_value='secret') as prompt:
            self.run_login(app)
        self.assertTrue(prompt.call_args.kwargs['hide_input'])
        app.check_password.assert_awaited_once_with('secret')
        app.storage.save.assert_awaited_once()
        app.disconnect.assert_awaited_once()

    def test_failures_disconnect_and_do_not_save(self):
        for response in (ValueError('broken'), object()):
            app = app_with(response)
            with self.assertRaises((ValueError, click.ClickException)):
                self.run_login(app)
            app.storage.save.assert_not_awaited()
            app.disconnect.assert_awaited_once()

    def test_existing_session_not_overwritten(self):
        app = app_with(connected=True)
        with self.assertRaises(click.ClickException):
            self.run_login(app)
        app.invoke.assert_not_awaited()
        app.storage.save.assert_not_awaited()
        app.disconnect.assert_awaited_once()

    def test_refuses_non_tty_without_connecting(self):
        app = app_with()
        with patch('sys.stdin.isatty', return_value=False):
            with self.assertRaises(click.ClickException):
                asyncio.run(login_qr(app))
        app.connect.assert_not_awaited()

    def test_qr_does_not_print_token_or_url(self):
        stream = io.StringIO()
        with patch('sys.stdout', stream):
            _display(b'test-token')
        self.assertNotIn('tg://', stream.getvalue())
        self.assertNotIn('dGVzdC10b2tlbg', stream.getvalue())
        self.assertIn('██', stream.getvalue())

    def test_real_file_storage_persists_after_disconnect(self):
        from pyrogram.storage import FileStorage

        async def exercise(path):
            storage = FileStorage('test', Path(path))
            await storage.open()
            await storage.dc_id(2)
            await storage.api_id(123)
            await storage.test_mode(False)
            await storage.auth_key(b'x' * 256)
            app = app_with(success())
            app.storage = storage
            app.disconnect = AsyncMock(side_effect=storage.close)
            await login_qr(app)
            reopened = FileStorage('test', Path(path))
            await reopened.open()
            self.assertEqual(await reopened.user_id(), 42)
            self.assertFalse(await reopened.is_bot())
            self.assertEqual(await reopened.dc_id(), 2)
            self.assertEqual(await reopened.auth_key(), b'x' * 256)
            await reopened.close()

        with tempfile.TemporaryDirectory() as path, patch('sys.stdin.isatty', return_value=True), patch('sys.stdout.isatty', return_value=True):
            asyncio.run(exercise(path))

    def test_cli_routes_to_interactive_client(self):
        app = app_with(success())
        with patch('tgcli.cli.build_client', return_value=app) as build, patch('tgcli.qr.login_qr', new_callable=AsyncMock) as login:
            result = CliRunner().invoke(main, ['login-qr'])
        self.assertEqual(result.exit_code, 0, str(result.exception))
        build.assert_called_once_with(interactive_login=True)
        login.assert_awaited_once_with(app)
