import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner
from pyrogram.errors import SessionPasswordNeeded

from tgcli.cli import main


class LoginTests(unittest.TestCase):
    def make_app(self):
        app = SimpleNamespace(
            connect=AsyncMock(), disconnect=AsyncMock(),
            send_code=AsyncMock(return_value=SimpleNamespace(type="app", timeout=30, phone_code_hash="hash")),
            sign_in=AsyncMock(), check_password=AsyncMock(),
            get_me=AsyncMock(return_value=SimpleNamespace(id=42, username="user")),
        )
        return app

    def test_login_hides_code_and_disconnects(self):
        app = self.make_app()
        with patch("tgcli.cli.build_client", return_value=app) as build:
            result = CliRunner().invoke(main, ["login"], input="+123456789\n987654\n")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("987654", result.output)
        build.assert_called_once_with(interactive_login=True)
        app.sign_in.assert_awaited_once_with("+123456789", "hash", "987654")
        app.disconnect.assert_awaited_once()

    def test_login_two_factor_hides_both_secrets(self):
        app = self.make_app()
        app.sign_in.side_effect = SessionPasswordNeeded()
        with patch("tgcli.cli.build_client", return_value=app):
            result = CliRunner().invoke(main, ["login"], input="+123456789\n987654\nsecret-password\n")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("987654", result.output)
        self.assertNotIn("secret-password", result.output)
        app.check_password.assert_awaited_once_with("secret-password")
        app.disconnect.assert_awaited_once()

    def test_login_disconnects_after_sign_in_error(self):
        app = self.make_app()
        app.sign_in.side_effect = ValueError("bad code")
        with patch("tgcli.cli.build_client", return_value=app):
            result = CliRunner().invoke(main, ["login"], input="+123456789\n987654\n")
        self.assertIsInstance(result.exception, ValueError)
        app.disconnect.assert_awaited_once()
