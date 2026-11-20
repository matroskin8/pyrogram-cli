from datetime import datetime
import asyncio
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pyrogram.errors import FloodWait, BadRequest
from pyrogram.enums import ParseMode
from tgcli.api import create_app

TOKEN = "a" * 32
HEADERS = {"Authorization": "Bearer " + TOKEN}

async def gen(items):
    for item in items:
        yield item

class Fake:
    def __init__(self, authorized=True):
        self.connect = AsyncMock(return_value=authorized)
        self.initialize = AsyncMock()
        self.invoke = AsyncMock()
        self.terminate = AsyncMock()
        self.disconnect = AsyncMock()
        self.user = Obj(id=10, username="alice", is_bot=False)
        self.chat = Obj(id=42, username=None, title="Chat", type="ChatType.PRIVATE")
        self.msg = Obj(id=3, date=datetime(2024, 1, 1), text="hi", caption=None, media=None, from_user=self.user)
        self.get_me = AsyncMock(return_value=self.user)
        self.get_contacts = AsyncMock(return_value=[self.user])
        self.send_message = AsyncMock(return_value=self.msg)
        self.get_chat = AsyncMock(return_value=self.chat)
        self.get_dialogs = lambda limit: gen([Obj(chat=self.chat, unread_messages_count=2)])
        self.get_chat_history = lambda chat, limit: gen([self.msg])
        self.search_messages = lambda chat, query, limit: gen([self.msg])

@pytest.fixture
def setup():
    fake = Fake()
    with TestClient(create_app(lambda: fake, TOKEN)) as client:
        yield client, fake
    fake.terminate.assert_awaited_once()
    fake.disconnect.assert_awaited_once()

def test_auth_and_no_public_docs(setup):
    c, fake = setup
    assert c.get('/v1/me').status_code == 401
    assert c.get('/v1/me', headers={'Authorization': 'Bearer wrong'}).status_code == 401
    assert c.get('/docs').status_code == 404
    fake.get_me.assert_awaited_once()
    assert c.get('/v1/me', headers=HEADERS).json()['id'] == 10

def test_read_and_send(setup):
    c, fake = setup
    assert c.get('/v1/dialogs?query=chat', headers=HEADERS).json()[0]['id'] == 42
    assert c.get('/v1/contacts?query=alice', headers=HEADERS).json()[0]['id'] == 10
    assert c.get('/v1/chats/42/messages', headers=HEADERS).json()[0]['text'] == 'hi'
    assert c.get('/v1/chats/42/messages?query=hi', headers=HEADERS).status_code == 200
    assert c.get('/v1/chats/not-found/messages', headers=HEADERS).status_code == 404
    assert c.get('/v1/dialogs?limit=201', headers=HEADERS).status_code == 422
    assert c.post('/v1/chats/42/messages', headers=HEADERS, json={'text': ''}).status_code == 422
    r = c.post('/v1/chats/42/messages', headers=HEADERS, json={'text': 'hello'})
    assert r.status_code == 201
    fake.send_message.assert_awaited_once_with(42, 'hello', parse_mode=ParseMode.DISABLED)

def test_send_requires_unambiguous_target_and_literal_text(setup):
    c, fake = setup
    for chat in ('Chat', 'alice', 'me', '@12345', '@bad-name'):
        assert c.post(f'/v1/chats/{chat}/messages', headers=HEADERS, json={'text': 'x'}).status_code == 422
    fake.send_message.assert_not_awaited()
    assert c.post('/v1/chats/@alice/messages', headers=HEADERS, json={'text': '**literal** <b>x</b>'}).status_code == 201
    fake.send_message.assert_awaited_once_with('@alice', '**literal** <b>x</b>', parse_mode=ParseMode.DISABLED)

def test_startup_sets_me_and_disables_flood_sleep(setup):
    c, fake = setup
    assert fake.me is fake.user
    assert fake.sleep_threshold == 0

def test_flood_sleep_override_even_for_iterators():
    fake = Fake()
    original_invoke = fake.invoke
    with TestClient(create_app(lambda: fake, TOKEN)):
        asyncio.run(fake.invoke('query', retries=2, timeout=3, sleep_threshold=60))
    original_invoke.assert_any_await('query', retries=2, timeout=3, sleep_threshold=0)

def test_bad_request_is_not_a_gateway_failure(setup):
    c, fake = setup
    fake.get_me.side_effect = BadRequest('bad request')
    assert c.get('/v1/me', headers=HEADERS).status_code == 400

def test_get_me_failure_disconnects():
    fake = Fake()
    fake.get_me.side_effect = RuntimeError('failed')
    with pytest.raises(RuntimeError, match='failed'):
        with TestClient(create_app(lambda: fake, TOKEN)): pass
    fake.disconnect.assert_awaited_once()
    fake.initialize.assert_not_awaited()

def test_flood_wait(setup):
    c, fake = setup
    fake.get_me.side_effect = FloodWait(value=12)
    r = c.get('/v1/me', headers=HEADERS)
    assert r.status_code == 429
    assert r.headers['retry-after'] == '12'

def test_startup_rejects_missing_token_and_unauthorized_session():
    fake = Fake(False)
    with pytest.raises(RuntimeError, match='at least 32'):
        with TestClient(create_app(lambda: fake, 'short')): pass
    fake.connect.assert_not_awaited()
    with pytest.raises(RuntimeError, match='not authorized'):
        with TestClient(create_app(lambda: fake, TOKEN)): pass
    fake.disconnect.assert_awaited_once()
    fake.initialize.assert_not_awaited()

def test_initialize_failure_disconnects():
    fake = Fake()
    fake.initialize.side_effect = RuntimeError('failed')
    with pytest.raises(RuntimeError, match='failed'):
        with TestClient(create_app(lambda: fake, TOKEN)): pass
    fake.disconnect.assert_awaited_once()
