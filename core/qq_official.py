"""QQ 官方机器人平台支持：平台判断、按钮协议/键盘构造、回调事件替身。"""

from __future__ import annotations

from types import SimpleNamespace

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
_QQ_ACTIONS = ("shoot", "status", "load")


def is_qq_official_event(event) -> bool:
    """按 AstrBot 平台类型判断事件是否来自 QQ 官方机器人平台。"""
    name = event.get_platform_name()
    if ADAPTER_NAME_2_TYPE is not None:
        return ADAPTER_NAME_2_TYPE.get(name) in _QQ_OFFICIAL_TYPES
    return name in _QQ_OFFICIAL_NAMES


def _make_button(btn_id: str, label: str, visited: str, style: int, data: str) -> dict:
    """构造单个回调按钮（点击触发 INTERACTION_CREATE）。"""
    return {
        "id": btn_id,
        "render_data": {"label": label, "visited_label": visited, "style": style},
        "action": {
            "type": 1,  # 回调按钮，触发 INTERACTION_CREATE
            "permission": {"type": 2},  # 所有人可点
            "data": data,
            "unsupport_tips": "当前客户端不支持按钮，请使用文字指令",
        },
    }


def build_game_keyboard(group_openid: str) -> dict:
    """构造游戏操作键盘：开枪 / 状态两个回调按钮。"""
    return {
        "content": {
            "rows": [
                {
                    "buttons": [
                        _make_button(
                            "rg2_shoot",
                            "🔫 开枪",
                            "💥 已开枪",
                            1,
                            f"{QQ_INTERACTION_PREFIX}shoot:{group_openid}",
                        ),
                        _make_button(
                            "rg2_status",
                            "📊 状态",
                            "📊 状态",
                            0,
                            f"{QQ_INTERACTION_PREFIX}status:{group_openid}",
                        ),
                    ]
                }
            ]
        }
    }


def build_start_keyboard(group_openid: str) -> dict:
    """构造快速开始键盘：开始游戏（随机装填）按钮。"""
    return {
        "content": {
            "rows": [
                {
                    "buttons": [
                        _make_button(
                            "rg2_load",
                            "🎮 开始游戏",
                            "🎮 已开始",
                            1,
                            f"{QQ_INTERACTION_PREFIX}load:{group_openid}",
                        )
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
        self,
        bot,
        group_openid: str,
        member_openid: str,
        platform_name: str = "qq_official",
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
