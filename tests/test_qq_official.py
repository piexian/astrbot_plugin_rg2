import datetime

import pytest

from astrbot_plugin_rg2.core.qq_official import (
    QQInteractionShim,
    ban_member,
    build_game_keyboard,
    is_qq_official_event,
    parse_interaction,
    send_card,
    send_text,
)


class FakeHTTP:
    def __init__(self):
        self.calls = []

    async def request(self, route, json=None):
        self.calls.append((route, json))
        return {}


class FakeAPI:
    def __init__(self):
        self._http = FakeHTTP()
        self.posts = []

    async def post_group_message(self, **kwargs):
        self.posts.append(kwargs)
        return {}


class FakeEvent:
    def __init__(self, platform_name):
        self._platform_name = platform_name

    def get_platform_name(self):
        return self._platform_name


def test_is_qq_official_event():
    assert is_qq_official_event(FakeEvent("qq_official"))
    assert is_qq_official_event(FakeEvent("qq_official_webhook"))
    assert not is_qq_official_event(FakeEvent("aiocqhttp"))
    assert not is_qq_official_event(FakeEvent("unknown_platform"))


@pytest.mark.asyncio
async def test_ban_member_payload():
    api = FakeAPI()
    await ban_member(api, "GID", "MID", 120)
    route, payload = api._http.calls[0]
    assert route.method == "POST"
    assert "/v2/groups/GID/restrict_chat_setting" in route.url
    member = payload["members"][0]
    assert member["op"] == "add"
    assert member["member_openid"] == "MID"
    expire = datetime.datetime.fromisoformat(member["mute_expire_at"])
    delta = expire - datetime.datetime.now(datetime.timezone.utc)
    assert 110 < delta.total_seconds() <= 120


@pytest.mark.asyncio
async def test_send_text_and_card():
    api = FakeAPI()
    await send_text(api, "GID", "hello", event_id="E1")
    assert api.posts[0]["msg_type"] == 0
    assert api.posts[0]["content"] == "hello"
    assert api.posts[0]["event_id"] == "E1"

    kb = build_game_keyboard("GID")
    await send_card(api, "GID", "**md**", kb, msg_id="M1")
    post = api.posts[1]
    assert post["msg_type"] == 2
    assert post["markdown"] == {"content": "**md**"}
    assert post["keyboard"] is kb
    assert post["msg_id"] == "M1"


def test_build_game_keyboard():
    kb = build_game_keyboard("GID")
    buttons = kb["content"]["rows"][0]["buttons"]
    datas = {b["action"]["data"] for b in buttons}
    assert datas == {"rg2:shoot:GID", "rg2:status:GID"}
    assert all(b["action"]["type"] == 1 for b in buttons)


def test_parse_interaction():
    assert parse_interaction("rg2:shoot:GID") == ("shoot", "GID")
    assert parse_interaction("rg2:status:GID") == ("status", "GID")
    assert parse_interaction("rg2:unknown:GID") is None
    assert parse_interaction("other:shoot:GID") is None
    assert parse_interaction("") is None
    assert parse_interaction(None) is None


def test_shim():
    bot = object()
    shim = QQInteractionShim(bot, "GID", "MID")
    assert shim.message_obj.group_id == "GID"
    assert shim.bot is bot
    assert shim.get_sender_id() == "MID"
    assert shim.get_sender_name() == "玩家"
    assert shim.is_admin() is False
    assert shim.get_platform_name() == "qq_official"
    assert shim.unified_msg_origin == "qq_official:GroupMessage:GID"
