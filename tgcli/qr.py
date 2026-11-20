"""Interactive QR authorization using Pyrogram's raw MTProto methods."""

import asyncio
import base64
import os
import signal
import sys
import time

import click
import qrcode
from pyrogram import raw
from pyrogram.errors import SessionPasswordNeeded
from pyrogram.session import Auth, Session

LOGIN_TIMEOUT = 300


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
    click.echo("Warning: the QR code is a login credential and may remain in terminal scrollback.")
    click.echo("Scan with Telegram: Settings → Devices → Link Desktop Device")
    for row in qr.get_matrix():
        click.echo("".join("  " if cell else "██" for cell in row))


async def _wait_for_login_update(app, expires):
    # Session.handle_packet calls Client.handle_updates even without initialize();
    # it places UpdateShort/Updates contents in this queue without a dispatcher.
    deadline = asyncio.get_running_loop().time() + max(0, expires - time.time())
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return
        try:
            update, _, _ = await asyncio.wait_for(app.dispatcher.updates_queue.get(), remaining)
        except asyncio.TimeoutError:
            return
        if isinstance(update, raw.types.UpdateLoginToken):
            return


def _password(deadline):
    # click.prompt is synchronous, so an asyncio timeout alone cannot interrupt it.
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    def expired(signum, frame):
        raise TimeoutError
    previous = signal.signal(signal.SIGALRM, expired)
    old_timer = signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        return click.prompt("2FA password required", hide_input=True, type=str)
    finally:
        signal.setitimer(signal.ITIMER_REAL, *old_timer)
        signal.signal(signal.SIGALRM, previous)


async def _authorize(app, deadline):
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
                return
            if not isinstance(result, raw.types.auth.LoginToken):
                raise click.ClickException("Unexpected QR login response")
            if result.token != last_token:
                _display(result.token)
                last_token = result.token
            if result.expires <= time.time():
                # Prevent a server repeatedly returning expired tokens from spinning.
                await asyncio.sleep(1)
            else:
                await _wait_for_login_update(app, result.expires)
        except SessionPasswordNeeded:
            await app.check_password(_password(deadline))
            return


async def login_qr(app):
    """Connect, authorize and commit session metadata, always disconnecting."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise click.ClickException("QR login requires a private interactive terminal (stdin and stdout must be TTYs)")
    if getattr(app, "is_connected", False):
        raise click.ClickException("Client is already connected")
    connected = False
    # The process-wide mask also protects SQLite journal files created at connect.
    old_umask = os.umask(0o077)
    deadline = time.monotonic() + LOGIN_TIMEOUT
    try:
        async def run():
            nonlocal connected
            authorized = await app.connect()
            connected = True
            if authorized:
                raise click.ClickException("Session is already authorized; use a different session to log in")
            await _authorize(app, deadline)
            await app.storage.save()
            click.echo("Login successful. Session saved.")
        try:
            await asyncio.wait_for(run(), LOGIN_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise click.ClickException("QR login timed out") from exc
    finally:
        try:
            if connected or getattr(app, "is_connected", False):
                await app.disconnect()
            else:
                # connect() can fail after opening storage or starting a session,
                # before Pyrogram sets is_connected.
                session = getattr(app, "session", None)
                try:
                    if session is not None and getattr(session, "connection", None) is not None:
                        await session.stop()
                finally:
                    if getattr(app.storage, "conn", None) is not None:
                        await app.storage.close()
        finally:
            os.umask(old_umask)
