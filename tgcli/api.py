"""Authenticated HTTP interface to an already-authorized Telegram session."""
import hmac
import os
import re
from contextlib import asynccontextmanager

import click
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pyrogram.enums import ParseMode
from pyrogram.errors import BadRequest, FloodWait, Forbidden, RPCError
from pyrogram import raw

from .client import build_client, fmt_peer
from .cli import _resolve_chat


def create_app(client_factory=build_client, token=None):
    @asynccontextmanager
    async def lifespan(app):
        secret = token if token is not None else os.environ.get("TGCLI_API_TOKEN", "")
        if len(secret.encode("utf-8")) < 32:
            raise RuntimeError("TGCLI_API_TOKEN must be at least 32 bytes")
        app.state.token = secret.encode("utf-8")
        client = client_factory()
        client.sleep_threshold = 0
        # Several Pyrogram iterators override the client threshold with 60 seconds.
        invoke = client.invoke

        async def invoke_without_flood_sleep(query, retries=10, timeout=15, sleep_threshold=None):
            return await invoke(query, retries=retries, timeout=timeout, sleep_threshold=0)

        client.invoke = invoke_without_flood_sleep
        connected = False
        initialized = False
        try:
            authorized = await client.connect()
            connected = True
            if not authorized:
                raise RuntimeError("Telegram session is not authorized; run tgcli login first")
            await client.invoke(raw.functions.updates.GetState())
            client.me = await client.get_me()
            await client.initialize()
            initialized = True
            app.state.telegram = client
            yield
        finally:
            if initialized:
                try:
                    await client.terminate()
                finally:
                    if connected:
                        await client.disconnect()
            elif connected:
                await client.disconnect()

    app = FastAPI(title="tgcli API", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    async def authenticate(request: Request, authorization: str = Header(default="")):
        scheme, _, supplied = authorization.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.encode("utf-8"), request.app.state.token):
            raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})
        return request.app.state.telegram

    @app.exception_handler(FloodWait)
    async def flood_wait(request, exc):
        return JSONResponse({"detail": "Telegram rate limit", "retry_after": exc.value}, status_code=429, headers={"Retry-After": str(exc.value)})

    @app.exception_handler(RPCError)
    async def telegram_error(request, exc):
        status = 400 if isinstance(exc, BadRequest) else 403 if isinstance(exc, Forbidden) else 502
        return JSONResponse({"detail": "Telegram request failed"}, status_code=status)

    async def resolve(client, chat):
        try:
            return await _resolve_chat(client, chat)
        except click.ClickException:
            raise HTTPException(404, "Chat not found") from None

    def peer(user):
        return {"id": user.id, "name": fmt_peer(user), "username": getattr(user, "username", None)}

    def message(m):
        media = getattr(m, "media", None)
        date = getattr(m, "date", None)
        return {"id": m.id, "date": date.isoformat() if date else None,
                "text": getattr(m, "text", None) or getattr(m, "caption", None),
                "media": getattr(media, "value", None),
                "from": peer(m.from_user) if getattr(m, "from_user", None) else None}

    @app.get("/v1/me")
    async def me(client=Depends(authenticate)):
        user = await client.get_me()
        return {**peer(user), "is_bot": bool(user.is_bot)}

    @app.get("/v1/dialogs")
    async def dialogs(query: str = "", limit: int = Query(50, ge=1, le=200), client=Depends(authenticate)):
        result = []
        async for d in client.get_dialogs(limit=limit):
            if query.casefold() in fmt_peer(d.chat).casefold():
                result.append({**peer(d.chat), "type": str(d.chat.type).split(".")[-1], "unread": d.unread_messages_count})
        return result

    @app.get("/v1/contacts")
    async def contacts(query: str = "", client=Depends(authenticate)):
        return [peer(c) for c in await client.get_contacts() if query.casefold() in fmt_peer(c).casefold()]

    @app.get("/v1/chats/{chat}/messages")
    async def history(chat: str, query: str = "", limit: int = Query(30, ge=1, le=200), client=Depends(authenticate)):
        chat_id = await resolve(client, chat)
        messages = client.search_messages(chat_id, query, limit=limit) if query else client.get_chat_history(chat_id, limit=limit)
        return [message(m) async for m in messages]

    class SendMessage(BaseModel):
        text: str = Field(min_length=1, max_length=4096)

    @app.post("/v1/chats/{chat}/messages", status_code=201)
    async def send(chat: str, body: SendMessage, client=Depends(authenticate)):
        if re.fullmatch(r"-?[0-9]{1,20}", chat):
            chat_id = int(chat)
            if not -(2**63) <= chat_id < 2**63 or chat_id == 0:
                raise HTTPException(422, "Invalid numeric chat ID")
        elif re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,31}", chat):
            chat_id = chat
        else:
            raise HTTPException(422, "Use a numeric chat ID or @username")
        return message(await client.send_message(chat_id, body.text, parse_mode=ParseMode.DISABLED))

    return app


app = create_app()


def main():
    import uvicorn
    uvicorn.run("tgcli.api:app", host="127.0.0.1", port=8000, workers=1, access_log=False)
