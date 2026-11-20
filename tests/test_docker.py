import asyncio
import os
import unittest
from unittest.mock import patch

from click.testing import CliRunner
from tgcli import client
from tgcli.cli import main


class DockerBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()
        asyncio.set_event_loop(None)

    def test_workdir_and_noninteractive_client(self):
        with patch.dict(os.environ, {"TGCLI_API_ID": "123", "TGCLI_API_HASH": "dummy", "TGCLI_WORKDIR": "/data", "TGCLI_NONINTERACTIVE": "1"}, clear=True), patch.object(client, "_load_env"):
            app = client.build_client()
            self.assertIsInstance(app, client.NonInteractiveClient)
            self.assertEqual(str(app.workdir), "/data")
            with self.assertRaisesRegex(client.ConfigError, "not authorized"):
                self.loop.run_until_complete(app.authorize())
            self.assertNotIsInstance(client.build_client(interactive_login=True), client.NonInteractiveClient)

    def test_default_stays_interactive(self):
        with patch.dict(os.environ, {"TGCLI_API_ID": "123", "TGCLI_API_HASH": "dummy"}, clear=True), patch.object(client, "_load_env"):
            self.assertNotIsInstance(client.build_client(), client.NonInteractiveClient)

    def test_config_error_is_friendly(self):
        with patch("tgcli.cli.build_client", side_effect=client.ConfigError("no credentials")):
            result = CliRunner().invoke(main, ["me"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Error: no credentials", result.output)
        self.assertNotIn("Traceback", result.output)

    def test_help_without_credentials(self):
        result = CliRunner().invoke(main, ["--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("login", result.output)


if __name__ == "__main__":
    unittest.main()
