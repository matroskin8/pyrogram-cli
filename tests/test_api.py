from datetime import datetime
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pyrogram.errors import FloodWait
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
    fake.get_me.assert_not_awaited()
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
    fake.send_message.assert_awaited_once_with(42, 'hello')

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
