import asyncio
import io
import os
import tempfile
from pathlib import Path
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import click
from click.testing import CliRunner
from pyrogram import raw
from pyrogram.errors import PasswordHashInvalid, SessionPasswordNeeded

from tgcli.cli import main
from tgcli.qr import login_qr, _migrate, _display


def app_with(*responses, connected=False):
    storage = SimpleNamespace(user_id=AsyncMock(), is_bot=AsyncMock(), save=AsyncMock(),
                              dc_id=AsyncMock(), auth_key=AsyncMock(), test_mode=AsyncMock(return_value=False))
    return SimpleNamespace(api_id=123, api_hash='hash', storage=storage,
                           connect=AsyncMock(return_value=connected), disconnect=AsyncMock(),
                           invoke=AsyncMock(side_effect=list(responses)), check_password=AsyncMock(),
                           session=None, dispatcher=SimpleNamespace(updates_queue=asyncio.Queue()))


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

    def test_update_triggers_export_without_polling(self):
        token = raw.types.auth.LoginToken(expires=int(time.time()) + 20, token=b'a')
        app = app_with(token, success())
        async def exercise():
            with patch('tgcli.qr._display'):
                task = asyncio.create_task(login_qr(app))
                await asyncio.sleep(0.05)
                self.assertEqual(app.invoke.await_count, 1)
                app.dispatcher.updates_queue.put_nowait((raw.types.UpdateLoginToken(), {}, {}))
                await task
        with patch('sys.stdin.isatty', return_value=True), patch('sys.stdout.isatty', return_value=True):
            asyncio.run(exercise())
        self.assertEqual(app.invoke.await_count, 2)

    def test_pyrogram_update_short_reaches_queue(self):
        from pyrogram import Client
        async def exercise():
            client = Client('test', api_id=123, api_hash='hash', in_memory=True)
            update = raw.types.UpdateLoginToken()
            await client.handle_updates(raw.types.UpdateShort(update=update, date=0))
            self.assertIs((await client.dispatcher.updates_queue.get())[0], update)
        asyncio.run(exercise())

    def test_session_packet_routes_to_real_client_queue(self):
        from pyrogram import Client
        from pyrogram.session import Session
        async def exercise():
            client = Client('test', api_id=123, api_hash='hash', in_memory=True)
            session = Session(client, 2, b'x' * 256, False)
            update = raw.types.UpdateLoginToken()
            body = raw.types.UpdateShort(update=update, date=0)
            from pyrogram.session.internals import MsgId
            msg = SimpleNamespace(body=body, seq_no=0, msg_id=MsgId())
            with patch('pyrogram.session.session.mtproto.unpack', return_value=msg):
                await session.handle_packet(b'packet')
                await asyncio.sleep(0)
            self.assertIs((await asyncio.wait_for(client.dispatcher.updates_queue.get(), 1))[0], update)
        asyncio.run(exercise())

    def test_expiry_refresh_and_overall_timeout(self):
        token = raw.types.auth.LoginToken(expires=int(time.time()) + 1, token=b'a')
        app = app_with(token, success())
        with patch('tgcli.qr._display'):
            self.run_login(app)
        self.assertEqual(app.invoke.await_count, 2)
        app = app_with(raw.types.auth.LoginToken(expires=int(time.time()) + 60, token=b'b'))
        with patch('tgcli.qr._display'), patch('tgcli.qr.LOGIN_TIMEOUT', 0.05):
            with self.assertRaisesRegex(click.ClickException, 'timed out'):
                self.run_login(app)
        app.disconnect.assert_awaited_once()

    def test_umask_restored_and_connect_failure_cleanup(self):
        app = app_with()
        app.storage.conn = object()
        app.storage.close = AsyncMock()
        app.session = SimpleNamespace(connection=object(), stop=AsyncMock())
        async def fail():
            mask = os.umask(0o077)
            os.umask(mask)
            self.assertEqual(mask & 0o077, 0o077)
            raise RuntimeError('connect failed')
        app.connect.side_effect = fail
        original = os.umask(0o022)
        try:
            with self.assertRaisesRegex(RuntimeError, 'connect failed'):
                self.run_login(app)
            current = os.umask(0o022)
            self.assertEqual(current, 0o022)
        finally:
            os.umask(original)
        app.session.stop.assert_awaited_once()
        app.storage.close.assert_awaited_once()

    def test_migration_uses_new_dc_then_import(self):
        app = app_with(raw.types.auth.LoginTokenMigrateTo(dc_id=4, token=b'migrate'), success())
        app.session = SimpleNamespace(stop=AsyncMock())
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

    def test_wrong_password_then_success(self):
        app = app_with(SessionPasswordNeeded())
        app.check_password.side_effect = [PasswordHashInvalid(), None]
        with patch('tgcli.qr.click.prompt', side_effect=['wrong', 'correct']):
            self.run_login(app)
        self.assertEqual(app.check_password.await_count, 2)
        app.storage.save.assert_awaited_once()
        app.disconnect.assert_awaited_once()

    def test_three_wrong_passwords_stop_cleanly(self):
        app = app_with(SessionPasswordNeeded())
        app.check_password.side_effect = PasswordHashInvalid()
        with patch('tgcli.qr.click.prompt', return_value='wrong'):
            with self.assertRaisesRegex(click.ClickException, 'maximum attempts'):
                self.run_login(app)
        self.assertEqual(app.check_password.await_count, 3)
        app.storage.save.assert_not_awaited()
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
