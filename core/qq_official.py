"""QQ 官方机器人平台支持：禁言、消息/卡片发送、按钮回调解析。"""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from typing import Any

from botpy.http import Route

# 按 AstrBot 平台类型判断 QQ 官机（websocket 与 webhook 两种接入）
try:
    from astrbot.core.star.filter.platform_adapter_type import (
        ADAPTER_NAME_2_TYPE,
        PlatformAdapterType,
    )

    _QQ_OFFICIAL_TYPES = (
        PlatformAdapterType.QQOFFICIAL,
        PlatformAdapterType.QQOFFICIAL_WEBHOOK,
    )
except ImportError:  # 兼容旧版本 AstrBot
    ADAPTER_NAME_2_TYPE = None
    _QQ_OFFICIAL_TYPES = ()

_QQ_OFFICIAL_NAMES = ("qq_official", "qq_official_webhook")

QQ_INTERACTION_PREFIX = "rg2:"
_QQ_ACTIONS = ("shoot", "status")


def is_qq_official_event(event) -> bool:
    """按 AstrBot 平台类型判断事件是否来自 QQ 官方机器人平台。"""
    name = event.get_platform_name()
    if ADAPTER_NAME_2_TYPE is not None:
        return ADAPTER_NAME_2_TYPE.get(name) in _QQ_OFFICIAL_TYPES
    return name in _QQ_OFFICIAL_NAMES


def get_qq_bot_client(context) -> Any | None:
    """从 platform_manager 获取 QQ 官机的 botpy client（websocket/webhook 均可）。"""
    try:
        from astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter import (
            QQOfficialPlatformAdapter,
        )
        from astrbot.core.platform.sources.qqofficial_webhook.qo_webhook_adapter import (
            QQOfficialWebhookAdapter,
        )
    except ImportError:
        return None
    for platform in context.platform_manager.get_insts():
        if isinstance(platform, (QQOfficialPlatformAdapter, QQOfficialWebhookAdapter)):
            return platform.get_client()
    return None


async def ban_member(api, group_openid: str, member_openid: str, seconds: int) -> None:
    """禁言群成员，调 POST /v2/groups/{group_openid}/restrict_chat_setting。"""
    expire = (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(seconds=seconds)
    ).isoformat(timespec="seconds")
    payload = {
        "members": [
            {"op": "add", "member_openid": member_openid, "mute_expire_at": expire}
        ]
    }
    await api._http.request(
        Route(
            "POST",
            "/v2/groups/{group_openid}/restrict_chat_setting",
            group_openid=group_openid,
        ),
        json=payload,
    )


async def send_text(
    api,
    group_openid: str,
    content: str,
    *,
    msg_id: str | None = None,
    event_id: str | None = None,
    msg_seq: int = 1,
) -> None:
    """发送群聊纯文本消息（msg_type=0）。"""
    await api.post_group_message(
        group_openid=group_openid,
        msg_type=0,
        content=content,
        msg_id=msg_id,
        msg_seq=msg_seq,
        event_id=event_id,
    )


async def send_card(
    api,
    group_openid: str,
    markdown_content: str,
    keyboard: dict,
    *,
    msg_id: str | None = None,
    event_id: str | None = None,
    msg_seq: int = 1,
) -> None:
    """发送 Markdown 卡片消息（msg_type=2），可挂内嵌键盘。"""
    await api.post_group_message(
        group_openid=group_openid,
        msg_type=2,
        markdown={"content": markdown_content},
        keyboard=keyboard,
        msg_id=msg_id,
        msg_seq=msg_seq,
        event_id=event_id,
    )


def build_game_keyboard(group_openid: str) -> dict:
    """构造游戏操作键盘：开枪 / 状态两个回调按钮。"""

    def _button(btn_id: str, label: str, visited: str, style: int, action: str) -> dict:
        return {
            "id": btn_id,
            "render_data": {"label": label, "visited_label": visited, "style": style},
            "action": {
                "type": 1,  # 回调按钮，触发 INTERACTION_CREATE
                "permission": {"type": 2},  # 所有人可点
                "data": f"{QQ_INTERACTION_PREFIX}{action}:{group_openid}",
                "unsupport_tips": "当前客户端不支持按钮，请使用文字指令",
            },
        }

    return {
        "content": {
            "rows": [
                {
                    "buttons": [
                        _button("rg2_shoot", "🔫 开枪", "💥 已开枪", 1, "shoot"),
                        _button("rg2_status", "📊 状态", "📊 状态", 0, "status"),
                    ]
                }
            ]
        }
    }


def parse_interaction(button_data: str | None) -> tuple[str, str] | None:
    """解析按钮回调数据 'rg2:<action>:<group_openid>'。"""
    if not button_data or not button_data.startswith(QQ_INTERACTION_PREFIX):
        return None
    parts = button_data.split(":", 2)
    if len(parts) != 3 or parts[1] not in _QQ_ACTIONS or not parts[2]:
        return None
    return parts[1], parts[2]


class QQInteractionShim:
    """按钮回调场景的最小 event 替身，供游戏逻辑复用。"""

    def __init__(
        self, bot, group_openid: str, member_openid: str, platform_name: str = "qq_official"
    ):
        self.bot = bot
        self.message_obj = SimpleNamespace(group_id=group_openid)
        self.unified_msg_origin = f"{platform_name}:GroupMessage:{group_openid}"
        self._member_openid = member_openid
        self._platform_name = platform_name

    def get_sender_id(self) -> str:
        return self._member_openid

    def get_sender_name(self) -> str:
        return "玩家"  # 回调事件不带昵称

    def is_admin(self) -> bool:
        return False

    def get_platform_name(self) -> str:
        return self._platform_name
