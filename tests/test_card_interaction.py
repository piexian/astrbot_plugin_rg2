from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot_plugin_rg2.main import RevolverGunPlugin


def make_plugin(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "astrbot_plugin_rg2.main.StarTools.get_data_dir", lambda name: tmp_path
    )
    return RevolverGunPlugin(None, {"qq_card_enabled": True})


def make_interaction(button_data, group_openid="GID", member="MID", event_id="E1"):
    api = SimpleNamespace(on_interaction_result=AsyncMock())
    return SimpleNamespace(
        id="I1",
        event_id=event_id,
        group_openid=group_openid,
        group_member_openid=member,
        data=SimpleNamespace(resolved=SimpleNamespace(button_data=button_data)),
        _api=api,
    )


@pytest.mark.asyncio
async def test_interaction_not_ours(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    interaction = make_interaction("other:shoot:GID")
    assert await plugin._handle_interaction(interaction) is False
    interaction._api.on_interaction_result.assert_not_called()


@pytest.mark.asyncio
async def test_interaction_status(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    plugin._qq_client = SimpleNamespace(
        api=SimpleNamespace(post_group_message=AsyncMock())
    )
    interaction = make_interaction("rg2:status:GID")
    assert await plugin._handle_interaction(interaction) is True
    interaction._api.on_interaction_result.assert_awaited_once_with("I1", 0)
    kw = plugin._qq_client.api.post_group_message.call_args.kwargs
    assert kw["group_openid"] == "GID"
    assert kw["event_id"] == "E1"  # 被动回复，不占主动频次
    assert "没有游戏进行中" in kw["content"]


@pytest.mark.asyncio
async def test_interaction_wrong_group(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    plugin._qq_client = SimpleNamespace(
        api=SimpleNamespace(post_group_message=AsyncMock())
    )
    interaction = make_interaction("rg2:shoot:OTHER", group_openid="GID")
    assert await plugin._handle_interaction(interaction) is True
    interaction._api.on_interaction_result.assert_awaited_once_with("I1", 1)
    plugin._qq_client.api.post_group_message.assert_not_called()


@pytest.mark.asyncio
async def test_reply_load_result_card(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    send_card_mock = AsyncMock()
    monkeypatch.setattr("astrbot_plugin_rg2.main.send_card", send_card_mock)
    event = SimpleNamespace(
        message_obj=SimpleNamespace(group_id="GID", message_id="M1"),
        bot=SimpleNamespace(api=SimpleNamespace()),
        get_platform_name=lambda: "qq_official",
        stop_event=MagicMock(),
    )
    handled = await plugin._reply_load_result(event, "GID", ["装填完毕"])
    assert handled is True
    kw = send_card_mock.call_args
    assert kw.args[1] == "GID"
    assert kw.kwargs["msg_id"] == "M1"
    # 卡片直发后必须 stop_event，否则事件会继续流入 LLM
    event.stop_event.assert_called_once()


@pytest.mark.asyncio
async def test_reply_load_result_fallback(tmp_path, monkeypatch):
    """卡片发送失败回退纯文本，返回 False 让调用方走原逻辑。"""
    plugin = make_plugin(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "astrbot_plugin_rg2.main.send_card", AsyncMock(side_effect=RuntimeError("x"))
    )
    event = SimpleNamespace(
        message_obj=SimpleNamespace(group_id="GID", message_id="M1"),
        bot=SimpleNamespace(api=SimpleNamespace()),
        get_platform_name=lambda: "qq_official",
    )
    assert await plugin._reply_load_result(event, "GID", ["装填完毕"]) is False
