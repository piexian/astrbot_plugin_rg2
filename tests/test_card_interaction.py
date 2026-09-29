"""卡片/按钮交互回归：替身对齐 qqoffice_expand SDK v1 真实公开面。

不虚构根服务 send_rich/group：所有发送/禁言/撤回断言都落在
``svc.for_event(event)`` 返回的绑定视图上（见 tests/qqoffice_fake.py）。
"""

import asyncio
import datetime
from types import SimpleNamespace

import pytest

from astrbot_plugin_rg2.main import INSTALL_HINT, QQOFFICE_PLUGIN, RevolverGunPlugin

from tests.qqoffice_fake import (
    FakeNativeEvent,
    FakeProviderStar,
    FakeQQOfficeEvent,
    FakeQQOfficeService,
)


def make_context(svc: FakeQQOfficeService | None, version="0.3.0"):
    """构造 get_registered_star 替身 Context；svc=None 表示注册表无此插件。"""
    if svc is None:
        return SimpleNamespace(get_registered_star=lambda name: None)
    meta = SimpleNamespace(
        name=QQOFFICE_PLUGIN,
        activated=True,
        star_cls=FakeProviderStar(svc),
        version=version,
    )
    return SimpleNamespace(get_registered_star=lambda name: meta)


def make_plugin(tmp_path, monkeypatch, card_enabled=True, context=None):
    """构造插件实例（不绑定中台）。"""
    monkeypatch.setattr(
        "astrbot_plugin_rg2.main.StarTools.get_data_dir", lambda name: tmp_path
    )
    return RevolverGunPlugin(context, {"qq_card_enabled": card_enabled})


def make_bound_plugin(
    tmp_path, monkeypatch, card_enabled=True, appid="APP-1", platform_id="qq_official"
):
    """构造插件并通过 get_service(api_version=1) 真实绑定路径接入中台。"""
    svc = FakeQQOfficeService(platform_id, appid)
    plugin = make_plugin(tmp_path, monkeypatch, card_enabled, context=make_context(svc))
    assert plugin._try_bind_qqoffice() is True
    return plugin, svc


def make_button_event(
    svc,
    button_data,
    group_openid="GID",
    member="MID",
    payload_id="E1",
    platform_id="qq_official",
):
    route = svc.routes[platform_id]
    raw = {"data": {"resolved": {"button_data": button_data}}}
    return FakeQQOfficeEvent(
        route,
        group_openid=group_openid,
        member_openid=member,
        payload_id=payload_id,
        raw=raw,
    )


def cleanup_tasks(plugin):
    for task in plugin.timeout_tasks.values():
        task.cancel()


async def wait_until(predicate, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


def kind_calls(svc, kind):
    return [c for c in svc.calls if c["kind"] == kind]


@pytest.mark.asyncio
async def test_interaction_not_ours(tmp_path, monkeypatch):
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    await svc.dispatch(make_button_event(svc, "other:shoot:GID"))
    assert svc.calls == []


@pytest.mark.asyncio
async def test_interaction_status(tmp_path, monkeypatch):
    """无游戏时点状态：经原始事件视图回复带「开始游戏」按钮的卡片（被动 event_id）。"""
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    await svc.dispatch(make_button_event(svc, "rg2:status:GID"))
    cards = kind_calls(svc, "send_rich")
    assert len(cards) == 1
    card = cards[0]
    assert card["appid"] == "APP-1"  # 来源机器人身份
    assert card["scene"] == "group" and card["target"] == "GID"
    assert card["event_id"] == "E1"  # 被动回复，不占主动频次
    assert card["event_id_source"] == "INTERACTION_CREATE"
    assert "没有游戏进行中" in card["markdown"]["markdown"]["content"]
    buttons = card["keyboard"]["keyboard"]["content"]["rows"][0]["buttons"]
    assert buttons[0]["action"]["data"] == "rg2:load:GID"


@pytest.mark.asyncio
async def test_interaction_load_starts_game(tmp_path, monkeypatch):
    """点「开始游戏」：随机装填开局并回复带开枪按钮的卡片。"""
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    try:
        await svc.dispatch(make_button_event(svc, "rg2:load:GID"))
        assert "GID" in plugin.group_games  # 游戏已创建
        card = kind_calls(svc, "send_rich")[0]
        buttons = card["keyboard"]["keyboard"]["content"]["rows"][0]["buttons"]
        assert {b["action"]["data"] for b in buttons} == {
            "rg2:shoot:GID",
            "rg2:status:GID",
        }
    finally:
        cleanup_tasks(plugin)


@pytest.mark.asyncio
async def test_interaction_wrong_group(tmp_path, monkeypatch):
    """回调数据与事件群不一致：忽略且不发消息（ack 由中台自动完成）。"""
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    await svc.dispatch(make_button_event(svc, "rg2:shoot:OTHER", group_openid="GID"))
    assert svc.calls == []


@pytest.mark.asyncio
async def test_interaction_stale_source_not_sent(tmp_path, monkeypatch):
    """事件来源代次已失效（中台重载）：不构造无来源发送，直接忽略本次回调。"""
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    ev = make_button_event(svc, "rg2:status:GID")  # 来源代次 = 1
    svc.routes["qq_official"].reload()  # 代次递增，旧事件来源失效
    await svc.dispatch(ev)
    assert svc.calls == []


@pytest.mark.asyncio
async def test_interaction_shoot_sends_fresh_card(tmp_path, monkeypatch):
    """游戏进行中点击开枪：结果以带新按钮的卡片下发（按钮点击后不可复原）。"""
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    # 一空一实，首枪空弹，游戏继续
    plugin.group_games["GID"] = {
        "chambers": [False, True, False, False, False, False],
        "current": 0,
        "start_time": datetime.datetime.now(),
        "shot_count": 0,
    }
    try:
        await svc.dispatch(make_button_event(svc, "rg2:shoot:GID"))
        card = kind_calls(svc, "send_rich")[0]
        assert card["event_id"] == "E1"
        buttons = card["keyboard"]["keyboard"]["content"]["rows"][0]["buttons"]
        assert {b["action"]["data"] for b in buttons} == {
            "rg2:shoot:GID",
            "rg2:status:GID",
        }
        assert "GID" in plugin.group_games  # 游戏仍在进行
    finally:
        cleanup_tasks(plugin)


@pytest.mark.asyncio
async def test_interaction_shoot_ban_via_view(tmp_path, monkeypatch):
    """按钮开枪中弹：禁言经来源视图（group.mute_member）落到正确群/成员。"""
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    plugin.group_games["GID"] = {
        "chambers": [True, False, False, False, False, False],
        "current": 0,
        "start_time": datetime.datetime.now(),
        "shot_count": 0,
    }
    try:
        await svc.dispatch(make_button_event(svc, "rg2:shoot:GID", member="MID"))
        mutes = kind_calls(svc, "group.mute_member")
        assert len(mutes) == 1
        assert mutes[0]["target"] == "GID" and mutes[0]["member_openid"] == "MID"
        assert mutes[0]["appid"] == "APP-1"
        card = kind_calls(svc, "send_rich")[0]
        assert "禁言" in card["markdown"]["markdown"]["content"]
        assert "GID" not in plugin.group_games  # 全部实弹已射，游戏结束
    finally:
        cleanup_tasks(plugin)


@pytest.mark.asyncio
async def test_card_recall_previous(tmp_path, monkeypatch):
    """第二张卡发出后撤回第一张，跟踪记录更新为新 id（按视图身份隔离）。"""
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    view = svc.for_event(make_button_event(svc, "rg2:status:GID"))
    await plugin._send_card("GID", "c1", {}, view=view)
    assert kind_calls(svc, "group.recall") == []
    await plugin._send_card("GID", "c2", {}, view=view)
    recalls = kind_calls(svc, "group.recall")
    assert len(recalls) == 1 and recalls[0]["message_id"] == "MSG1"
    assert plugin._last_card_msg["GID"] == ("APP-1", "MSG2")


@pytest.mark.asyncio
async def test_card_recall_failure_tolerated(tmp_path, monkeypatch):
    """撤回失败（超2分钟窗口）不影响主流程，跟踪记录仍更新。"""

    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    view = svc.for_event(make_button_event(svc, "rg2:status:GID"))

    async def fail_recall(openid, message_id, **kw):
        raise RuntimeError("超过2分钟")

    view.group.recall = fail_recall
    await plugin._send_card("GID", "c1", {}, view=view)
    resp = await plugin._send_card("GID", "c2", {}, view=view)
    assert resp["id"] == "MSG2"
    assert plugin._last_card_msg["GID"] == ("APP-1", "MSG2")


@pytest.mark.asyncio
async def test_card_no_cross_identity_recall(tmp_path, monkeypatch):
    """机器人改绑后新卡片不撤回旧机器人发送的卡片（不跨身份操作）。"""
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    old_view = svc.for_event(make_button_event(svc, "rg2:status:GID"))
    await plugin._send_card("GID", "c1", {}, view=old_view)
    svc.routes["qq_official"].rebind("APP-2")  # 改绑到另一机器人
    new_view = svc.for_event(make_button_event(svc, "rg2:status:GID"))
    resp = await plugin._send_card("GID", "c2", {}, view=new_view)
    assert resp["id"] == "MSG2"
    assert kind_calls(svc, "group.recall") == []  # 旧身份卡片不能被新身份撤回
    assert plugin._last_card_msg["GID"] == ("APP-2", "MSG2")


@pytest.mark.asyncio
async def test_card_send_failure_fallback(tmp_path, monkeypatch):
    """卡片发送失败回退纯文本（经同一视图），不触发撤回。"""
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    rejection = RuntimeError("明确拒绝卡片")
    rejection.phase = "rejected"
    svc.fail_send_rich = rejection
    await svc.dispatch(make_button_event(svc, "rg2:status:GID"))
    texts = kind_calls(svc, "group.send")
    assert len(texts) == 1
    assert texts[0]["target"] == "GID" and "没有游戏进行中" in texts[0]["content"]
    assert texts[0]["event_id"] == "E1"
    assert kind_calls(svc, "group.recall") == []


@pytest.mark.asyncio
async def test_reply_load_result_card(tmp_path, monkeypatch):
    """普通群消息装填：经 for_event 视图发卡片（被动 msg_id 随事件携带）。"""
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    event = FakeNativeEvent(svc.routes["qq_official"], "GID")
    handled = await plugin._reply_load_result(event, "GID", ["装填完毕"])
    assert handled is True
    card = kind_calls(svc, "send_rich")[0]
    assert card["scene"] == "group" and card["target"] == "GID"
    assert card["msg_id"] == "NMSG1"  # 被动回复窗口
    assert card["event_id"] is None  # 普通消息不伪造 event_id
    # 卡片直发后必须 stop_event，否则事件会继续流入 LLM
    assert event.stopped is True


@pytest.mark.asyncio
async def test_reply_load_result_fallback(tmp_path, monkeypatch):
    """卡片发送失败返回 False，让调用方走原文本逻辑。"""
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    rejection = RuntimeError("明确未发送")
    rejection.phase = "not_sent"
    svc.fail_send_rich = rejection
    event = FakeNativeEvent(svc.routes["qq_official"], "GID")
    assert await plugin._reply_load_result(event, "GID", ["装填完毕"]) is False
    assert getattr(event, "stopped", False) is not True


@pytest.mark.asyncio
async def test_reply_load_result_stale_source(tmp_path, monkeypatch):
    """来源核验失败（适配器已重载）：不发送卡片，回退原文本逻辑。"""
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    route = svc.routes["qq_official"]
    event = FakeNativeEvent(route, "GID")
    route.reload()  # client 对象更换 → 事件来源过期
    assert await plugin._reply_load_result(event, "GID", ["装填完毕"]) is False
    assert svc.calls == []


def test_qq_gate(tmp_path, monkeypatch):
    """svc 未绑定时官机事件回安装提示，OneBot 不受影响。"""
    plugin = make_plugin(tmp_path, monkeypatch)
    qq_event = SimpleNamespace(get_platform_name=lambda: "qq_official")
    ob_event = SimpleNamespace(get_platform_name=lambda: "aiocqhttp")
    assert plugin._qq_gate(qq_event) == INSTALL_HINT
    assert plugin._qq_gate(ob_event) is None
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    assert plugin._qq_gate(qq_event) is None


@pytest.mark.asyncio
async def test_terminate_unsubscribes_and_clears(tmp_path, monkeypatch):
    """卸载：解除订阅（事件不再进入处理器）、清理卡片跟踪、服务转入 closed。"""
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    plugin._last_card_msg["GID"] = ("APP-1", "MSG1")
    await plugin.terminate()
    assert plugin._last_card_msg == {}
    assert plugin.qq_svc is None
    assert svc._subs.get("INTERACTION_CREATE") == []  # 订阅已解绑
    status = plugin.get_service().get_status()
    assert status["state"] == "closed" and status["ready"] is False
    await svc.dispatch(make_button_event(svc, "rg2:status:GID"))
    assert svc.calls == []  # 卸载后回调不再进入业务


@pytest.mark.asyncio
async def test_timeout_notification_follows_same_identity(tmp_path, monkeypatch):
    """同身份重载后超时通知仍发到原机器人（instance 长期视图跟随重载）。"""
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    plugin.timeout = 0.1
    await svc.dispatch(make_button_event(svc, "rg2:load:GID"))
    assert "GID" in plugin.group_games
    svc.routes["qq_official"].reload()  # 同身份重载（代次递增，AppID 不变）
    assert await wait_until(lambda: "GID" not in plugin.group_games)
    texts = await _wait_texts(svc)
    assert texts and texts[0]["target"] == "GID" and texts[0]["appid"] == "APP-1"
    # 卡片只有开局按钮回复这一张，超时通知走主动文本
    assert len(kind_calls(svc, "send_rich")) == 1


async def _wait_texts(svc):
    async def poll():
        return kind_calls(svc, "group.send")

    deadline = asyncio.get_running_loop().time() + 2.0
    while asyncio.get_running_loop().time() < deadline:
        texts = kind_calls(svc, "group.send")
        if texts:
            return texts
        await asyncio.sleep(0.02)
    return []


@pytest.mark.asyncio
async def test_timeout_notification_never_switches_bot(tmp_path, monkeypatch):
    """改绑其他机器人后超时通知失败放弃，不跨机器人发送。"""
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    plugin.timeout = 0.1
    await svc.dispatch(make_button_event(svc, "rg2:load:GID"))
    svc.routes["qq_official"].rebind("APP-2")  # 改绑其他 AppID
    await asyncio.sleep(0.4)
    assert await wait_until(lambda: "GID" not in plugin.group_games)
    await asyncio.sleep(0.1)
    assert kind_calls(svc, "group.send") == []  # 绝不自动换机器人


@pytest.mark.asyncio
async def test_multi_instance_source_isolation(tmp_path, monkeypatch):
    """双实例（双机器人）：各自事件的消息走各自身份视图，互不串。"""
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    svc.add_route("qq_sales2", "APP-2")
    await svc.dispatch(
        make_button_event(
            svc, "rg2:status:G1", group_openid="G1", platform_id="qq_official"
        )
    )
    await svc.dispatch(
        make_button_event(
            svc, "rg2:status:G2", group_openid="G2", platform_id="qq_sales2"
        )
    )
    cards = kind_calls(svc, "send_rich")
    assert [(c["appid"], c["target"]) for c in cards] == [
        ("APP-1", "G1"),
        ("APP-2", "G2"),
    ]


@pytest.mark.asyncio
async def test_ai_flow_proactive_send_via_identity_view(tmp_path, monkeypatch):
    """AI 触发补发：官机走事件同身份视图主动发送，不落到任意机器人。"""
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    event = FakeNativeEvent(svc.routes["qq_official"], "GID")
    await plugin.ai_check_status(event)
    texts = kind_calls(svc, "group.send")
    assert len(texts) == 1
    assert texts[0]["target"] == "GID" and texts[0]["appid"] == "APP-1"
    assert texts[0]["msg_id"] is None  # 主动消息（不带被动窗口）


@pytest.mark.asyncio
async def test_concurrent_cards_track_latest_before_recall(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    plugin._last_card_msg["GID"] = ("bot", "old")
    ids = iter(("new-a", "new-b"))
    started, release = asyncio.Event(), asyncio.Event()
    recalled = []

    async def send_rich(**kwargs):
        return {"id": next(ids)}

    async def recall(group, message):
        recalled.append(message)
        if len(recalled) == 1:
            started.set()
            await release.wait()

    view = SimpleNamespace(
        prefix=lambda: "bot", send_rich=send_rich, group=SimpleNamespace(recall=recall)
    )
    first = asyncio.create_task(plugin._send_card("GID", "first", {}, view=view))
    try:
        await asyncio.wait_for(started.wait(), 1)
        await plugin._send_card("GID", "second", {}, view=view)
    finally:
        release.set()
        await first
    assert recalled == ["old", "new-a"]
    assert plugin._last_card_msg["GID"] == ("bot", "new-b")
