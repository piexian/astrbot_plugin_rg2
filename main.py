import asyncio
import datetime
import json
import os
import random

import yaml

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .core.qq_official import (
    QQInteractionShim,
    build_game_keyboard,
    build_start_keyboard,
    is_qq_official_event,
    parse_interaction,
)
from .text_manager import TextManager

# 插件元数据（仅 register 装饰器使用，真实版本号从 metadata.yaml 读取）
PLUGIN_NAME = "astrbot_plugin_rg2"
PLUGIN_AUTHOR = "piexian"
PLUGIN_DESCRIPTION = (
    "一个刺激的群聊轮盘赌游戏插件，支持管理员装填子弹、用户开枪对决、随机走火等功能"
)
PLUGIN_REPO = "https://github.com/piexian/astrbot_plugin_rg2"
_FALLBACK_VERSION = "1.3.5"

QQOFFICE_PLUGIN = "astrbot_plugin_qqoffice_expand"
INSTALL_HINT = (
    "⚠️ 当前平台需安装 astrbot_plugin_qqoffice_expand 插件才能使用本游戏\n"
    "💡 插件市场搜索 qqoffice_expand，或从链接安装: "
    "https://github.com/piexian/astrbot_plugin_qqoffice_expand"
)
# 导入事件类型
try:
    from astrbot.core.star.filter.event_message_type import EventMessageType
except ImportError:
    EventMessageType = None  # 兼容旧版本


def _noop_decorator(*_args, **_kwargs):
    return lambda f: f


# 老版本 AstrBot 无插件加载广播钩子，空装饰器兜底
_on_plugin_loaded = getattr(filter, "on_plugin_loaded", _noop_decorator)
_on_plugin_unloaded = getattr(filter, "on_plugin_unloaded", _noop_decorator)


@register(
    PLUGIN_NAME,
    PLUGIN_AUTHOR,
    PLUGIN_DESCRIPTION,
    _FALLBACK_VERSION,
    PLUGIN_REPO,
)
class RevolverGunPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        """初始化左轮手枪插件

        Args:
            context: AstrBot上下文对象
            config: 插件配置字典
        """
        super().__init__(context)
        self.context = context
        self.config = config or {}

        # 读取插件版本（来自 metadata.yaml，失败回落到 _FALLBACK_VERSION）
        self.plugin_version = self._load_plugin_version()

        # 游戏状态管理（key 统一为群ID字符串）
        self.group_games: dict[str, dict] = {}
        self.group_misfire: dict[str, bool] = {}
        self.timeout_tasks: dict[str, asyncio.Task] = {}

        # AI触发器事件队列
        self.ai_trigger_queue: dict[str, dict] = {}
        self.ai_trigger_counter = 0  # 用于生成一致的ID

        # 数据持久化
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_rg2")
        self.config_file = self.data_dir / "group_misfire.json"

        # 加载持久化配置
        self._load_misfire_config()

        # 初始化文本管理器（构造永远成功，加载错误内部消化）
        self.text_manager = TextManager(
            custom_texts=self.config.get("custom_texts", []) or []
        )
        logger.info(f"文本管理器初始化成功，已加载分类: {self.text_manager.categories}")

        # 配置参数
        self.timeout = self.config.get("timeout_seconds", 120)
        self.misfire_prob = self.config.get("misfire_probability", 0.003)
        self.min_ban = self.config.get("min_ban_seconds", 60)
        self.max_ban = self.config.get("max_ban_seconds", 300)
        self.default_misfire = self.config.get("misfire_enabled_by_default", False)
        self.ai_trigger_delay = self.config.get("ai_trigger_delay", 2)
        self._last_ban_error = ""  # 最近一次禁言失败的原因（供回复文案使用）

        # QQ 官机卡片交互
        self.qq_card_enabled = self.config.get("qq_card_enabled", True)
        self.qq_svc = None  # qqoffice_expand 的 star_cls，None 时官机功能停用
        self._qq_unsub = None  # INTERACTION_CREATE 订阅解绑闭包
        self._last_card_msg = {}  # group_id -> 上一条卡片 message_id

        # 弹膛/装弹相关
        self.max_bullet_count = self.config.get("max_bullet_count", 6)
        self.chamber_count = self.config.get("chamber_count", self.max_bullet_count)
        self.fixed_bullet_count = self.config.get("fixed_bullet_count", 0)
        self.no_full_chamber = self.config.get("no_full_chamber", False)
        self.end_on_full_rotation = self.config.get("end_on_full_rotation", False)
        self.hide_bullet_count = self.config.get("hide_bullet_count", False)

        # 验证配置有效性
        if self.chamber_count < 1:
            raise ValueError(f"chamber_count 必须 >= 1，当前值: {self.chamber_count}")
        if self.max_bullet_count < 1:
            raise ValueError(
                f"max_bullet_count 必须 >= 1，当前值: {self.max_bullet_count}"
            )
        if self.max_bullet_count > self.chamber_count:
            raise ValueError(
                f"max_bullet_count({self.max_bullet_count}) 不能超过 chamber_count({self.chamber_count})"
            )

        # 注册函数工具
        self._register_function_tools()

    def _load_plugin_version(self) -> str:
        """从 metadata.yaml 读取插件版本，失败则回落到默认值"""
        try:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            metadata_path = os.path.join(current_dir, "metadata.yaml")
            if os.path.exists(metadata_path):
                with open(metadata_path, encoding="utf-8") as f:
                    metadata = yaml.safe_load(f) or {}
                version = metadata.get("version", _FALLBACK_VERSION)
                logger.info(f"插件版本从 metadata.yaml 读取: {version}")
                return version
            logger.warning(f"未找到 metadata.yaml，使用默认版本: {_FALLBACK_VERSION}")
        except Exception as e:
            logger.error(f"读取插件版本失败，使用默认版本: {e}")
        return _FALLBACK_VERSION

    def _register_function_tools(self):
        """注册函数工具到AstrBot"""
        try:
            from .tools.revolver_game_tool import RevolverGameTool

            # 初始化统一工具并传递插件实例
            revolver_tool = RevolverGameTool(plugin_instance=self)

            # >= v4.5.1 使用新的注册方式
            if hasattr(self.context, "add_llm_tools"):
                self.context.add_llm_tools(revolver_tool)
            else:
                # < v4.5.1 兼容旧版本
                tool_mgr = self.context.provider_manager.llm_tools
                tool_mgr.func_list.append(revolver_tool)

            logger.info("左轮手枪统一触发器工具注册成功")
        except Exception as e:
            logger.error(f"注册函数工具失败: {e}", exc_info=True)

    # ========== QQ 官机中台接入 ==========

    async def initialize(self):
        """插件初始化：尝试绑定 qqoffice_expand 中台"""
        self._try_bind_qqoffice()

    def _try_bind_qqoffice(self) -> bool:
        """尝试绑定 qqoffice_expand；成功则订阅按钮回调。"""
        if self.qq_svc is not None:
            return True
        try:
            meta = self.context.get_registered_star(QQOFFICE_PLUGIN)
        except Exception as e:
            logger.error(f"查询 {QQOFFICE_PLUGIN} 失败: {e}")
            return False
        if not (
            meta
            and meta.activated
            and meta.star_cls
            and getattr(meta.star_cls, "ready", False)
        ):
            return False
        self.qq_svc = meta.star_cls
        self._qq_unsub = self.qq_svc.on("INTERACTION_CREATE", self._on_qqoffice_event)
        logger.info("已接入 qqoffice_expand（官机卡片/按钮/禁言走中台）")
        return True

    @_on_plugin_loaded()
    async def _on_expand_loaded(self, metadata):
        """中台加载广播：晚于本插件加载时补绑"""
        if self.qq_svc is None and getattr(metadata, "name", "") == QQOFFICE_PLUGIN:
            self._try_bind_qqoffice()

    @_on_plugin_unloaded()
    async def _on_expand_unloaded(self, metadata):
        """中台卸载广播：解绑等待下次加载"""
        if getattr(metadata, "name", "") == QQOFFICE_PLUGIN and self.qq_svc is not None:
            if self._qq_unsub:
                self._qq_unsub()
            self.qq_svc = None
            self._qq_unsub = None
            logger.info("qqoffice_expand 已卸载，官机功能转为安装提示模式")

    def _qq_gate(self, event: AstrMessageEvent) -> str | None:
        """官机平台且中台未绑定时返回安装提示，否则 None。"""
        if self.qq_svc is None and is_qq_official_event(event):
            return INSTALL_HINT
        return None

    async def _on_qqoffice_event(self, ev):
        """处理中台转发的按钮回调（INTERACTION_CREATE）。"""
        if self.qq_svc is None or not getattr(ev, "is_interaction", False):
            return
        try:
            resolved = (ev.raw.get("data") or {}).get("resolved") or {}
            parsed = parse_interaction(resolved.get("button_data"))
            if not parsed:
                return
            action, group_openid = parsed
            # 校验回调与事件来自同一群
            if ev.group_openid and group_openid != ev.group_openid:
                logger.warning(
                    f"按钮回调群不一致: 数据={group_openid} 事件={ev.group_openid}，忽略"
                )
                return
            member_openid = ev.member_openid or "unknown"
            shim = QQInteractionShim(None, group_openid, member_openid)
            if action == "load":
                # 快速开始：随机装填（与 /装填 无参一致，所有用户可用）
                msgs = await self._do_load_game(shim, group_openid, "玩家", None)
            elif action == "shoot":
                self._init_group(group_openid)
                msgs = await self._do_shoot_game(
                    shim, group_openid, "玩家", member_openid
                )
            else:
                msgs = [self._do_status(group_openid)]
            content = "\n".join(msgs)
            if self.qq_card_enabled:
                # 按钮点击后不可复原，每次交互都补发带新按钮的卡片；
                # 键盘随游戏状态切换（进行中=开枪/状态，未开始=快速开始）
                keyboard = (
                    build_game_keyboard(group_openid)
                    if group_openid in self.group_games
                    else build_start_keyboard(group_openid)
                )
                resp = await self._send_card(
                    group_openid, content, keyboard, event_id=ev.payload_id
                )
                if resp is None:
                    await self.qq_svc.group.send(
                        group_openid, content, event_id=ev.payload_id
                    )
            else:
                await self.qq_svc.group.send(
                    group_openid, content, event_id=ev.payload_id
                )
        except Exception as e:
            logger.error(f"按钮回调处理失败: {e}", exc_info=True)

    async def _send_card(
        self,
        group_id: str,
        content: str,
        keyboard: dict,
        *,
        event: AstrMessageEvent | None = None,
        event_id: str | None = None,
    ) -> dict | None:
        """官机卡片统一发送：成功后撤回上一条卡片并记录新 id；失败返回 None 由调用方降级。"""
        try:
            kwargs = {
                "markdown": {"markdown": {"content": content}},
                "keyboard": {"keyboard": keyboard},
            }
            if event is not None:
                resp = await self.qq_svc.send_rich(event, **kwargs)
            else:
                resp = await self.qq_svc.send_rich(
                    scene="group",
                    target_openid=group_id,
                    event_id=event_id,
                    event_id_source="INTERACTION_CREATE" if event_id else None,
                    **kwargs,
                )
        except Exception as e:
            logger.error(f"发送卡片失败: {e}")
            return None
        prev_id = self._last_card_msg.get(group_id)
        new_id = resp.get("id") if isinstance(resp, dict) else None
        if prev_id and prev_id != new_id:
            try:
                await self.qq_svc.group.recall(group_id, prev_id)
            except Exception as e:
                logger.debug(f"撤回旧卡片失败（可能超2分钟窗口）: {e}")
        if new_id:
            self._last_card_msg[group_id] = new_id
        return resp

    async def _reply_load_result(
        self, event: AstrMessageEvent, group_id: str, msgs: list[str]
    ) -> bool:
        """QQ 官机且开启卡片时直接发卡片，返回 True；否则返回 False 走原文本逻辑"""
        if not (self.qq_card_enabled and self.qq_svc and is_qq_official_event(event)):
            return False
        resp = await self._send_card(
            group_id, "\n".join(msgs), build_game_keyboard(group_id), event=event
        )
        if resp is None:
            return False
        # 卡片已通过中台直发，标记事件已消费，防止继续流入 LLM
        event.stop_event()
        return True

    async def _reply_status_result(
        self, event: AstrMessageEvent, group_id: str, text: str
    ) -> bool:
        """QQ 官机且开启卡片时状态回复渲染为卡片，返回 True；否则返回 False"""
        if not (self.qq_card_enabled and self.qq_svc and is_qq_official_event(event)):
            return False
        # 键盘随游戏状态切换：进行中=开枪/状态，未开始=快速开始
        keyboard = (
            build_game_keyboard(group_id)
            if group_id in self.group_games
            else build_start_keyboard(group_id)
        )
        resp = await self._send_card(group_id, text, keyboard, event=event)
        if resp is None:
            return False
        # 卡片已通过中台直发，标记事件已消费，防止继续流入 LLM
        event.stop_event()
        return True

    def _get_group_id(self, event: AstrMessageEvent) -> str | None:
        """获取群ID（统一返回字符串；QQ 官机为 group_openid）

        Args:
            event: 消息事件对象

        Returns:
            群ID字符串，如果不在群聊中返回None
        """
        # 首先尝试从 message_obj 获取（普通消息）
        group_id = getattr(event.message_obj, "group_id", None)
        if group_id:
            return str(group_id)

        # 如果失败，尝试从 unified_msg_origin 解析（LLM工具调用）
        try:
            origin = getattr(event, "unified_msg_origin", "")
            # 格式: platform_name:GroupMessage:group_id（兼容旧格式 :group:）
            if origin and (":GroupMessage:" in origin or ":group:" in origin):
                parts = origin.split(":")
                if len(parts) >= 3:
                    return parts[2]
        except (ValueError, AttributeError):
            pass

        return None

    def _get_user_name(self, event: AstrMessageEvent) -> str:
        """获取用户昵称

        Args:
            event: 消息事件对象

        Returns:
            用户昵称，如果获取失败返回"玩家"
        """
        return event.get_sender_name() or "玩家"

    async def _get_group_role(self, event: AstrMessageEvent, user_id: str) -> str:
        """查询用户在群内的角色

        Returns:
            ``"owner"`` / ``"admin"`` / ``"member"``；获取失败返回 ""。
        """
        try:
            group_id = self._get_group_id(event)
            if not group_id:
                return ""
            # QQ 官机：群消息原始 payload 的 author.member_role 直接带角色
            if is_qq_official_event(event):
                raw = getattr(event.message_obj, "raw_message", None)
                raw_data = getattr(raw, "raw_data", {}) if raw else {}
                if isinstance(raw_data, dict):
                    author = raw_data.get("author", {})
                    if isinstance(author, dict):
                        return str(author.get("member_role") or "").lower()
                return ""
            if not hasattr(event.bot, "get_group_member_info"):
                return ""
            member_info = await event.bot.get_group_member_info(
                group_id=int(group_id), user_id=int(user_id), no_cache=True
            )
            if isinstance(member_info, dict):
                return str(member_info.get("role", "") or "")
            return str(getattr(member_info, "role", "") or "")
        except Exception as e:
            logger.error(f"获取群角色失败: {e}")
            return ""

    async def _is_group_admin(self, event: AstrMessageEvent) -> bool:
        """检查发送者是否是群主/管理员或 Bot 超管"""
        try:
            if event.is_admin():
                return True
            return await self._get_group_role(event, event.get_sender_id()) in (
                "owner",
                "admin",
            )
        except Exception as e:
            logger.error(f"检查群管理员权限失败: {e}")
            return False

    def _init_group(self, group_id: str):
        """初始化群状态

        Args:
            group_id: 群ID
        """
        if group_id not in self.group_misfire:
            self.group_misfire[group_id] = self.default_misfire

    def _load_misfire_config(self):
        """加载走火配置

        key 统一归一为字符串（兼容历史 int key 与 QQ 官机 openid）。
        """
        try:
            if self.config_file.exists():
                with open(self.config_file, encoding="utf-8") as f:
                    data = json.load(f)
                # 兼容历史 str / int key，统一转 str
                normalized = {}
                for k, v in data.items():
                    try:
                        normalized[str(k)] = bool(v)
                    except (TypeError, ValueError):
                        continue
                self.group_misfire.update(normalized)
                logger.info(f"已加载 {len(normalized)} 个群的走火配置")
            else:
                logger.info("未找到走火配置文件，使用默认配置")
        except Exception as e:
            logger.error(f"加载走火配置失败: {e}")

    def _save_misfire_config(self):
        """保存走火配置

        JSON 仅支持字符串 key，序列化时显式转 str 以保证语义稳定。
        """
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            serializable = {str(k): bool(v) for k, v in self.group_misfire.items()}
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(serializable, f, ensure_ascii=False, indent=2)
            logger.debug(f"已保存 {len(serializable)} 个群的走火配置")
        except Exception as e:
            logger.error(f"保存走火配置失败: {e}")

    def _create_chambers(self, bullet_count: int) -> list[bool]:
        """创建弹膛状态

        Args:
            bullet_count: 子弹数量

        Returns:
            弹膛状态列表，True表示有子弹
        """
        chambers = [False] * self.chamber_count
        if bullet_count > 0:
            positions = random.sample(range(self.chamber_count), bullet_count)
            for pos in positions:
                chambers[pos] = True
        return chambers

    def _get_random_bullet_count(self) -> int:
        """获取随机子弹数量

        根据配置决定随机范围：
        - 如果设置了固定数量，返回固定值
        - 如果开启了禁止满膛，最大值为 max_bullet_count - 1
        - 否则范围为 1 到 max_bullet_count

        Returns:
            子弹数量
        """
        # 如果设置了固定装弹数量
        if self.fixed_bullet_count > 0:
            return min(self.fixed_bullet_count, self.max_bullet_count)

        # 计算随机范围
        max_count = self.max_bullet_count
        if self.no_full_chamber and max_count > 1:
            max_count -= 1

        return random.randint(1, max_count)

    def _parse_bullet_count(self, message: str) -> int | None:
        """解析子弹数量

        Args:
            message: 用户输入的消息

        Returns:
            解析出的子弹数量，如果解析失败返回None
        """
        parts = message.strip().split()
        if len(parts) < 2:
            return None

        try:
            count = int(parts[1])
            max_allowed = (
                self.chamber_count - 1 if self.no_full_chamber else self.chamber_count
            )
            if 1 <= count <= max_allowed:
                return count
        except (ValueError, IndexError):
            pass
        return None

    def _check_misfire(self, group_id: str) -> bool:
        """检查是否触发随机走火

        Args:
            group_id: 群ID

        Returns:
            是否触发走火
        """
        if not self.group_misfire.get(group_id, False):
            return False
        return random.random() < self.misfire_prob

    def _check_game_end(self, game: dict) -> bool:
        """检查游戏是否应该结束

        Args:
            game: 游戏状态字典

        Returns:
            是否应该结束游戏
        """
        chambers = game.get("chambers", [])
        remaining = sum(chambers)

        if remaining == 0:
            return True

        if self.end_on_full_rotation:
            shot_count = game.get("shot_count", 0)
            remaining_chambers = self.chamber_count - (shot_count % self.chamber_count)
            if remaining == remaining_chambers:
                return True

        return False

    def _cleanup_game(self, group_id: str):
        """清理游戏状态和超时任务

        Args:
            group_id: 群ID
        """
        if group_id in self.timeout_tasks:
            self.timeout_tasks[group_id].cancel()
            del self.timeout_tasks[group_id]
        self.group_games.pop(group_id, None)

    async def _is_user_bannable(self, event: AstrMessageEvent, user_id: str) -> bool:
        """检查用户是否可以被禁言（群主/管理员免疫）

        无法判断角色时（接口缺失/异常）默认可以禁言，避免游戏卡住。
        """
        role = await self._get_group_role(event, user_id)
        if role in ("owner", "admin"):
            logger.info(f"用户 {user_id} 是{role}，跳过禁言")
            return False
        return True

    def _format_ban_duration(self, seconds: int) -> str:
        """格式化禁言时长显示

        Args:
            seconds: 禁言时长（秒）

        Returns:
            格式化后的时长字符串
        """
        if seconds < 60:
            return f"{seconds}秒"
        elif seconds < 3600:
            minutes = seconds // 60
            remaining_seconds = seconds % 60
            if remaining_seconds > 0:
                return f"{minutes}分{remaining_seconds}秒"
            else:
                return f"{minutes}分钟"
        else:
            hours = seconds // 3600
            remaining_minutes = (seconds % 3600) // 60
            if remaining_minutes > 0:
                return f"{hours}小时{remaining_minutes}分钟"
            else:
                return f"{hours}小时"

    async def _ban_user(
        self,
        event: AstrMessageEvent,
        user_id: str,
        *,
        is_bannable: bool | None = None,
    ) -> int:
        """禁言用户

        Args:
            event: 消息事件对象
            user_id: 要禁言的用户ID（QQ 官机为 member_openid）

        Returns:
            禁言时长（秒），如果禁言失败返回 0
        """
        group_id = self._get_group_id(event)
        if not group_id:
            logger.warning("❌ 无法获取群ID，跳过禁言")
            return 0

        if is_bannable is None:
            is_bannable = await self._is_user_bannable(event, user_id)
        if not is_bannable:
            user_name = self._get_user_name(event)
            logger.info(f"⏭️ 用户 {user_name}({user_id}) 是管理员/群主，跳过禁言")
            return 0

        duration = random.randint(self.min_ban, self.max_ban)
        formatted_duration = self._format_ban_duration(duration)
        self._last_ban_error = ""

        # QQ 官机：走 qqoffice_expand 中台禁言接口
        if is_qq_official_event(event):
            if self.qq_svc is None:
                self._last_ban_error = "svc_missing"
                logger.warning("官机中台未绑定，跳过禁言")
                return 0
            try:
                logger.info(f"🎯 正在禁言用户 {user_id}，时长 {formatted_duration}")
                expire = (
                    datetime.datetime.now(datetime.timezone.utc)
                    + datetime.timedelta(seconds=duration)
                ).isoformat(timespec="seconds")
                await self.qq_svc.group.mute_member(group_id, user_id, expire)
                logger.info(
                    f"✅ 用户 {user_id} 在群 {group_id} 被禁言 {formatted_duration}"
                )
                return duration
            except Exception as e:
                logger.error(f"❌ QQ官机禁言用户失败: {e}", exc_info=True)
                # 区分失败原因：机器人没权限 / 对方是群主管理员（免疫）
                err = str(e)
                if "机器人不是群管理员" in err or ("机器人" in err and "权限" in err):
                    self._last_ban_error = "bot_admin"
                elif "群主" in err or "管理员" in err or "普通成员" in err:
                    self._last_ban_error = "target_immune"
                return 0
        try:
            if hasattr(event.bot, "set_group_ban"):
                logger.info(f"🎯 正在禁言用户 {user_id}，时长 {formatted_duration}")
                await event.bot.set_group_ban(
                    group_id=int(group_id), user_id=int(user_id), duration=duration
                )
                logger.info(
                    f"✅ 用户 {user_id} 在群 {group_id} 被禁言 {formatted_duration}"
                )
                return duration
            else:
                logger.error("❌ Bot 没有 set_group_ban 方法，无法禁言")
                logger.error("💡 提示：请检查机器人适配器是否支持禁言功能")
        except Exception as e:
            logger.error(f"❌ 禁言用户失败: {e}", exc_info=True)
            # 检查是否是权限问题
            error_msg = str(e).lower()
            if any(
                keyword in error_msg
                for keyword in ["permission", "权限", "privilege", "insufficient"]
            ):
                logger.error("🔐 权限不足：请检查机器人是否有群管理权限！")
                logger.error("💡 解决方法：将机器人设置为群管理员")

        return 0

    def _format_ban_failure(self) -> str:
        """根据最近一次禁言失败原因生成回复文案"""
        if self._last_ban_error == "svc_missing":
            return "⚠️ 禁言失败！（需安装 qqoffice_expand 插件）"
        if self._last_ban_error == "target_immune":
            return "⚠️ 对方是群主/管理员，免疫禁言！"
        if self._last_ban_error == "bot_admin":
            return "⚠️ 禁言失败！（机器人需要群管理员权限）"
        return "⚠️ 禁言失败！"

    async def _send_group_text(self, bot, group_id: str, text: str):
        """跨平台发送群文本消息（OneBot 走 send_group_msg，QQ 官机走中台）"""
        if hasattr(bot, "send_group_msg"):
            await bot.send_group_msg(group_id=int(group_id), message=text)
        elif self.qq_svc is not None:
            await self.qq_svc.group.send(group_id, text)
        else:
            logger.warning("官机中台未绑定，跳过主动群消息")

    # ========== 共享业务逻辑 ==========

    async def _do_load_game(
        self,
        event: AstrMessageEvent,
        group_id: str,
        user_name: str,
        requested: int | None,
        ai_mode: bool = False,
    ) -> list[str]:
        """执行装填流程，返回应回复的消息列表。"""
        self._init_group(group_id)
        if group_id in self.group_games:
            return [f"💥 {user_name}，游戏还在进行中！"]

        bullet_count: int | None = None
        if requested is not None:
            max_allowed = (
                self.chamber_count - 1 if self.no_full_chamber else self.chamber_count
            )
            if 1 <= requested <= max_allowed:
                bullet_count = requested

        if bullet_count is not None:
            if not await self._is_group_admin(event):
                return [
                    f"😏 {user_name}，你又不是管理才不听你的！\n💡 请使用 /装填 进行随机装填"
                ]
        else:
            bullet_count = self._get_random_bullet_count()

        chambers = self._create_chambers(bullet_count)
        self.group_games[group_id] = {
            "chambers": chambers,
            "current": 0,
            "start_time": datetime.datetime.now(),
            "shot_count": 0,
        }
        await self._start_timeout(event, group_id)
        logger.info(
            f"{'AI: ' if ai_mode else ''}用户 {user_name} 在群 {group_id} 装填 {bullet_count} 发子弹"
        )

        bullet_display = "?" if self.hide_bullet_count else bullet_count
        load_msg = self.text_manager.get_text(
            "load_messages",
            sender_nickname=user_name,
            bullet_count=bullet_display,
        )
        if ai_mode:
            body = (
                f"🎯 {user_name} 挑战命运！\n🔫 {load_msg}\n"
                f"💀 {self.chamber_count} 弹膛，谁敢扣动扳机？\n"
                f"⚡ 限时 {self.timeout} 秒！"
            )
        else:
            body = (
                f"🔫 {load_msg}\n"
                f"💀 {self.chamber_count} 弹膛，生死一线！\n"
                f"⚡ 限时 {self.timeout} 秒！"
            )
        return [body]

    async def _do_shoot_game(
        self,
        event: AstrMessageEvent,
        group_id: str,
        user_name: str,
        user_id: int,
        ai_mode: bool = False,
    ) -> list[str]:
        """执行开枪流程，返回应回复的消息列表。"""
        self._init_group(group_id)
        game = self.group_games.get(group_id)
        if not game:
            return [f"⚠️ {user_name}，枪里没子弹！"]

        await self._start_timeout(event, group_id)

        chambers = game["chambers"]
        current = game["current"]
        game["shot_count"] = game.get("shot_count", 0) + 1
        prefix = "AI: " if ai_mode else ""
        msgs: list[str] = []

        if chambers[current]:
            chambers[current] = False
            game["current"] = (current + 1) % self.chamber_count

            is_bannable = await self._is_user_bannable(event, user_id)
            if not is_bannable:
                logger.info(
                    f"⏭️ {prefix}用户 {user_name}({user_id}) 是管理员/群主，免疫中弹"
                )
                msgs.append(
                    f"💥 枪声炸响！\n😱 {user_name} 中弹倒地！\n⚠️ 管理员/群主免疫！"
                )
            else:
                ban_duration = await self._ban_user(
                    event, user_id, is_bannable=is_bannable
                )
                if ban_duration > 0:
                    ban_msg = f"🔇 禁言 {self._format_ban_duration(ban_duration)}"
                else:
                    ban_msg = self._format_ban_failure()
                logger.info(
                    f"💥 {prefix}用户 {user_name}({user_id}) 在群 {group_id} 中弹"
                )
                trigger_msg = self.text_manager.get_text("trigger_descriptions")
                reaction_msg = self.text_manager.get_text(
                    "user_reactions", sender_nickname=user_name
                )
                msgs.append(f"💥 {trigger_msg}\n😱 {reaction_msg}\n{ban_msg}")
        else:
            game["current"] = (current + 1) % self.chamber_count
            logger.info(f"{prefix}用户 {user_name}({user_id}) 在群 {group_id} 空弹逃生")
            msgs.append(
                self.text_manager.get_text("miss_messages", sender_nickname=user_name)
            )

        if self._check_game_end(game):
            self._cleanup_game(group_id)
            logger.info(f"{prefix}群 {group_id} 游戏结束")
            end_msg = self.text_manager.get_text("game_end")
            msgs.append(f"🏁 {end_msg}\n🔄 再来一局？")
        return msgs

    def _do_status(self, group_id: str) -> str:
        """生成游戏状态文案。"""
        game = self.group_games.get(group_id)
        if not game:
            return (
                "🔍 没有游戏进行中\n"
                "💡 使用 /装填 开始游戏（随机装填）\n"
                "💡 管理员可使用 /装填 [数量] 指定子弹"
            )
        chambers = game["chambers"]
        current = game["current"]
        remaining = sum(chambers)
        status = "🎯 有子弹" if chambers[current] else "🍀 安全"
        title = self.text_manager.get_text("game_status")
        return (
            f"🔫 {title}\n"
            f"📊 剩余子弹：{remaining}发\n"
            f"🎯 当前弹膛：第{current + 1}膛\n"
            f"{status}"
        )

    async def _do_misfire(
        self,
        event: AstrMessageEvent,
        group_id: str,
        user_name: str,
        user_id: int,
    ) -> str:
        """执行随机走火，返回回复文案。"""
        is_bannable = await self._is_user_bannable(event, user_id)
        if not is_bannable:
            logger.info(
                f"⏭️ 群 {group_id} 用户 {user_name}({user_id}) 是管理员/群主，免疫随机走火"
            )
            return f"💥 手枪走火！\n😱 {user_name} 不幸中弹！\n⚠️ 管理员/群主免疫！"
        ban_duration = await self._ban_user(event, user_id, is_bannable=is_bannable)
        if ban_duration > 0:
            ban_msg = f"🔇 禁言 {self._format_ban_duration(ban_duration)}！"
        else:
            ban_msg = self._format_ban_failure()
        logger.info(f"💥 群 {group_id} 用户 {user_name}({user_id}) 触发随机走火")
        misfire_desc = self.text_manager.get_text("misfire_descriptions")
        reaction_msg = self.text_manager.get_text(
            "user_reactions", sender_nickname=user_name
        )
        return f"💥 {misfire_desc}\n😱 {reaction_msg}\n{ban_msg}"

    # ========== 独立指令 ==========

    @filter.command("装填")
    async def load_bullets(self, event: AstrMessageEvent):
        """装填子弹

        用法: [指令前缀]装填 [数量]
        不指定数量则随机装填1-6发子弹（所有用户可用）
        指定数量则装填固定子弹（仅限管理员）
        """
        try:
            hint = self._qq_gate(event)
            if hint:
                yield event.plain_result(hint)
                return
            group_id = self._get_group_id(event)
            if not group_id:
                yield event.plain_result("❌ 仅限群聊使用")
                return
            user_name = self._get_user_name(event)
            requested = self._parse_bullet_count(event.message_str or "")
            msgs = await self._do_load_game(
                event, group_id, user_name, requested, ai_mode=False
            )
            if await self._reply_load_result(event, group_id, msgs):
                return
            for msg in msgs:
                yield event.plain_result(msg)
        except Exception as e:
            logger.error(f"装填子弹失败: {e}")
            yield event.plain_result("❌ 装填失败，请重试")

    @filter.command("开枪")
    async def shoot(self, event: AstrMessageEvent):
        """扣动扳机

        用法: [指令前缀]开枪
        参与当前游戏的射击，可能中弹或空弹
        """
        try:
            hint = self._qq_gate(event)
            if hint:
                yield event.plain_result(hint)
                return
            group_id = self._get_group_id(event)
            if not group_id:
                yield event.plain_result("❌ 仅限群聊使用")
                return
            user_name = self._get_user_name(event)
            user_id = event.get_sender_id()
            for msg in await self._do_shoot_game(
                event, group_id, user_name, user_id, ai_mode=False
            ):
                yield event.plain_result(msg)
        except Exception as e:
            logger.error(f"开枪失败: {e}")
            yield event.plain_result("❌ 操作失败，请重试")

    @filter.command_group("左轮")
    def revolver_group(self):
        """左轮手枪游戏指令组"""
        pass

    @revolver_group.command("状态")
    async def game_status(self, event: AstrMessageEvent):
        """查看游戏状态

        用法: [指令前缀]左轮 状态
        查看当前游戏的子弹剩余情况和弹膛状态
        """
        try:
            hint = self._qq_gate(event)
            if hint:
                yield event.plain_result(hint)
                return
            group_id = self._get_group_id(event)
            if not group_id:
                yield event.plain_result("❌ 仅限群聊使用")
                return
            status_text = self._do_status(group_id)
            if await self._reply_status_result(event, group_id, status_text):
                return
            yield event.plain_result(status_text)
        except Exception as e:
            logger.error(f"查询游戏状态失败: {e}")
            yield event.plain_result("❌ 查询失败，请重试")

    @revolver_group.command("帮助")
    async def show_help(self, event: AstrMessageEvent):
        """显示帮助信息

        用法: [指令前缀]左轮 帮助
        显示插件的使用说明和游戏规则
        """
        try:
            help_text = f"""🔫 左轮手枪对决 v{self.plugin_version}

【用户指令】
/装填 - 随机装填子弹（1-6发）
/开枪 - 扣动扳机
/左轮 状态 - 查看游戏状态
/左轮 帮助 - 显示帮助

【管理员指令】
/装填 [数量] - 装填指定数量子弹（1-6发）
/走火开 - 开启随机走火
/走火关 - 关闭随机走火

【AI功能】
• "来玩左轮手枪" - 开启游戏
• "我也要玩" - 参与游戏
• "游戏状态" - 查询状态

【游戏规则】
• 6弹膛，随机装填指定数量子弹
• 中弹禁言60-300秒随机时长
• 超时120秒自动结束游戏
• 走火概率0.3%(如开启)
• 支持自然语言交互"""

            yield event.plain_result(help_text)
        except Exception as e:
            logger.error(f"显示帮助失败: {e}")
            yield event.plain_result("❌ 显示帮助失败")

    @filter.command("走火开")
    async def enable_misfire(self, event: AstrMessageEvent):
        """开启随机走火

        用法: [指令前缀]走火开
        开启后群聊中每条消息都有概率触发随机走火
        """
        try:
            hint = self._qq_gate(event)
            if hint:
                yield event.plain_result(hint)
                return
            group_id = self._get_group_id(event)
            if not group_id:
                yield event.plain_result("❌ 仅限群聊使用")
                return

            # 检查群管理员权限
            if not await self._is_group_admin(event):
                user_name = self._get_user_name(event)
                yield event.plain_result(f"😏 {user_name}，你又不是管理才不听你的！")
                return

            self._init_group(group_id)
            self.group_misfire[group_id] = True
            self._save_misfire_config()
            logger.info(f"群 {group_id} 随机走火已开启")
            yield event.plain_result("🔥 随机走火已开启！")
        except Exception as e:
            logger.error(f"开启走火失败: {e}")
            yield event.plain_result("❌ 操作失败，请重试")

    @filter.command("走火关")
    async def disable_misfire(self, event: AstrMessageEvent):
        """关闭随机走火

        用法: [指令前缀]走火关
        关闭随机走火功能
        """
        try:
            hint = self._qq_gate(event)
            if hint:
                yield event.plain_result(hint)
                return
            group_id = self._get_group_id(event)
            if not group_id:
                yield event.plain_result("❌ 仅限群聊使用")
                return

            # 检查群管理员权限
            if not await self._is_group_admin(event):
                user_name = self._get_user_name(event)
                yield event.plain_result(f"😏 {user_name}，你又不是管理才不听你的！")
                return

            self._init_group(group_id)
            self.group_misfire[group_id] = False
            self._save_misfire_config()
            logger.info(f"群 {group_id} 随机走火已关闭")
            yield event.plain_result("💤 随机走火已关闭！")
        except Exception as e:
            logger.error(f"关闭走火失败: {e}")
            yield event.plain_result("❌ 操作失败，请重试")

    # ========== 随机走火监听 ==========

    @filter.event_message_type(
        EventMessageType.GROUP_MESSAGE if EventMessageType else "group"
    )
    async def on_group_message(self, event: AstrMessageEvent):
        """监听群消息，触发随机走火

        监听非指令消息，根据设定的概率触发随机走火事件
        """
        try:
            group_id = self._get_group_id(event)
            if group_id and self._check_misfire(group_id):
                hint = self._qq_gate(event)
                if hint:
                    yield event.plain_result(hint)
                    return
                user_name = self._get_user_name(event)
                user_id = event.get_sender_id()
                yield event.plain_result(
                    await self._do_misfire(event, group_id, user_name, user_id)
                )
        except Exception as e:
            logger.error(f"随机走火监听失败: {e}")

    # ========== 辅助功能 ==========

    async def _start_timeout(self, event: AstrMessageEvent, group_id: str):
        """启动超时机制

        Args:
            event: 消息事件对象
            group_id: 群ID

        Note:
            使用 asyncio 创建后台任务，超时后自动结束游戏
        """
        # 取消之前的超时任务（如果存在）
        if group_id in self.timeout_tasks:
            task = self.timeout_tasks[group_id]
            if not task.done():
                task.cancel()

        # 保存必要的信息用于超时回调
        bot = event.bot

        # 创建新的超时任务
        async def timeout_check():
            try:
                await asyncio.sleep(self.timeout)
                # 检查游戏是否还在进行
                if group_id in self.group_games:
                    # 清理游戏状态
                    del self.group_games[group_id]

                    # 发送超时通知（跨平台）
                    try:
                        timeout_msg = self.text_manager.get_text("timeout")
                        await self._send_group_text(
                            bot,
                            group_id,
                            f"⏰ {timeout_msg}\n⏱️ {self.timeout} 秒无人操作\n🏁 游戏已自动结束",
                        )
                    except Exception as e:
                        logger.error(f"发送超时通知失败: {e}")

                    logger.info(f"群 {group_id} 游戏因超时而结束")
            except asyncio.CancelledError:
                # 任务被取消，说明有新操作
                pass
            except Exception as e:
                logger.error(f"超时检查失败: {e}")

        # 启动超时任务
        self.timeout_tasks[group_id] = asyncio.create_task(timeout_check())
        logger.debug(f"群 {group_id} 超时任务已启动，{self.timeout} 秒后触发")

    # ========== AI触发器管理 ==========

    def _register_ai_trigger(self, action: str, event: AstrMessageEvent) -> str:
        """注册AI触发器等待事件

        Args:
            action: 操作类型
            event: 消息事件对象

        Returns:
            生成的唯一标识符
        """
        # 使用插件内部计数器生成一致的ID
        self.ai_trigger_counter += 1
        unique_id = f"trigger_{self.ai_trigger_counter}_{event.get_sender_id()}"

        logger.info(f"AI trigger registered: {unique_id}, action={action}")
        self.ai_trigger_queue[unique_id] = {
            "action": action,
            "event": event,
            "timestamp": datetime.datetime.now(),
        }

        return unique_id

    async def _execute_ai_trigger(self, unique_id: str):
        """执行AI触发的操作

        Args:
            unique_id: 唯一标识符
        """
        if unique_id not in self.ai_trigger_queue:
            return

        trigger_data = self.ai_trigger_queue.pop(unique_id)

        action = trigger_data["action"]
        event = trigger_data["event"]

        try:
            execution_time = datetime.datetime.now() - trigger_data["timestamp"]
            logger.info(
                f"Executing AI trigger: {unique_id}, action={action}, wait_time={execution_time.total_seconds():.1f}s"
            )

            if action == "start":
                await self.ai_start_game(event, None)
            elif action == "join":
                await self.ai_join_game(event)
            elif action == "status":
                await self.ai_check_status(event)

        except Exception as e:
            logger.error(f"AI trigger execution failed: {e}")

    @filter.on_decorating_result(priority=5)
    async def _on_decorating_result(self, event: AstrMessageEvent):
        """消息装饰钩子 - 标记AI消息即将发送

        Args:
            event: 消息事件对象
        """
        try:
            # 只记录有AI触发器待处理，但不执行
            if self.ai_trigger_queue:
                logger.info(
                    f"Decorating result, {len(self.ai_trigger_queue)} triggers pending"
                )
        except Exception as e:
            logger.error(f"Decorating result hook failed: {e}")

    @filter.after_message_sent(priority=10)
    async def _on_message_sent(self, event: AstrMessageEvent):
        """消息发送后钩子 - 执行待处理的AI触发器

        Args:
            event: 消息事件对象
        """
        try:
            # 执行最早的待处理触发器
            if self.ai_trigger_queue:
                # 获取最早的触发器
                oldest_id = min(
                    self.ai_trigger_queue.keys(),
                    key=lambda k: self.ai_trigger_queue[k]["timestamp"],
                )

                logger.info(f"Message sent, executing AI trigger: {oldest_id}")

                # 使用配置的延迟时间
                delay = self.ai_trigger_delay
                logger.info(f"Waiting {delay}s before executing")
                await asyncio.sleep(delay)
                await self._execute_ai_trigger(oldest_id)

        except Exception as e:
            logger.error(f"Message sent hook failed: {e}")

    # ========== AI工具调用方法 ==========

    async def ai_start_game(self, event: AstrMessageEvent, bullets: int | None = None):
        """AI启动游戏 - 供 AI 工具调用"""
        group_id = self._get_group_id(event)
        if not group_id:
            logger.warning("AI工具无法获取group_id")
            return
        try:
            user_name = self._get_user_name(event)
            msgs = await self._do_load_game(
                event, group_id, user_name, bullets, ai_mode=True
            )
            if await self._reply_load_result(event, group_id, msgs):
                return
            for msg in msgs:
                await self._send_group_text(event.bot, group_id, msg)
        except Exception as e:
            logger.error(f"AI启动游戏失败: {e}")
            await self._send_group_text(event.bot, group_id, "❌ 游戏启动失败，请重试")

    async def ai_join_game(self, event: AstrMessageEvent):
        """AI参与游戏 - 供 AI 工具调用"""
        group_id = self._get_group_id(event)
        if not group_id:
            logger.warning("AI工具无法获取group_id")
            return
        try:
            user_name = self._get_user_name(event)
            user_id = event.get_sender_id()
            for msg in await self._do_shoot_game(
                event, group_id, user_name, user_id, ai_mode=True
            ):
                await self._send_group_text(event.bot, group_id, msg)
        except Exception as e:
            logger.error(f"AI参与游戏失败: {e}")
            await self._send_group_text(event.bot, group_id, "❌ 操作失败，请重试")

    async def ai_check_status(self, event: AstrMessageEvent):
        """AI查询游戏状态 - 供 AI 工具调用"""
        group_id = self._get_group_id(event)
        if not group_id:
            logger.warning("AI工具无法获取group_id")
            return
        try:
            await self._send_group_text(event.bot, group_id, self._do_status(group_id))
        except Exception as e:
            logger.error(f"AI查询状态失败: {e}")
            await self._send_group_text(event.bot, group_id, "❌ 查询失败，请重试")

    async def terminate(self):
        """插件卸载清理

        清理所有游戏状态和配置，确保插件安全卸载
        """
        try:
            # 解绑官机中台订阅
            if self._qq_unsub:
                self._qq_unsub()
                self._qq_unsub = None
            self.qq_svc = None
            self._last_card_msg.clear()
            # 先记录数量再清理
            num_games = len(self.group_games)
            num_configs = len(self.group_misfire)
            num_tasks = len(self.timeout_tasks)
            num_ai_triggers = len(self.ai_trigger_queue)

            # 取消所有超时任务
            for task in self.timeout_tasks.values():
                if not task.done():
                    task.cancel()

            # 清理游戏状态
            self.group_games.clear()
            self.group_misfire.clear()
            self.timeout_tasks.clear()
            self.ai_trigger_queue.clear()

            # 记录卸载日志
            logger.info(f"左轮手枪插件 v{self.plugin_version} 已安全卸载")
            logger.info(f"清理了 {num_games} 个游戏状态")
            logger.info(f"清理了 {num_configs} 个群配置")
            logger.info(f"取消了 {num_tasks} 个超时任务")
            logger.info(f"清理了 {num_ai_triggers} 个AI触发器")
        except Exception as e:
            logger.error(f"插件卸载失败: {e}")
            # 即使清理失败也不抛出异常，确保插件能够卸载
