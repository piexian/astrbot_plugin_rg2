"""SDK v1 契约回归：get_service 门面、发现层、依赖状态与生命周期。"""

import asyncio
from types import SimpleNamespace

import pytest

from astrbot_plugin_rg2.main import QQOFFICE_PLUGIN
from astrbot_plugin_rg2.public_api import (
    RevolverGameService,
    ServiceAPIVersionError,
)

from tests.test_card_interaction import make_bound_plugin, make_context, make_plugin
from tests.qqoffice_fake import FakeProviderStar, FakeQQOfficeService

STATUS_KEYS = {"api_version", "instance_id", "state", "ready", "reason", "dependencies"}
DEP_KEYS = {
    "available",
    "state",
    "reason",
    "activated",
    "has_instance",
    "api_supported",
    "version",
    "ready",
}


def meta_of(svc=None, *, activated=True, star=None, version="0.3.0"):
    if svc is None and star is None:
        return None
    return SimpleNamespace(
        name=QQOFFICE_PLUGIN,
        activated=activated,
        star_cls=star if star is not None else FakeProviderStar(svc),
        version=version,
    )


# ---------- get_service / 版本协商 ----------


def test_get_service_same_object_per_load(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    assert plugin.get_service() is plugin.get_service(api_version=1)
    assert isinstance(plugin.get_service(), RevolverGameService)


@pytest.mark.parametrize("bad", [True, False, 2, 0, "1", 1.0, None])
def test_get_service_rejects_non_v1(tmp_path, monkeypatch, bad):
    plugin = make_plugin(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError) as ei:
        plugin.get_service(bad)
    assert getattr(ei.value, "code", "") == "unsupported_version"
    assert isinstance(ei.value, ServiceAPIVersionError)


def test_get_service_rejects_int_subclass(tmp_path, monkeypatch):
    class DerivedVersion(int):
        pass

    plugin = make_plugin(tmp_path, monkeypatch)
    with pytest.raises(ServiceAPIVersionError):
        plugin.get_service(DerivedVersion(1))


def test_get_status_shape(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    service = plugin.get_service()
    status = service.get_status()
    assert STATUS_KEYS <= set(status)
    assert status["api_version"] == 1
    assert isinstance(status["instance_id"], str) and status["instance_id"]
    assert status["state"] == "initializing"
    assert status["ready"] is False  # ready 当且仅当 state == ready
    assert status["dependencies"]["qqoffice"].keys() >= DEP_KEYS - {"dependencies"}


def test_capabilities(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    assert plugin.get_service().capabilities() == {"api_version": 1, "features": []}


def test_instance_id_differs_across_reload(tmp_path, monkeypatch):
    """重载后新服务 instance_id 不同，旧服务可置为 closed 永久失效。"""
    first = make_plugin(tmp_path, monkeypatch)
    second = make_plugin(tmp_path, monkeypatch)
    assert first.get_service().instance_id != second.get_service().instance_id
    first.get_service()._set_state("closed")
    assert first.get_service().get_status()["state"] == "closed"
    assert second.get_service().get_status()["state"] == "initializing"


# ---------- 生命周期 / wait_ready ----------


@pytest.mark.asyncio
async def test_wait_ready_and_initialize(tmp_path, monkeypatch):
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    service = plugin.get_service()
    with pytest.raises(TimeoutError):
        # initialize 之前 state=initializing
        await service.wait_ready(timeout=0.05)
    await plugin.initialize()
    snapshot = await service.wait_ready(timeout=1)
    assert snapshot == service.get_status()
    assert snapshot["ready"] is True and snapshot["reason"] is None


@pytest.mark.asyncio
async def test_wait_ready_after_terminate_closed(tmp_path, monkeypatch):
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    await plugin.initialize()
    await plugin.terminate()
    with pytest.raises(RuntimeError) as ei:
        await plugin.get_service().wait_ready(timeout=1)
    assert getattr(ei.value, "code", "") == "service_closed"
    # 关闭后 get_status 仍可查询
    status = plugin.get_service().get_status()
    assert status["state"] == "closed" and status["ready"] is False


@pytest.mark.asyncio
async def test_ready_not_blocked_by_missing_dependency(tmp_path, monkeypatch):
    """缺 QQ 依赖只影响官机功能，自身 ready 不变 False。"""
    plugin = make_plugin(tmp_path, monkeypatch, context=make_context(None))
    await plugin.initialize()
    status = plugin.get_service().get_status()
    assert status["ready"] is True and status["state"] == "ready"
    dep = status["dependencies"]["qqoffice"]
    assert dep["available"] is False and dep["state"] == "missing"


@pytest.mark.asyncio
async def test_terminate_cancels_timeout_tasks(tmp_path, monkeypatch):
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    await plugin.initialize()
    plugin.timeout_tasks["G"] = asyncio.create_task(asyncio.sleep(30))
    await plugin.terminate()
    assert plugin.timeout_tasks == {}
    assert plugin.group_games == {}


# ---------- 依赖状态：发现层与服务状态分开 ----------


@pytest.mark.asyncio
async def test_dependency_missing(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch, context=make_context(None))
    await plugin.initialize()  # 绑定尝试后记录 missing
    dep = plugin.qqoffice_dependency_status()
    assert dep["available"] is False
    assert dep["state"] == "missing"
    assert dep["activated"] is None and dep["has_instance"] is None


def test_dependency_disabled(tmp_path, monkeypatch):
    svc = FakeQQOfficeService()
    context = SimpleNamespace(
        get_registered_star=lambda name: meta_of(svc, activated=False)
    )
    plugin = make_plugin(tmp_path, monkeypatch, context=context)
    assert plugin._try_bind_qqoffice() is False  # 绑定尝试记录 disabled
    dep = plugin.qqoffice_dependency_status()
    assert dep["available"] is False and dep["state"] == "disabled"
    assert dep["activated"] is False and dep["version"] == "0.3.0"


def test_dependency_no_instance(tmp_path, monkeypatch):
    """已启用但尚无插件实例（star_cls=None）：与禁用/不支持接口明确区分。"""
    context = SimpleNamespace(
        get_registered_star=lambda name: SimpleNamespace(
            name=QQOFFICE_PLUGIN, activated=True, star_cls=None, version="0.3.0"
        )
    )
    plugin = make_plugin(tmp_path, monkeypatch, context=context)
    assert plugin._try_bind_qqoffice() is False  # 绑定尝试记录 no_instance
    dep = plugin.qqoffice_dependency_status()
    assert dep["available"] is False and dep["state"] == "no_instance"
    assert dep["activated"] is True
    assert dep["has_instance"] is False  # star_cls 为 None
    assert dep["api_supported"] is None  # 无实例时无法判定接口支持


def test_dependency_unsupported_api(tmp_path, monkeypatch):
    """旧版提供者没有 get_service：提示升级而非复用内部接口。"""

    class LegacyProvider:
        ready = True  # 旧版布尔标志不得作为绑定依据

    context = SimpleNamespace(
        get_registered_star=lambda name: SimpleNamespace(
            name=QQOFFICE_PLUGIN,
            activated=True,
            star_cls=LegacyProvider(),
            version="0.2.0",
        )
    )
    plugin = make_plugin(tmp_path, monkeypatch, context=context)
    assert plugin._try_bind_qqoffice() is False
    assert plugin.qq_svc is None
    dep = plugin.qqoffice_dependency_status()
    assert dep["state"] == "unsupported_api"
    assert dep["available"] is False and dep["api_supported"] is False
    assert dep["version"] == "0.2.0"


def test_dependency_unsupported_version(tmp_path, monkeypatch):
    """提供者 get_service 拒绝 v1（版本不兼容）：明确原因码。"""

    class FutureProvider:
        def get_service(self, api_version=1):
            err = RuntimeError("only api_version=2")
            err.code = "unsupported_version"
            raise err

    context = SimpleNamespace(
        get_registered_star=lambda name: SimpleNamespace(
            name=QQOFFICE_PLUGIN,
            activated=True,
            star_cls=FutureProvider(),
            version="9.9.0",
        )
    )
    plugin = make_plugin(tmp_path, monkeypatch, context=context)
    assert plugin._try_bind_qqoffice() is False
    dep = plugin.qqoffice_dependency_status()
    assert dep["state"] == "unsupported_version"
    assert dep["available"] is False


def test_dependency_bound_ready_vs_unready(tmp_path, monkeypatch):
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    dep = plugin.qqoffice_dependency_status()
    assert dep["available"] is True and dep["state"] == "bound"
    assert dep["reason"] is None and dep["ready"] is True
    assert dep["version"] == "0.3.0"

    svc.state = "initializing"  # 提供者未就绪
    dep = plugin.qqoffice_dependency_status()
    assert dep["available"] is False and dep["state"] == "unready"
    assert dep["reason"] == "provider_not_ready" and dep["ready"] is False


# ---------- 加载顺序 / 卸载重载（幂等） ----------


@pytest.mark.asyncio
async def test_consumer_loads_first_then_provider(tmp_path, monkeypatch):
    """消费者先加载：绑定失败仅记录 missing；提供者加载广播后补绑。"""
    svc = FakeQQOfficeService()
    context = make_context(None)  # 注册表尚无提供者
    plugin = make_plugin(tmp_path, monkeypatch, context=context)
    await plugin.initialize()
    assert plugin.qq_svc is None
    assert (
        plugin.get_service().get_status()["dependencies"]["qqoffice"]["state"]
        == "missing"
    )

    # 提供者随后加载完成 → on_plugin_loaded 广播
    context.get_registered_star = lambda name: meta_of(svc)
    await plugin._on_expand_loaded(SimpleNamespace(name=QQOFFICE_PLUGIN))
    assert plugin.qq_svc is svc
    assert svc._subs["INTERACTION_CREATE"]  # 已订阅按钮回调
    dep = plugin.get_service().get_status()["dependencies"]["qqoffice"]
    assert dep["available"] is True and dep["state"] == "bound"


@pytest.mark.asyncio
async def test_provider_unload_rebind_cycle(tmp_path, monkeypatch):
    """提供者卸载→重载：幂等解绑、重绑到新服务对象。"""
    old_svc = FakeQQOfficeService()
    context = make_context(old_svc)
    plugin = make_plugin(tmp_path, monkeypatch, context=context)
    assert plugin._try_bind_qqoffice() is True
    assert plugin.qq_svc is old_svc

    await plugin._on_expand_unloaded(SimpleNamespace(name=QQOFFICE_PLUGIN))
    assert plugin.qq_svc is None
    assert old_svc._subs["INTERACTION_CREATE"] == []
    # 幂等：重复卸载广播不再动作
    await plugin._on_expand_unloaded(SimpleNamespace(name=QQOFFICE_PLUGIN))
    assert plugin.qq_svc is None

    new_svc = FakeQQOfficeService(appid="APP-9")
    context.get_registered_star = lambda name: meta_of(new_svc)
    await plugin._on_expand_loaded(SimpleNamespace(name=QQOFFICE_PLUGIN))
    assert plugin.qq_svc is new_svc
    # 幂等：重复加载广播不重复绑定
    await plugin._on_expand_loaded(SimpleNamespace(name=QQOFFICE_PLUGIN))
    assert plugin.qq_svc is new_svc
    assert len(new_svc._subs["INTERACTION_CREATE"]) == 1


@pytest.mark.asyncio
async def test_hooks_ignore_other_plugins(tmp_path, monkeypatch):
    plugin, svc = make_bound_plugin(tmp_path, monkeypatch)
    await plugin._on_expand_unloaded(SimpleNamespace(name="another_plugin"))
    assert plugin.qq_svc is not None  # 不受无关插件卸载影响
