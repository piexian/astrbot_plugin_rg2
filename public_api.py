"""rg2 的 SDK v1 状态与依赖查询门面。"""

from __future__ import annotations

import asyncio
import uuid

__all__ = [
    "SDK_API_VERSION",
    "RevolverGameService",
    "ServiceAPIVersionError",
    "ServiceClosedError",
]

SDK_API_VERSION = 1

_VALID_STATES = ("initializing", "ready", "unavailable", "closing", "closed")

_DEPENDENCY_UNKNOWN = {
    "available": False,
    "state": "unknown",
    "reason": "status_query_failed",
    "activated": None,
    "has_instance": None,
    "api_supported": None,
    "version": None,
    "ready": None,
}


class ServiceAPIVersionError(RuntimeError):
    """请求的 api_version 不受支持（稳定 code：unsupported_version）。"""

    def __init__(self, message: str = "unsupported api_version"):
        super().__init__(message)
        self.code = "unsupported_version"


class ServiceClosedError(RuntimeError):
    """服务已关闭，等待就绪没有意义（稳定 code：service_closed）。"""

    def __init__(self, message: str = "service closed"):
        super().__init__(message)
        self.code = "service_closed"


class RevolverGameService:
    """报告本插件生命周期；QQ 依赖缺失不影响其他平台就绪。"""

    _POLL_INTERVAL = 0.05

    def __init__(self, plugin=None):
        self._plugin = plugin
        self._instance_id = f"rg2-{uuid.uuid4().hex}"
        self._state = "initializing"
        self._reason: str | None = "initializing"

    # ---- 生命周期标记（仅插件自身在 initialize / terminate 中调用） ----

    def _set_state(self, state: str, reason: str | None = None) -> None:
        if state not in _VALID_STATES:
            raise ValueError(f"unknown service state: {state!r}")
        self._state = state
        self._reason = None if state == "ready" else (reason or state)

    # ---- 只读属性 ----

    @property
    def instance_id(self) -> str:
        """本次插件加载生成的非空唯一标识。"""
        return self._instance_id

    @property
    def state(self) -> str:
        return self._state

    # ---- SDK v1 必需接口 ----

    def get_status(self) -> dict:
        """同步本地状态快照；服务关闭后仍可查询。

        ready 只说明 rg2 自身初始化完成，不保证 QQ 上游、余额或目标
        平台可用。dependencies.qqoffice 表达官机中台依赖的可见性。
        """
        dependencies: dict = {}
        plugin = self._plugin
        if plugin is not None:
            try:
                dependencies = {"qqoffice": plugin.qqoffice_dependency_status()}
            except Exception:
                dependencies = {"qqoffice": dict(_DEPENDENCY_UNKNOWN)}
        return {
            "api_version": SDK_API_VERSION,
            "instance_id": self._instance_id,
            "state": self._state,
            "ready": self._state == "ready",
            "reason": None if self._state == "ready" else self._reason,
            "dependencies": dependencies,
        }

    def capabilities(self) -> dict:
        """能力声明：本轮只公开状态，features 为空列表。"""
        return {"api_version": SDK_API_VERSION, "features": []}

    async def wait_ready(self, timeout: float | None = None) -> dict:
        """等待自身初始化完成，成功返回 get_status 同形快照。

        - 超时抛 TimeoutError（调用方应显式设置有界超时）；
        - closing / closed 抛 ServiceClosedError（code=service_closed）；
        - 不等待 QQ 依赖、不触发任何网络请求。
        """
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + max(0.0, float(timeout))
        while True:
            if self._state in ("closing", "closed"):
                raise ServiceClosedError(f"astrbot_plugin_rg2 service is {self._state}")
            if self._state == "ready":
                return self.get_status()
            if deadline is not None and loop.time() >= deadline:
                raise TimeoutError(
                    f"astrbot_plugin_rg2 service not ready within {timeout}s "
                    f"(state={self._state})"
                )
            await asyncio.sleep(self._POLL_INTERVAL)
