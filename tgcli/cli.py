"""tgcli commands — one tool for your own Telegram account.

Everything here acts as *you* (the account owner): it can read your dialogs,
search your history, send messages/files from your account and export chats.
Treat this tool like a key to your account — keep the session file secret.
"""

import asyncio
import json
import sys
from datetime import datetime

import click
from pyrogram.errors import FloodWait, SessionPasswordNeeded

from .client import build_client, fmt_peer


def _run(coro):
    try:
        asyncio.run(coro)
    except FloodWait as e:
        click.secho(f"FLOOD_WAIT: try again in {e.value}s", fg="yellow", err=True)
        sys.exit(2)
    except KeyboardInterrupt:
        click.secho("aborted", fg="yellow", err=True)
        sys.exit(130)


def _timestamp(ts) -> str:
    if isinstance(ts, datetime):
        return ts.strftime("%Y-%m-%d %H:%M")
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


@click.group()
@click.version_option()
def main():
    """tgcli — work with your Telegram account from the terminal.

    Requires TGCLI_API_ID / TGCLI_API_HASH (my.telegram.org) and a session.
    Create one with `tgcli login` or reuse an existing Pyrogram session file.
    """


@main.command()
def login():
    """Create a new session interactively (code goes to your Telegram/SMS)."""
    from pyrogram import Client

    from .client import load_config

    cfg = load_config()
    phone = click.prompt("Phone number (international format)")

    async def _login():
        app = Client(cfg["session"], api_id=cfg["api_id"], api_hash=cfg["api_hash"])
        await app.connect()
        sent = await app.send_code(phone)
        click.echo(
            f"Code sent (type={sent.type}, timeout={sent.timeout}s). "
            "Check your Telegram app."
        )
        code = click.prompt("Enter the code", type=str)
        try:
            await app.sign_in(phone, sent.phone_code_hash, code)
        except SessionPasswordNeeded:
            password = click.prompt("2FA password required", hide_input=True, type=str)
            await app.check_password(password)
        me = await app.get_me()
        click.echo(f"Logged in as {fmt_peer(me)} (id={me.id})")
        await app.stop()

    _run(_login())


@main.command()
def me():
    """Show who the session belongs to."""
    async def _me():
        app = build_client()
        await app.start()
        me = await app.get_me()
        click.echo(f"id:        {me.id}")
        click.echo(f"name:      {me.first_name} {me.last_name or ''}".rstrip())
        click.echo(f"username:  @{me.username}" if me.username else "username:  (none)")
        click.echo(f"is_bot:    {me.is_bot}")
        await app.stop()

    _run(_me())


@main.command()
@click.option("--query", "-q", help="Filter dialogs by name/username (case-insensitive).")
@click.option("--limit", "-n", default=50, show_default=True, help="Max dialogs.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON lines.")
def dialogs(query, limit, as_json):
    """List your recent dialogs."""
    async def _dialogs():
        app = build_client()
        await app.start()
        async for d in app.get_dialogs(limit=limit):
            chat = d.chat
            name = fmt_peer(chat)
            if query and query.lower() not in name.lower():
                continue
            if as_json:
                click.echo(
                    json.dumps(
                        {
                            "id": chat.id,
                            "type": str(chat.type).split(".")[-1],
                            "name": name,
                            "username": getattr(chat, "username", None),
                        },
                        ensure_ascii=False,
                    )
                )
            else:
                t = str(chat.type).split(".")[-1]
                click.echo(f"{chat.id:<15} {t:<8} {name}")
        await app.stop()

    _run(_dialogs())


@main.command()
@click.argument("query")
@click.option("--limit", "-n", default=20, show_default=True, help="Max results.")
def search(query, limit):
    """Search your history for messages matching QUERY.

    Iterates recent dialogs and searches messages with Pyrogram's
    search_messages — i.e. it searches Telegram, not a local index.
    """
    async def _search():
        app = build_client()
        await app.start()
        found = 0
        async for d in app.get_dialogs(limit=200):
            if found >= limit:
                break
            chat = d.chat
            try:
                async for m in app.search_messages(chat.id, query, limit=limit - found):
                    found += 1
                    text = (m.text or m.caption or f"[{m.media.value}]")[:120].replace("\n", " ")
                    click.echo(f"[{_timestamp(m.date)}] {fmt_peer(chat)} (id={chat.id})")
                    click.echo(f"  {text}")
            except FloodWait:
                raise
            except Exception:
                continue
        click.echo(f"\n{found} result(s)", err=True)
        await app.stop()

    _run(_search())


async def _resolve_chat(app, target: str) -> int:
    """Return a chat id for username (with/without @), numeric id, or dialog title."""
    target = target.strip()
    if target.startswith("@"):
        chat = await app.get_chat(target[1:])
        return chat.id
    if target.lstrip("-").isdigit():
        return int(target)
    # Fall back to a title match among recent dialogs
    async for d in app.get_dialogs(limit=200):
        if fmt_peer(d.chat).lower() == target.lower():
            return d.chat.id
    raise click.ClickException(f"Chat not found: {target!r}")


@main.command()
@click.argument("chat")
@click.option("--limit", "-n", default=30, show_default=True, help="Max messages.")
@click.option("--query", "-q", help="Only messages matching this query.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON lines.")
def history(chat, limit, query, as_json):
    """Show recent messages from CHAT (username, @username, id, or title)."""
    async def _history():
        app = build_client()
        await app.start()
        chat_id = await _resolve_chat(app, chat)
        if query:
            msgs = app.search_messages(chat_id, query, limit=limit)
        else:
            msgs = app.get_chat_history(chat_id, limit=limit)
        async for m in msgs:
            text = m.text or m.caption or f"[{m.media.value if m.media else '?'}]"
            if as_json:
                click.echo(
                    json.dumps(
                        {
                            "id": m.id,
                            "date": _timestamp(m.date),
                            "from_me": bool(m.from_user and m.from_user.is_self),
                            "text": text,
                        },
                        ensure_ascii=False,
                    )
                )
            else:
                who = "me" if (m.from_user and m.from_user.is_self) else fmt_peer(m.from_user or m.chat)
                click.echo(f"[{_timestamp(m.date)}] {who}: {text[:200]}")
        await app.stop()

    _run(_history())


@main.command()
@click.argument("chat")
@click.argument("text")
def send(chat, text):
    """Send TEXT to CHAT from your account."""
    async def _send():
        app = build_client()
        await app.start()
        chat_id = await _resolve_chat(app, chat)
        m = await app.send_message(chat_id, text)
        click.echo(f"sent to {fmt_peer(m.chat)} (message_id={m.id})")
        await app.stop()

    _run(_send())


@main.command(name="send-file")
@click.argument("chat")
@click.argument("path", type=click.Path(exists=True))
@click.option("--caption", "-c", help="Optional caption.")
def send_file(chat, path, caption):
    """Send a FILE to CHAT from your account."""
    async def _send_file():
        app = build_client()
        await app.start()
        chat_id = await _resolve_chat(app, chat)
        m = await app.send_document(chat_id, path, caption=caption)
        click.echo(f"file sent to {fmt_peer(m.chat)} (message_id={m.id})")
        await app.stop()

    _run(_send_file())


@main.command()
@click.option("--query", "-q", help="Filter contacts by name.")
def contacts(query):
    """List your Telegram contacts."""
    async def _contacts():
        app = build_client()
        await app.start()
        for c in await app.get_contacts():
            name = fmt_peer(c)
            if query and query.lower() not in name.lower():
                continue
            click.echo(f"{c.id:<12} @{c.username or '-'}  {name}")
        await app.stop()

    _run(_contacts())


@main.command(name="find-bot")
@click.argument("query")
def find_bot(query):
    """Find bots whose name/username contains QUERY."""
    async def _find_bot():
        app = build_client()
        await app.start()
        async for d in app.get_dialogs(limit=200):
            chat = d.chat
            if getattr(chat, "is_bot", False):
                name = fmt_peer(chat)
                if query.lower() in name.lower():
                    click.echo(f"{chat.id:<12} {name}")
        await app.stop()

    _run(_find_bot())


@main.command()
@click.argument("chat")
@click.argument("out", type=click.Path())
@click.option("--limit", "-n", default=1000, show_default=True, help="Max messages.")
def export(chat, out, limit):
    """Export CHAT history to a JSONL file (one message per line)."""
    async def _export():
        app = build_client()
        await app.start()
        chat_id = await _resolve_chat(app, chat)
        written = 0
        with open(out, "w", encoding="utf-8") as f:
            async for m in app.get_chat_history(chat_id, limit=limit):
                rec = {
                    "id": m.id,
                    "date": _timestamp(m.date),
                    "from_me": bool(m.from_user and m.from_user.is_self),
                    "text": m.text or m.caption or None,
                    "media": m.media.value if m.media else None,
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1
        click.echo(f"exported {written} message(s) to {out}")
        await app.stop()

    _run(_export())


if __name__ == "__main__":
    main()
