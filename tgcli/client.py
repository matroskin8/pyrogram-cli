"""Session bootstrap: load credentials from env / .env and build a Pyrogram Client."""

import os
from pathlib import Path

from dotenv import load_dotenv
from pyrogram import Client


class ConfigError(RuntimeError):
    pass


class NonInteractiveClient(Client):
    async def authorize(self):
        raise ConfigError("Session is not authorized; run 'tgcli login' interactively first")


def _load_env() -> None:
    """Load .env from cwd, then from home dir (later wins only if not set)."""
    load_dotenv(Path.cwd() / ".env")
    load_dotenv(Path.home() / ".tgcli.env")


def load_config() -> dict:
    _load_env()

    api_id = os.getenv("TGCLI_API_ID") or os.getenv("API_ID")
    api_hash = os.getenv("TGCLI_API_HASH") or os.getenv("API_HASH")
    session = os.getenv("TGCLI_SESSION") or os.getenv("SESSION") or "tgcli_session"

    if not api_id or not api_hash:
        raise ConfigError(
            "Missing credentials. Set TGCLI_API_ID and TGCLI_API_HASH "
            "(get them at https://my.telegram.org/apps) or put them in .env"
        )

    try:
        api_id = int(api_id)
    except (TypeError, ValueError):
        raise ConfigError("TGCLI_API_ID must be an integer")

    return {"api_id": api_id, "api_hash": api_hash, "session": str(session)}


def build_client(*, interactive_login: bool = False) -> Client:
    cfg = load_config()
    noninteractive = os.getenv("TGCLI_NONINTERACTIVE", "").lower() in ("1", "true", "yes")
    client_type = NonInteractiveClient if noninteractive and not interactive_login else Client
    return client_type(
        cfg["session"],
        api_id=cfg["api_id"],
        api_hash=cfg["api_hash"],
        workdir=os.getenv("TGCLI_WORKDIR") or str(Path.cwd()),
        in_memory=False,
    )


def fmt_peer(peer) -> str:
    """Human-readable peer name: username or 'First Last' or title."""
    if peer is None:
        return "?"
    username = getattr(peer, "username", None)
    if username:
        return f"@{username}"
    first = getattr(peer, "first_name", None) or ""
    last = getattr(peer, "last_name", None) or ""
    name = f"{first} {last}".strip()
    if name:
        return name
    return getattr(peer, "title", None) or str(getattr(peer, "id", "?"))
