import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot_plugin_rg2.main import INSTALL_HINT, RevolverGunPlugin


def make_plugin(tmp_path, monkeypatch, card_enabled=True):
    """构造插件实例并绑定 fake 中台 svc。"""
    monkeypatch.setattr(
        "astrbot_plugin_rg2.main.StarTools.get_data_dir", lambda name: tmp_path
    )
    plugin = RevolverGunPlugin(None, {"qq_card_enabled": card_enabled})
    svc = SimpleNamespace(
        send_rich=AsyncMock(return_value={"id": "MSG1"}),
        group=SimpleNamespace(
            send=AsyncMock(), recall=AsyncMock(), mute_member=AsyncMock()
        ),
    )
    plugin.qq_svc = svc
    return plugin, svc


def make_event(button_data, group_openid="GID", member="MID", payload_id="E1"):
    """模拟中台归一后的 QQOfficeEvent。"""
    return SimpleNamespace(
        is_interaction=True,
        raw={"data": {"resolved": {"button_data": button_data}}},
        group_openid=group_openid,
        member_openid=member,
        payload_id=payload_id,
    )


def cleanup_tasks(plugin):
    for task in plugin.timeout_tasks.values():
        task.cancel()


@pytest.mark.asyncio
async def test_interaction_not_ours(tmp_path, monkeypatch):
    plugin, svc = make_plugin(tmp_path, monkeypatch)
    await plugin._on_qqoffice_event(make_event("other:shoot:GID"))
    svc.send_rich.assert_not_called()
    svc.group.send.assert_not_called()


@pytest.mark.asyncio
async def test_interaction_status(tmp_path, monkeypatch):
    """无游戏时点状态：回复带「开始游戏」按钮的卡片。"""
    plugin, svc = make_plugin(tmp_path, monkeypatch)
    await plugin._on_qqoffice_event(make_event("rg2:status:GID"))
    kw = svc.send_rich.call_args.kwargs
    assert kw["scene"] == "group"
    assert kw["target_openid"] == "GID"
    assert kw["event_id"] == "E1"  # 被动回复，不占主动频次
    assert kw["event_id_source"] == "INTERACTION_CREATE"
    assert "没有游戏进行中" in kw["markdown"]["markdown"]["content"]
    buttons = kw["keyboard"]["keyboard"]["content"]["rows"][0]["buttons"]
    assert buttons[0]["action"]["data"] == "rg2:load:GID"


@pytest.mark.asyncio
async def test_interaction_load_starts_game(tmp_path, monkeypatch):
    """点「开始游戏」：随机装填开局并回复带开枪按钮的卡片。"""
    plugin, svc = make_plugin(tmp_path, monkeypatch)
    try:
        await plugin._on_qqoffice_event(make_event("rg2:load:GID"))
        assert "GID" in plugin.group_games  # 游戏已创建
        kw = svc.send_rich.call_args.kwargs
        buttons = kw["keyboard"]["keyboard"]["content"]["rows"][0]["buttons"]
        assert {b["action"]["data"] for b in buttons} == {
            "rg2:shoot:GID",
            "rg2:status:GID",
        }
    finally:
        cleanup_tasks(plugin)


@pytest.mark.asyncio
async def test_interaction_wrong_group(tmp_path, monkeypatch):
    """回调数据与事件群不一致：忽略且不发消息（ack 由中台自动完成）。"""
    plugin, svc = make_plugin(tmp_path, monkeypatch)
    await plugin._on_qqoffice_event(make_event("rg2:shoot:OTHER", group_openid="GID"))
    svc.send_rich.assert_not_called()
    svc.group.send.assert_not_called()


@pytest.mark.asyncio
async def test_interaction_shoot_sends_fresh_card(tmp_path, monkeypatch):
    """游戏进行中点击开枪：结果以带新按钮的卡片下发（按钮点击后不可复原）。"""
    plugin, svc = make_plugin(tmp_path, monkeypatch)
    # 一空一实，首枪空弹，游戏继续
    plugin.group_games["GID"] = {
        "chambers": [False, True, False, False, False, False],
        "current": 0,
        "start_time": datetime.datetime.now(),
        "shot_count": 0,
    }
    try:
        await plugin._on_qqoffice_event(make_event("rg2:shoot:GID"))
        kw = svc.send_rich.call_args.kwargs
        assert kw["event_id"] == "E1"
        buttons = kw["keyboard"]["keyboard"]["content"]["rows"][0]["buttons"]
        assert {b["action"]["data"] for b in buttons} == {
            "rg2:shoot:GID",
            "rg2:status:GID",
        }
        assert "GID" in plugin.group_games  # 游戏仍在进行
    finally:
        cleanup_tasks(plugin)


@pytest.mark.asyncio
async def test_card_recall_previous(tmp_path, monkeypatch):
    """第二张卡发出后撤回第一张，跟踪记录更新为新 id。"""
    plugin, svc = make_plugin(tmp_path, monkeypatch)
    svc.send_rich.side_effect = [{"id": "MSG1"}, {"id": "MSG2"}]
    await plugin._send_card("GID", "c1", {}, event_id="E1")
    svc.group.recall.assert_not_called()
    await plugin._send_card("GID", "c2", {}, event_id="E1")
    svc.group.recall.assert_awaited_once_with("GID", "MSG1")
    assert plugin._last_card_msg["GID"] == "MSG2"


@pytest.mark.asyncio
async def test_card_recall_failure_tolerated(tmp_path, monkeypatch):
    """撤回失败（超2分钟窗口）不影响主流程，跟踪记录仍更新。"""
    plugin, svc = make_plugin(tmp_path, monkeypatch)
    svc.send_rich.side_effect = [{"id": "MSG1"}, {"id": "MSG2"}]
    svc.group.recall.side_effect = RuntimeError("超过2分钟")
    await plugin._send_card("GID", "c1", {}, event_id="E1")
    resp = await plugin._send_card("GID", "c2", {}, event_id="E1")
    assert resp == {"id": "MSG2"}
    assert plugin._last_card_msg["GID"] == "MSG2"


@pytest.mark.asyncio
async def test_card_send_failure_fallback(tmp_path, monkeypatch):
    """卡片发送失败回退纯文本，不触发撤回。"""
    plugin, svc = make_plugin(tmp_path, monkeypatch)
    svc.send_rich.side_effect = RuntimeError("x")
    await plugin._on_qqoffice_event(make_event("rg2:status:GID"))
    svc.group.send.assert_awaited_once()
    args, kw = svc.group.send.call_args
    assert args[0] == "GID" and "没有游戏进行中" in args[1]
    assert kw["event_id"] == "E1"
    svc.group.recall.assert_not_called()


@pytest.mark.asyncio
async def test_reply_load_result_card(tmp_path, monkeypatch):
    plugin, svc = make_plugin(tmp_path, monkeypatch)
    event = SimpleNamespace(
        message_obj=SimpleNamespace(group_id="GID", message_id="M1"),
        get_platform_name=lambda: "qq_official",
        stop_event=MagicMock(),
    )
    handled = await plugin._reply_load_result(event, "GID", ["装填完毕"])
    assert handled is True
    assert svc.send_rich.call_args.args[0] is event
    # 卡片直发后必须 stop_event，否则事件会继续流入 LLM
    event.stop_event.assert_called_once()


@pytest.mark.asyncio
async def test_reply_load_result_fallback(tmp_path, monkeypatch):
    """卡片发送失败返回 False，让调用方走原文本逻辑。"""
    plugin, svc = make_plugin(tmp_path, monkeypatch)
    svc.send_rich.side_effect = RuntimeError("x")
    event = SimpleNamespace(
        message_obj=SimpleNamespace(group_id="GID", message_id="M1"),
        get_platform_name=lambda: "qq_official",
    )
    assert await plugin._reply_load_result(event, "GID", ["装填完毕"]) is False


def test_qq_gate(tmp_path, monkeypatch):
    """svc 未绑定时官机事件回安装提示，OneBot 不受影响。"""
    plugin, svc = make_plugin(tmp_path, monkeypatch)
    qq_event = SimpleNamespace(get_platform_name=lambda: "qq_official")
    ob_event = SimpleNamespace(get_platform_name=lambda: "aiocqhttp")
    plugin.qq_svc = None
    assert plugin._qq_gate(qq_event) == INSTALL_HINT
    assert plugin._qq_gate(ob_event) is None
    plugin.qq_svc = svc
    assert plugin._qq_gate(qq_event) is None


@pytest.mark.asyncio
async def test_terminate_clears_card_tracking(tmp_path, monkeypatch):
    plugin, _ = make_plugin(tmp_path, monkeypatch)
    plugin._last_card_msg["GID"] = "MSG1"
    await plugin.terminate()
    assert plugin._last_card_msg == {}
    assert plugin.qq_svc is None
