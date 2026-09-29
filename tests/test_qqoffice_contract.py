"""可选的跨仓契约回归：真实双方插件，只有宿主索引和 HTTP 为本地替身。"""

import asyncio
import importlib
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from astrbot_plugin_rg2.main import QQOFFICE_PLUGIN, RevolverGunPlugin

SOURCE = Path(
    os.environ.get(
        "QQOFFICE_SOURCE", str(Path(__file__).resolve().parents[2] / QQOFFICE_PLUGIN)
    )
)
if not (SOURCE / "core" / "plugin_service.py").is_file():
    pytest.skip(
        "设置 QQOFFICE_SOURCE 可运行真实提供者契约测试", allow_module_level=True
    )
Client = pytest.importorskip("botpy.client").Client
ConnectionState = pytest.importorskip("botpy.connection").ConnectionState
package = types.ModuleType("sdk_contract_qqoffice")
package.__path__ = [str(SOURCE)]
sys.modules[package.__name__] = package
Provider = importlib.import_module(f"{package.__name__}.main").Main
Events = importlib.import_module(f"{package.__name__}.core.events")
Routing = importlib.import_module(f"{package.__name__}.core.routing")


class LocalHTTP:
    def __init__(self):
        self._token = object()
        self.is_sandbox = False
        self.calls = []
        self.fail_messages = False

    async def request(self, route, **kwargs):
        self.calls.append((route.method, route.path, kwargs))
        if (
            self.fail_messages
            and route.method == "POST"
            and route.path.endswith("/messages")
        ):
            raise TimeoutError("upstream result is unknown")
        return {"id": f"M{len(self.calls)}", "file_info": "local-file", "ttl": 3600}


def make_adapter(pid="A", appid="bot-A"):
    http = LocalHTTP()
    client = Client.__new__(Client)
    client._closed = False
    client.loop = asyncio.get_running_loop()
    client.api = NS(_http=http)
    client.http = http
    client.intents = 1 << 30
    client._connection = NS(
        state=ConnectionState(client.ws_dispatch, client.api), _session_list=[]
    )
    return NS(
        appid=appid,
        config={"id": pid, "appid": appid},
        client=client,
        get_client=lambda: client,
        meta=lambda: NS(name="qq_official", id=pid),
        intents=NS(value=1 << 30),
    )


def native_event(adapter, pid="A", group="G"):
    event = NS(
        bot=adapter.client,
        get_platform_id=lambda: pid,
        get_platform_name=lambda: "qq_official",
        platform=NS(name="qq_official"),
        message_obj=NS(group_id=group, message_id="NATIVE", raw_message={}),
        get_sender_id=lambda: "U",
        get_sender_name=lambda: "玩家",
        is_admin=lambda: False,
        unified_msg_origin=f"{pid}:GroupMessage:{group}",
    )
    event.stop_event = lambda: setattr(event, "stopped", True)
    return event


def button(provider, pid="A", action="status", group="G"):
    route = provider.routes.ensure_current_route(pid)
    return Events.QQOfficeEvent(
        type="INTERACTION_CREATE",
        name="interaction_create",
        raw={
            "type": 11,
            "data": {"resolved": {"button_data": f"rg2:{action}:{group}"}},
        },
        payload_id="BUTTON-EVENT",
        interaction_id="BUTTON",
        scene="group",
        group_openid=group,
        member_openid="U",
        source=Routing.EventSource(pid, route.robot_key, route.generation),
    )


@pytest.fixture
async def pair(tmp_path, monkeypatch):
    def data_dir(name=None):
        path = tmp_path / (name or "provider")
        path.mkdir(exist_ok=True)
        return path

    monkeypatch.setattr("astrbot_plugin_rg2.main.StarTools.get_data_dir", data_dir)
    adapters = {"A": make_adapter(), "B": make_adapter("B", "bot-B")}
    manager = NS(_inst_map={key: {"inst": value} for key, value in adapters.items()})
    registry = {}
    context = NS(
        platform_manager=manager,
        get_registered_star=registry.get,
        add_llm_tools=lambda *args: None,
        get_config=lambda *args: {},
    )
    provider = Provider(context, {"retry_max": 0})
    meta = NS(name=QQOFFICE_PLUGIN, activated=True, star_cls=provider, version="0.3.0")
    registry[QQOFFICE_PLUGIN] = meta
    consumer = RevolverGunPlugin(context, {"qq_card_enabled": True})
    try:
        yield NS(
            provider=provider,
            consumer=consumer,
            meta=meta,
            registry=registry,
            adapters=adapters,
            manager=manager,
        )
    finally:
        await consumer.terminate()
        await provider.terminate()


async def start(pair):
    await pair.provider.initialize()
    await pair.consumer.initialize()
    assert pair.consumer.qq_svc is pair.provider.get_service()


@pytest.mark.asyncio
async def test_real_provider_native_and_button_messages(pair):
    await start(pair)
    event = native_event(pair.adapters["A"])
    assert await pair.consumer._reply_load_result(event, "G", ["开始"])
    body = pair.adapters["A"].client.http.calls[-1][2]["json"]
    assert body["msg_id"] == "NATIVE"
    assert body["markdown"]["content"] == "开始"
    assert event.stopped is True
    await pair.consumer._on_qqoffice_event(button(pair.provider, pid="B"))
    sends = [
        call
        for call in pair.adapters["B"].client.http.calls
        if call[0] == "POST" and call[1].endswith("/messages")
    ]
    assert len(sends) == 1
    assert sends[0][2]["json"]["event_id"] == "BUTTON-EVENT"
    assert "content" in sends[0][2]["json"]["markdown"]


@pytest.mark.asyncio
async def test_real_provider_plain_button_reply(pair):
    await start(pair)
    pair.consumer.qq_card_enabled = False
    await pair.consumer._on_qqoffice_event(button(pair.provider))
    sends = [
        call
        for call in pair.adapters["A"].client.http.calls
        if call[1].endswith("/messages")
    ]
    assert len(sends) == 1
    assert sends[0][2]["json"]["event_id"] == "BUTTON-EVENT"


@pytest.mark.asyncio
async def test_real_provider_unready_is_not_bound(pair):
    await pair.consumer.initialize()
    assert pair.consumer.get_service().get_status()["ready"] is True
    assert pair.consumer.qq_svc is None
    assert pair.consumer.qqoffice_dependency_status()["available"] is False
    await pair.provider.initialize()
    await pair.consumer._on_expand_loaded(pair.meta)
    assert pair.consumer.qq_svc is pair.provider.get_service()


@pytest.mark.asyncio
async def test_dependency_status_tracks_current_registry(pair):
    await start(pair)
    pair.meta.activated = False
    dep = pair.consumer.get_service().get_status()["dependencies"]["qqoffice"]
    assert dep["available"] is False
    assert dep["state"] == "disabled"
    assert dep["activated"] is False
    pair.registry.clear()
    assert pair.consumer.qqoffice_dependency_status()["state"] == "missing"


@pytest.mark.asyncio
async def test_closed_provider_rejected_and_consumer_unbinds(pair):
    await start(pair)
    old = pair.provider.get_service()
    await pair.provider.terminate()
    assert pair.consumer.qqoffice_dependency_status()["available"] is False
    assert pair.consumer._try_bind_qqoffice() is False
    assert pair.consumer.qq_svc is None
    assert old.get_status()["state"] == "closed"


@pytest.mark.asyncio
async def test_unknown_card_send_is_not_replayed_as_text(pair):
    await start(pair)
    http = pair.adapters["A"].client.http
    http.fail_messages = True
    await pair.consumer._on_qqoffice_event(button(pair.provider))
    sends = [
        call
        for call in http.calls
        if call[0] == "POST" and call[1].endswith("/messages")
    ]
    assert len(sends) == 1


@pytest.mark.asyncio
async def test_real_provider_mute_and_delayed_identity_guard(pair):
    await start(pair)
    event = native_event(pair.adapters["A"])
    view = pair.consumer._view_for_event(event)
    assert await pair.consumer._ban_user(event, "U", is_bannable=True, qq_view=view) > 0
    mute = pair.adapters["A"].client.http.calls[-1]
    assert mute[1] == "/v2/groups/G/restrict_chat_setting"
    assert mute[2]["json"]["members"][0]["member_openid"] == "U"
    delayed = pair.consumer._delay_view_for(event)
    replacement = make_adapter("A", "different-bot")
    pair.manager._inst_map["A"] = {"inst": replacement}
    await pair.consumer._send_group_text(None, "G", "通知", qq_view=delayed)
    assert replacement.client.http.calls == []


@pytest.mark.asyncio
async def test_unknown_native_reply_does_not_fall_back(pair):
    await start(pair)
    http = pair.adapters["A"].client.http
    http.fail_messages = True
    event = native_event(pair.adapters["A"])
    assert await pair.consumer._reply_load_result(event, "G", ["开始"]) is True
    assert event.stopped is True
    assert len(http.calls) == 1


@pytest.mark.asyncio
async def test_dependency_reload_replaces_cached_service(pair):
    await start(pair)
    old = pair.consumer.qq_svc
    await pair.provider.terminate()
    replacement = Provider(pair.provider.context, {"retry_max": 0})
    pair.meta.star_cls = replacement
    try:
        await replacement.initialize()
        await pair.consumer._on_expand_loaded(pair.meta)
        assert pair.consumer.qq_svc is replacement.get_service()
        assert pair.consumer.qq_svc is not old
        assert old.get_status()["state"] == "closed"
        assert pair.consumer.qqoffice_dependency_status()["available"] is True
    finally:
        await replacement.terminate()


@pytest.mark.asyncio
async def test_dependency_probe_does_not_bind_or_send(pair):
    await pair.provider.initialize()
    status = pair.consumer.qqoffice_dependency_status()
    assert status["state"] == "unbound" and status["ready"] is True
    assert status["available"] is False and pair.consumer.qq_svc is None
    assert all(not adapter.client.http.calls for adapter in pair.adapters.values())


@pytest.mark.asyncio
async def test_missing_message_receipt_keeps_previous_card(pair):
    await start(pair)
    http = pair.adapters["A"].client.http

    async def no_receipt(route, **kwargs):
        http.calls.append((route.method, route.path, kwargs))
        return {}

    http.request = no_receipt
    pair.consumer._last_card_msg["G"] = ("bot-A@production", "previous")
    event = native_event(pair.adapters["A"])
    assert await pair.consumer._reply_status_result(event, "G", "状态") is True
    assert event.stopped is True and len(http.calls) == 1
    assert pair.consumer._last_card_msg["G"] == ("bot-A@production", "previous")


@pytest.mark.asyncio
async def test_delayed_view_keeps_original_button_identity(pair):
    await start(pair)
    view = pair.consumer._view_for_event(button(pair.provider))
    shim = NS(qq_view=view)
    replacement = make_adapter("A", "different-bot")
    pair.manager._inst_map["A"] = {"inst": replacement}
    assert pair.consumer._delay_view_for(shim) is None
    assert replacement.client.http.calls == []
