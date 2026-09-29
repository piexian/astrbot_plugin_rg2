import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_rg2.main import RevolverGunPlugin

from tests.qqoffice_fake import FakeQQOfficeService


def make_plugin(tmp_path, monkeypatch):
    """绕过 AstrBot 运行时构造插件实例。"""
    monkeypatch.setattr(
        "astrbot_plugin_rg2.main.StarTools.get_data_dir", lambda name: tmp_path
    )
    plugin = RevolverGunPlugin(None, {})
    return plugin


def make_bound_plugin(tmp_path, monkeypatch, appid="APP-1", platform_id="qq_official"):
    """构造插件并通过 get_service(api_version=1) 绑定中台替身。"""
    plugin = make_plugin(tmp_path, monkeypatch)
    svc = FakeQQOfficeService(platform_id, appid)
    context = SimpleNamespace(
        get_registered_star=lambda name: SimpleNamespace(
            name="astrbot_plugin_qqoffice_expand",
            activated=True,
            star_cls=type("P", (), {"get_service": lambda self, api_version=1: svc})(),
            version="0.3.0",
        )
    )
    plugin.context = context
    assert plugin._try_bind_qqoffice() is True
    return plugin, svc


def make_event(
    platform="aiocqhttp", group_id="123", sender="456", role="member", svc=None
):
    event = SimpleNamespace()
    event.message_obj = SimpleNamespace(group_id=group_id)
    event.unified_msg_origin = f"{platform}:GroupMessage:{group_id}"
    event.get_sender_id = lambda: sender
    event.get_sender_name = lambda: "测试员"
    event.is_admin = lambda: False
    event.get_platform_name = lambda: platform
    event.get_platform_id = lambda: platform
    if platform in ("qq_official", "qq_official_webhook"):
        if svc is not None:
            event.bot = svc.routes[platform].client
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
    """官机禁言经来源视图落到正确群/成员（不再直呼根服务 group.*）。"""
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    event = make_event(platform="qq_official", group_id="GID", sender="MID", svc=svc)
    duration = await plugin._ban_user(event, "MID", is_bannable=True)
    assert duration > 0
    mutes = [c for c in svc.calls if c["kind"] == "group.mute_member"]
    assert mutes and mutes[0]["target"] == "GID" and mutes[0]["member_openid"] == "MID"
    assert mutes[0]["appid"] == "APP-1"  # 来源机器人身份


@pytest.mark.asyncio
async def test_ban_user_qqofficial_permission_hint(tmp_path, monkeypatch):
    """QQ 官机禁言失败时按错误内容区分：机器人没权限 / 对方是管理免疫。"""
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    event = make_event(platform="qq_official", group_id="GID", sender="MID", svc=svc)
    view = plugin._view_for_event(event)

    async def fake_mute(*args):
        raise RuntimeError("机器人不是群管理员")

    view.group.mute_member = fake_mute
    monkeypatch.setattr(plugin, "_view_for_event", lambda ev: view)
    assert await plugin._ban_user(event, "MID", is_bannable=True) == 0
    assert plugin._last_ban_error == "bot_admin"
    assert "机器人需要群管理员权限" in plugin._format_ban_failure()

    async def fake_mute_immune(*args):
        raise RuntimeError("只能操作普通成员，不能操作群主，管理员")

    view.group.mute_member = fake_mute_immune
    assert await plugin._ban_user(event, "MID", is_bannable=True) == 0
    assert plugin._last_ban_error == "target_immune"
    assert "免疫" in plugin._format_ban_failure()


@pytest.mark.asyncio
async def test_ban_user_qqofficial_stale_source(tmp_path, monkeypatch):
    """来源核验失败（适配器重载）：禁言失败并提示，不换机器人重试。"""
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    event = make_event(platform="qq_official", group_id="GID", sender="MID", svc=svc)
    svc.routes["qq_official"].reload()  # client 对象更换 → 事件来源过期
    assert await plugin._ban_user(event, "MID", is_bannable=True) == 0
    assert plugin._last_ban_error == "no_source"
    assert "来源视图" in plugin._format_ban_failure()
    assert svc.calls == []


@pytest.mark.asyncio
async def test_ban_user_qqofficial_svc_missing(tmp_path, monkeypatch):
    """中台未绑定时官机禁言直接失败并提示安装。"""
    plugin = make_plugin(tmp_path, monkeypatch)
    event = make_event(platform="qq_official", group_id="GID", sender="MID")
    assert await plugin._ban_user(event, "MID", is_bannable=True) == 0
    assert plugin._last_ban_error == "svc_missing"
    assert "qqoffice_expand" in plugin._format_ban_failure()


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


@pytest.mark.asyncio
async def test_send_group_text_onebot_and_view(tmp_path, monkeypatch):
    """OneBot 走 send_group_msg；官机走传入视图；无视图时放弃不发送。"""
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    bot = SimpleNamespace(send_group_msg=AsyncMock())
    await plugin._send_group_text(bot, "123", "hi")
    bot.send_group_msg.assert_awaited_once_with(group_id=123, message="hi")

    view = svc.instance("qq_official")  # 长期视图
    await plugin._send_group_text(None, "GID", "hi", qq_view=view)
    texts = [c for c in svc.calls if c["kind"] == "group.send"]
    assert texts and texts[0]["target"] == "GID"

    await plugin._send_group_text(None, "GID", "hi")  # 无视图 → 仅告警
    assert len([c for c in svc.calls if c["kind"] == "group.send"]) == 1
