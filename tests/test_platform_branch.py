import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_rg2.main import RevolverGunPlugin


def make_plugin(tmp_path, monkeypatch):
    """绕过 AstrBot 运行时构造插件实例。"""
    monkeypatch.setattr(
        "astrbot_plugin_rg2.main.StarTools.get_data_dir", lambda name: tmp_path
    )
    plugin = RevolverGunPlugin(None, {})
    return plugin


def make_event(platform="aiocqhttp", group_id="123", sender="456", role="member"):
    event = SimpleNamespace()
    event.message_obj = SimpleNamespace(group_id=group_id)
    event.unified_msg_origin = f"{platform}:GroupMessage:{group_id}"
    event.get_sender_id = lambda: sender
    event.get_sender_name = lambda: "测试员"
    event.is_admin = lambda: False
    event.get_platform_name = lambda: platform
    if platform in ("qq_official", "qq_official_webhook"):
        event.message_obj.raw_message = SimpleNamespace(
            raw_data={"author": {"member_role": role}}
        )
    return event


def test_get_group_id_returns_str(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    assert plugin._get_group_id(make_event(group_id=123)) == "123"
    assert (
        plugin._get_group_id(make_event(platform="qq_official", group_id="OID"))
        == "OID"
    )
    # message_obj 无 group_id 时从 unified_msg_origin 解析
    event = make_event()
    event.message_obj = SimpleNamespace(group_id=None)
    assert plugin._get_group_id(event) == "123"


def test_misfire_config_str_keys(tmp_path, monkeypatch):
    (tmp_path / "group_misfire.json").write_text(json.dumps({"123": True, 456: False}))
    plugin = make_plugin(tmp_path, monkeypatch)
    assert plugin.group_misfire == {"123": True, "456": False}


@pytest.mark.asyncio
async def test_get_group_role_qqofficial(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    event = make_event(platform="qq_official", role="admin")
    assert await plugin._get_group_role(event, "MID") == "admin"


@pytest.mark.asyncio
async def test_ban_user_qqofficial(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    calls = []

    async def fake_ban(api, group_openid, member_openid, seconds):
        calls.append((group_openid, member_openid, seconds))

    monkeypatch.setattr("astrbot_plugin_rg2.main.ban_member", fake_ban)
    event = make_event(platform="qq_official", group_id="GID", sender="MID")
    event.bot = SimpleNamespace(api=object())
    duration = await plugin._ban_user(event, "MID", is_bannable=True)
    assert duration > 0
    assert calls and calls[0][0] == "GID" and calls[0][1] == "MID"


@pytest.mark.asyncio
async def test_ban_user_onebot_unchanged(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    bot = SimpleNamespace(set_group_ban=AsyncMock())
    event = make_event(group_id=123, sender=456)
    event.bot = bot
    duration = await plugin._ban_user(event, "456", is_bannable=True)
    assert duration > 0
    kwargs = bot.set_group_ban.call_args.kwargs
    assert kwargs["group_id"] == 123  # OneBot 调用点转回 int
    assert kwargs["user_id"] == 456
