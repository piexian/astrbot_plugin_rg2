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
    """无游戏时点状态：回复带「开始游戏」按钮的卡片。"""
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
    assert kw["msg_type"] == 2
    assert "没有游戏进行中" in kw["markdown"]["content"]
    buttons = kw["keyboard"]["content"]["rows"][0]["buttons"]
    assert buttons[0]["action"]["data"] == "rg2:load:GID"


@pytest.mark.asyncio
async def test_interaction_load_starts_game(tmp_path, monkeypatch):
    """点「开始游戏」：随机装填开局并回复带开枪按钮的卡片。"""
    plugin = make_plugin(tmp_path, monkeypatch)
    plugin._qq_client = SimpleNamespace(
        api=SimpleNamespace(post_group_message=AsyncMock())
    )
    interaction = make_interaction("rg2:load:GID")
    try:
        assert await plugin._handle_interaction(interaction) is True
        interaction._api.on_interaction_result.assert_awaited_once_with("I1", 0)
        assert "GID" in plugin.group_games  # 游戏已创建
        kw = plugin._qq_client.api.post_group_message.call_args.kwargs
        assert kw["msg_type"] == 2
        buttons = kw["keyboard"]["content"]["rows"][0]["buttons"]
        assert {b["action"]["data"] for b in buttons} == {
            "rg2:shoot:GID",
            "rg2:status:GID",
        }
    finally:
        for task in plugin.timeout_tasks.values():
            task.cancel()


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
async def test_interaction_shoot_sends_fresh_card(tmp_path, monkeypatch):
    """游戏进行中点击开枪：结果以带新按钮的卡片下发（按钮点击后不可复原）。"""
    import datetime

    plugin = make_plugin(tmp_path, monkeypatch)
    api = SimpleNamespace(post_group_message=AsyncMock())
    plugin._qq_client = SimpleNamespace(api=api)
    # 一空一实，首枪空弹，游戏继续
    plugin.group_games["GID"] = {
        "chambers": [False, True, False, False, False, False],
        "current": 0,
        "start_time": datetime.datetime.now(),
        "shot_count": 0,
    }
    interaction = make_interaction("rg2:shoot:GID")
    try:
        assert await plugin._handle_interaction(interaction) is True
        interaction._api.on_interaction_result.assert_awaited_once_with("I1", 0)
        kw = api.post_group_message.call_args.kwargs
        assert kw["msg_type"] == 2
        assert kw["event_id"] == "E1"
        buttons = kw["keyboard"]["content"]["rows"][0]["buttons"]
        assert {b["action"]["data"] for b in buttons} == {
            "rg2:shoot:GID",
            "rg2:status:GID",
        }
        assert "GID" in plugin.group_games  # 游戏仍在进行
    finally:
        for task in plugin.timeout_tasks.values():
            task.cancel()


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
