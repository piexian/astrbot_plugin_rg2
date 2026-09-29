"""qqoffice_expand SDK v1 公开门面与绑定视图的测试替身。

形状对齐提供者真实公开面（不虚构根服务 send_rich/group）：

- 根服务：get_status / capabilities / wait_ready / instance(platform_id) /
  for_event(event) / on / on_any / md / kb / btn / reference / ref_from_event；
- 绑定视图：send_rich（自动携带事件目标）/ group.send / group.recall /
  group.mute_member / prefix() / platform_id；
- 路由语义：instance 视图跟随同身份重载、改绑其他 AppID 拒绝；
  for_event 扩展事件按来源代次固定（代次变化即失败）；原生事件校验
  event.bot 仍是当前 client。
"""

from __future__ import annotations

import asyncio
import uuid

SDK_API_VERSION = 1

EVENT_ID_SCOPES = {
    "group": {"INTERACTION_CREATE", "GROUP_ADD_ROBOT", "GROUP_MSG_RECEIVE"},
    "c2c": {"INTERACTION_CREATE", "C2C_MSG_RECEIVE", "FRIEND_ADD"},
}


class InstanceUnavailable(RuntimeError):
    """平台实例不在运行索引中。"""


class InstanceIdentityChanged(RuntimeError):
    """实例已改绑其他 AppID，旧视图不能转给新机器人。"""


class StaleSourceEvent(RuntimeError):
    """事件来源代次/client 已失效。"""


class FakeRoute:
    """单个平台实例的当前路由（模拟本体索引收敛）。"""

    def __init__(self, platform_id: str, appid: str):
        self.platform_id = platform_id
        self.appid = appid
        self.client = object()  # 原生事件来源核验用对象身份
        self.generation = 1

    def rebind(self, appid: str) -> None:
        """改绑 AppID：身份变化，代次递增（模拟适配器重建）。"""
        self.appid = appid
        self.client = object()
        self.generation += 1

    def reload(self) -> None:
        """同身份重载：AppID 不变，代次递增。"""
        self.client = object()
        self.generation += 1


class FakeSource:
    """扩展事件不可变来源（挂载时绑定）。"""

    def __init__(self, route: FakeRoute, generation: int | None = None):
        self.platform_id = route.platform_id
        self.appid = route.appid
        self.generation = route.generation if generation is None else generation


class FakeQQOfficeEvent:
    """扩展事件（INTERACTION_CREATE 等）替身。"""

    def __init__(
        self,
        route: FakeRoute,
        *,
        type: str = "INTERACTION_CREATE",
        group_openid: str | None = None,
        member_openid: str | None = None,
        payload_id: str | None = None,
        raw: dict | None = None,
        source_generation: int | None = None,
        source_appid: str | None = None,
    ):
        self.type = type
        self.name = type.lower()
        self.raw = raw if raw is not None else {}
        self.payload_id = payload_id
        self.scene = "group" if group_openid else None
        self.group_openid = group_openid
        self.user_openid = None
        self.member_openid = member_openid
        self.message_id = None
        self.source = FakeSource(route, source_generation)
        if source_appid is not None:
            self.source.appid = source_appid

    @property
    def is_interaction(self) -> bool:
        return self.type == "INTERACTION_CREATE"


class FakeNativeEvent:
    """原生 AstrMessageEvent 最小替身（普通群消息）。"""

    def __init__(self, route: FakeRoute, group_id: str, *, bot=None, sender="M1"):
        self._platform_name = route.platform_id
        self._platform_id = route.platform_id
        self.bot = route.client if bot is None else bot
        self.message_obj = type(
            "MO",
            (),
            {"group_id": group_id, "message_id": "NMSG1", "raw_message": None},
        )()
        self.sender_id = sender

    def get_platform_name(self) -> str:
        return self._platform_name

    def get_platform_id(self) -> str:
        return self._platform_id

    def get_sender_id(self) -> str:
        return self.sender_id

    def get_sender_name(self) -> str:
        return "测试员"

    def is_admin(self) -> bool:
        return False

    def stop_event(self) -> None:
        self.stopped = True


def _resolve_native_target(event) -> tuple[str | None, str | None, str | None]:
    group_id = getattr(event.message_obj, "group_id", None)
    if group_id:
        return "group", str(group_id), getattr(event.message_obj, "message_id", None)
    return None, None, None


class FakeGroupAPI:
    """群命名空间：记录调用并返回官方形状响应。"""

    def __init__(self, view: "FakeView"):
        self._view = view

    async def send(
        self, openid: str, content: str, *, msg_id=None, event_id=None, **kw
    ):
        self._view._check_current()
        return self._view._record(
            "group.send",
            scene="group",
            target=openid,
            content=content,
            msg_id=msg_id,
            event_id=event_id,
        )

    async def recall(self, openid: str, message_id: str, **kw):
        self._view._check_current()
        return self._view._record(
            "group.recall", scene="group", target=openid, message_id=message_id
        )

    async def mute_member(self, openid: str, member_openid: str, expire_at: str):
        self._view._check_current()
        return self._view._record(
            "group.mute_member",
            scene="group",
            target=openid,
            member_openid=member_openid,
            expire_at=expire_at,
        )


class FakeView:
    """绑定来源视图替身（对齐 BoundView 的事件目标自动填充语义）。"""

    def __init__(
        self, svc: "FakeQQOfficeService", route: FakeRoute, *, source_client=None
    ):
        self._svc = svc
        self._route = route
        self._appid = route.appid
        self.platform_id = route.platform_id
        self._source_client = source_client
        # 扩展事件视图按创建时代次固定（pin_generation）；原生视图核验
        # source_client；instance 长期视图两者皆为 None（跟随同身份重载）
        self._source_generation: int | None = None
        self._target: tuple[str | None, str | None, str | None] = (None, None, None)
        self._event_id: str | None = None
        self._event_id_source: str | None = None
        self.group = FakeGroupAPI(self)

    # -- 来源固定 --

    def pin_generation(self, generation: int) -> None:
        self._source_generation = generation

    def _check_current(self) -> None:
        route = self._svc._route(self.platform_id)
        if route.appid != self._appid:
            raise InstanceIdentityChanged(
                f"{self.platform_id!r} 已从 {self._appid} 改绑到 {route.appid}"
            )
        if (
            self._source_generation is not None
            and self._source_generation != route.generation
        ):
            raise StaleSourceEvent(
                f"来源代次 {self._source_generation} 已失效（当前 {route.generation}）"
            )
        if self._source_client is not None and self._source_client is not route.client:
            raise StaleSourceEvent("事件来源 client 已不是当前实例")

    def prefix(self) -> str:
        self._check_current()
        return self._appid

    # -- 事件目标 --

    def set_event_target(
        self,
        scene: str | None,
        openid: str | None,
        msg_id: str | None,
        event_id: str | None = None,
        event_id_source: str | None = None,
    ) -> None:
        self._target = (scene, openid, msg_id)
        self._event_id = event_id
        self._event_id_source = event_id_source

    # -- 发送 --

    async def send_rich(
        self,
        *,
        content: str | None = None,
        markdown: dict | None = None,
        keyboard: dict | None = None,
        msg_id: str | None = None,
        event_id: str | None = None,
        event_id_source: str | None = None,
        msg_seq: int | None = None,
        scene: str | None = None,
        target_openid: str | None = None,
        **extra,
    ) -> dict:
        self._check_current()
        if isinstance(self._svc.fail_send_rich, Exception):
            raise self._svc.fail_send_rich
        if self._svc.fail_send_rich:
            raise RuntimeError("send_rich 注入失败")
        ev_scene, ev_openid, ev_msg_id = self._target
        if scene is None and target_openid is None and ev_scene:
            scene, target_openid, msg_id = ev_scene, ev_openid, (msg_id or ev_msg_id)
            if event_id is None:
                event_id = self._event_id
            if event_id_source is None:
                event_id_source = self._event_id_source
        if scene is None or target_openid is None:
            raise InstanceUnavailable("send_rich 需要 scene+target_openid（无来源）")
        return self._record(
            "send_rich",
            scene=scene,
            target=target_openid,
            content=content,
            markdown=markdown,
            keyboard=keyboard,
            msg_id=msg_id,
            event_id=event_id,
            event_id_source=event_id_source,
        )

    def _record(self, kind: str, **fields) -> dict:
        record = {"kind": kind, "appid": self._appid, **fields}
        self._svc.calls.append(record)
        self._svc.call_seq += 1
        record["id"] = f"MSG{self._svc.call_seq}"
        return record


class FakeProviderStar:
    """提供者 star_cls 替身：仅暴露 get_service（用于发现层测试）。"""

    def __init__(self, service: "FakeQQOfficeService"):
        self._service = service

    def get_service(self, api_version: int = 1):
        if isinstance(api_version, bool) or api_version != 1:
            err = RuntimeError(f"unsupported api_version {api_version!r}")
            err.code = "unsupported_version"
            raise err
        return self._service


class FakeQQOfficeService:
    """SDK v1 公开门面替身：路由 + 订阅 + 视图创建。"""

    def __init__(self, platform_id: str = "qq_official", appid: str = "APP-1"):
        self.instance_id = f"qqoffice-{uuid.uuid4().hex[:8]}"
        self.state = "ready"
        self.calls: list[dict] = []
        self.call_seq = 0
        self.fail_send_rich = False  # 置 True 时所有视图 send_rich 抑制失败
        self.routes: dict[str, FakeRoute] = {platform_id: FakeRoute(platform_id, appid)}
        self._subs: dict[str, list] = {}
        self._any_subs: list = []
        self.md = lambda content: {"markdown": {"content": content}}
        self.kb = None
        self.btn = None
        self.reference = None

    # ---- SDK v1 必需接口 ----

    def get_status(self) -> dict:
        return {
            "api_version": SDK_API_VERSION,
            "instance_id": self.instance_id,
            "state": self.state,
            "ready": self.state == "ready",
            "reason": {
                "closing": "service_closed",
                "closed": "service_closed",
                "unavailable": "initialize_failed",
            }.get(self.state),
        }

    def capabilities(self) -> dict:
        return {"api_version": 1, "features": ["qq.instance", "qq.events"]}

    async def wait_ready(self, timeout=None) -> dict:
        if self.state in ("closing", "closed"):
            err = RuntimeError("service closed")
            err.code = "service_closed"
            raise err
        return self.get_status()

    # ---- 路由 ----

    def _route(self, platform_id: str) -> FakeRoute:
        route = self.routes.get(platform_id)
        if route is None:
            raise InstanceUnavailable(f"平台实例 {platform_id!r} 不在运行索引中")
        return route

    def add_route(self, platform_id: str, appid: str) -> FakeRoute:
        route = FakeRoute(platform_id, appid)
        self.routes[platform_id] = route
        return route

    def remove_route(self, platform_id: str) -> None:
        self.routes.pop(platform_id, None)

    # ---- 视图 ----

    def instance(self, platform_id: str) -> FakeView:
        return FakeView(self, self._route(platform_id))

    def for_event(self, event) -> FakeView:
        source = getattr(event, "source", None)
        if source is not None:  # 扩展事件：按来源代次固定
            route = self._route(source.platform_id)
            if source.appid != route.appid:
                raise InstanceIdentityChanged("实例已改绑其他 AppID")
            if source.generation != route.generation:
                raise StaleSourceEvent("事件来源代次已失效")
            view = FakeView(self, route)
            view.pin_generation(source.generation)
            view.set_event_target(*_ext_event_target(event))
            return view
        route = self._route(event.get_platform_id())
        bot = getattr(event, "bot", None)
        if bot is not None and bot is not route.client:
            raise StaleSourceEvent("事件来源 client 已不是当前实例")
        view = FakeView(self, route, source_client=bot)
        view.set_event_target(*_resolve_native_target(event))
        return view

    def ref_from_event(self, event):
        return None

    # ---- 订阅 ----

    def on(self, event_type: str, handler):
        self._subs.setdefault(event_type, []).append(handler)

        def unsub():
            try:
                self._subs[event_type].remove(handler)
            except ValueError:
                pass

        return unsub

    def on_any(self, handler):
        self._any_subs.append(handler)

        def unsub():
            try:
                self._any_subs.remove(handler)
            except ValueError:
                pass

        return unsub

    async def dispatch(self, event) -> int:
        """模拟中台事件分发，返回已调用处理器数量。"""
        handlers = list(self._subs.get(event.type, []))
        for handler in handlers:
            await handler(event)
        for handler in list(self._any_subs):
            await handler(event)
        return len(handlers) + len(self._any_subs)


def _ext_event_target(event: FakeQQOfficeEvent) -> tuple:
    scene = event.scene or ("group" if event.group_openid else None)
    openid = event.group_openid or event.user_openid
    msg_id = str(event.message_id) if event.message_id else None
    event_id = None
    event_id_source = None
    if (
        scene in EVENT_ID_SCOPES
        and event.type in EVENT_ID_SCOPES[scene]
        and event.payload_id
    ):
        event_id = str(event.payload_id)
        event_id_source = event.type
    return scene, str(openid) if openid else None, msg_id, event_id, event_id_source


def wait_for(tasks: list[asyncio.Task]) -> None:
    """测试辅助：等待并收集任务异常。"""
    for task in tasks:
        task.result()
