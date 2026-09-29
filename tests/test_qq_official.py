from astrbot_plugin_rg2.core.qq_official import (
    QQInteractionShim,
    build_game_keyboard,
    build_start_keyboard,
    is_qq_official_event,
    parse_interaction,
)


class FakeEvent:
    def __init__(self, platform_name):
        self._platform_name = platform_name

    def get_platform_name(self):
        return self._platform_name


def test_is_qq_official_event():
    assert is_qq_official_event(FakeEvent("qq_official"))
    assert is_qq_official_event(FakeEvent("qq_official_webhook"))
    assert not is_qq_official_event(FakeEvent("aiocqhttp"))
    assert not is_qq_official_event(FakeEvent("unknown_platform"))


def test_build_game_keyboard():
    kb = build_game_keyboard("GID")
    buttons = kb["content"]["rows"][0]["buttons"]
    datas = {b["action"]["data"] for b in buttons}
    assert datas == {"rg2:shoot:GID", "rg2:status:GID"}
    assert all(b["action"]["type"] == 1 for b in buttons)


def test_parse_interaction():
    assert parse_interaction("rg2:shoot:GID") == ("shoot", "GID")
    assert parse_interaction("rg2:status:GID") == ("status", "GID")
    assert parse_interaction("rg2:load:GID") == ("load", "GID")
    assert parse_interaction("rg2:unknown:GID") is None
    assert parse_interaction("other:shoot:GID") is None
    assert parse_interaction("") is None
    assert parse_interaction(None) is None


def test_build_start_keyboard():
    kb = build_start_keyboard("GID")
    buttons = kb["content"]["rows"][0]["buttons"]
    assert len(buttons) == 1
    assert buttons[0]["action"]["data"] == "rg2:load:GID"
    assert buttons[0]["action"]["type"] == 1


def test_shim():
    shim = QQInteractionShim(None, "GID", "MID")
    assert shim.message_obj.group_id == "GID"
    assert shim.bot is None
    assert shim.get_sender_id() == "MID"
    assert shim.get_sender_name() == "玩家"
    assert shim.is_admin() is False
    assert shim.get_platform_name() == "qq_official"
    assert shim.unified_msg_origin == "qq_official:GroupMessage:GID"
    assert shim.qq_view is None  # 不携带视图时不得用于发送


def test_shim_carries_bound_view():
    """回调替身只读携带原始事件绑定的视图，不构造发送能力。"""
    sentinel = object()
    shim = QQInteractionShim(None, "GID", "MID", qq_view=sentinel)
    assert shim.qq_view is sentinel
    assert not hasattr(shim, "send_rich")
    assert not hasattr(shim, "group")
