"""Interactive QR authorization using Pyrogram's raw MTProto methods."""

import asyncio
import base64
import sys
import time

import click
import qrcode
from pyrogram import raw
from pyrogram.errors import SessionPasswordNeeded
from pyrogram.session import Auth, Session


async def _migrate(app, dc_id):
    # Match Pyrogram's send_code migration: the auth key belongs to a DC.
    await app.session.stop()
    await app.storage.dc_id(dc_id)
    test_mode = await app.storage.test_mode()
    await app.storage.auth_key(await Auth(app, dc_id, test_mode).create())
    app.session = Session(app, dc_id, await app.storage.auth_key(), test_mode)
    await app.session.start()


def _display(token):
    url = "tg://login?token=" + base64.urlsafe_b64encode(token).decode().rstrip("=")
    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    click.echo("Scan with Telegram: Settings → Devices → Link Desktop Device")
    for row in qr.get_matrix():
        click.echo("".join("  " if cell else "██" for cell in row))


async def login_qr(app):
    """Connect, authorize and commit session metadata, always disconnecting."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise click.ClickException("QR login requires a private interactive terminal (stdin and stdout must be TTYs)")
    authorized = await app.connect()
    try:
        if authorized:
            raise click.ClickException("Session is already authorized; use a different session to log in")
        last_token = None
        while True:
            try:
                result = await app.invoke(raw.functions.auth.ExportLoginToken(
                    api_id=app.api_id, api_hash=app.api_hash, except_ids=[]))
                while isinstance(result, raw.types.auth.LoginTokenMigrateTo):
                    await _migrate(app, result.dc_id)
                    result = await app.invoke(raw.functions.auth.ImportLoginToken(token=result.token))
                if isinstance(result, raw.types.auth.LoginTokenSuccess):
                    user = getattr(result.authorization, "user", None)
                    if user is None:
                        raise click.ClickException("Unexpected authorization response")
                    await app.storage.user_id(user.id)
                    await app.storage.is_bot(False)
                    break
                if not isinstance(result, raw.types.auth.LoginToken):
                    raise click.ClickException("Unexpected QR login response")
                if result.token != last_token:
                    _display(result.token)
                    last_token = result.token
                # Poll for acceptance as well as expiry, since connect() alone does not
                # start Pyrogram's update dispatcher. Never busy-loop on expired tokens.
                await asyncio.sleep(max(1, min(3, result.expires - time.time())))
            except SessionPasswordNeeded:
                password = click.prompt("2FA password required", hide_input=True, type=str)
                await app.check_password(password)  # persists user_id/is_bot in storage
                break
        await app.storage.save()  # disconnect() closes SQLite without committing
        click.echo("Login successful. Session saved.")
    finally:
        await app.disconnect()
