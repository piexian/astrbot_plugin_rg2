import random
import datetime
import asyncio
from typing import Dict, List, Optional
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger

# 插件元数据
PLUGIN_NAME = "astrbot_plugin_rg2"
PLUGIN_AUTHOR = "piexian"
PLUGIN_DESCRIPTION = (
    "一个刺激的群聊轮盘赌游戏插件，支持管理员装填子弹、用户开枪对决、随机走火等功能"
)
PLUGIN_VERSION = "1.1.0"  # 默认版本，将从metadata.yaml读取
PLUGIN_REPO = "https://github.com/piexian/astrbot_plugin_rg2"

# 文本管理器（延迟初始化）
text_manager = None

# 导入事件类型
try:
    from astrbot.core.star.filter.event_message_type import EventMessageType
except ImportError:
    # 兼容旧版本
    EventMessageType = None

CHAMBER_COUNT = 6
DEFAULT_TIMEOUT = 120
DEFAULT_MISFIRE_PROB = 0.003
DEFAULT_MIN_BAN = 60
DEFAULT_MAX_BAN = 300


@register(
    PLUGIN_NAME,
    PLUGIN_AUTHOR,
    PLUGIN_DESCRIPTION,
    PLUGIN_VERSION,
    PLUGIN_REPO,
)
class RevolverGunPlugin(Star):
    def __init__(self, context: Context, config: Optional[Dict] = None):
        """初始化左轮手枪插件

        Args:
            context: AstrBot上下文对象
            config: 插件配置字典
        """
        super().__init__(context)
        self.context = context
        self.config = config or {}

        # 读取插件版本
        self._load_plugin_version()

        # 游戏状态管理
        self.group_games: Dict[int, Dict] = {}
        self.group_misfire: Dict[int, bool] = {}
        self.timeout_tasks: Dict[int, asyncio.Task] = {}

        # AI触发器事件队列
        self.ai_trigger_queue: Dict[str, Dict] = {}

        # 数据持久化
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_rg2")
        self.config_file = self.data_dir / "group_misfire.json"

        # 加载持久化配置
        self._load_misfire_config()

        # 初始化文本管理器
        self._init_text_manager()

        # 配置参数
        self.timeout = self.config.get("timeout_seconds", DEFAULT_TIMEOUT)
        self.misfire_prob = self.config.get("misfire_probability", DEFAULT_MISFIRE_PROB)
        self.min_ban = self.config.get("min_ban_seconds", DEFAULT_MIN_BAN)
        self.max_ban = self.config.get("max_ban_seconds", DEFAULT_MAX_BAN)
        self.default_misfire = self.config.get("misfire_enabled_by_default", False)
        self.ai_trigger_delay = self.config.get(
            "ai_trigger_delay", 5
        )  # AI工具触发延迟（秒）

        # 注册函数工具
        self._register_function_tools()

    def _load_plugin_version(self):
        """从metadata.yaml读取插件版本"""
        try:
            import yaml
            import os

            # 获取插件目录路径
            current_dir = os.path.dirname(os.path.abspath(__file__))
            metadata_path = os.path.join(current_dir, "metadata.yaml")

            if os.path.exists(metadata_path):
                with open(metadata_path, "r", encoding="utf-8") as f:
                    metadata = yaml.safe_load(f)
                    self.plugin_version = metadata.get("version", PLUGIN_VERSION)
                    logger.info(f"插件版本从metadata.yaml读取: {self.plugin_version}")
            else:
                self.plugin_version = PLUGIN_VERSION
                logger.warning(
                    f"未找到metadata.yaml，使用默认版本: {self.plugin_version}"
                )

        except Exception as e:
            self.plugin_version = PLUGIN_VERSION
            logger.error(f"读取插件版本失败，使用默认版本: {e}")

    def _init_text_manager(self):
        """初始化文本管理器"""
        global text_manager
        try:
            from .text_manager import TextManager

            self.text_manager = TextManager(config=self.config)
            text_manager = self.text_manager
            logger.info("文本管理器初始化成功")
        except Exception as e:
            logger.error(f"文本管理器初始化失败: {e}")

            # 使用默认文本管理器（空实现）
            class DummyTextManager:
                def get_text(self, category, **kwargs):
                    return ""

            text_manager = DummyTextManager()

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

    def _get_group_id(self, event: AstrMessageEvent) -> Optional[int]:
        """获取群ID

        Args:
            event: 消息事件对象

        Returns:
            群ID，如果不在群聊中返回None
        """
        # 首先尝试从 message_obj 获取（普通消息）
        group_id = getattr(event.message_obj, "group_id", None)
        if group_id:
            return group_id

        # 如果失败，尝试从 unified_msg_origin 解析（LLM工具调用）
        try:
            origin = getattr(event, "unified_msg_origin", "")
            if origin and ":group:" in origin:
                # 格式: platform_name:group:group_id
                parts = origin.split(":")
                if len(parts) >= 3:
                    return int(parts[2])
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

    async def _is_group_admin(self, event: AstrMessageEvent) -> bool:
        """检查用户是否是群管理员

        Args:
            event: 消息事件对象

        Returns:
            是否是群管理员
        """
        try:
            group_id = self._get_group_id(event)
            if not group_id:
                return False

            user_id = int(event.get_sender_id())

            # 检查是否是bot超级管理员
            if event.is_admin():
                return True

            # 调用napcat接口获取群成员信息
            if hasattr(event.bot, "get_group_member_info"):
                member_info = await event.bot.get_group_member_info(
                    group_id=group_id, user_id=user_id, no_cache=True
                )

                # 检查角色：owner(群主) 或 admin(管理员)
                role = (
                    member_info.get("role", "")
                    if isinstance(member_info, dict)
                    else getattr(member_info, "role", "")
                )
                return role in ["owner", "admin"]

            return False
        except Exception as e:
            logger.error(f"检查群管理员权限失败: {e}")
            return False

    def _init_group(self, group_id: int):
        """初始化群状态

        Args:
            group_id: 群ID
        """
        if group_id not in self.group_misfire:
            self.group_misfire[group_id] = self.default_misfire

    def _load_misfire_config(self):
        """加载走火配置"""
        try:
            import json

            if self.config_file.exists():
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.group_misfire.update(data)
                logger.info(f"已加载 {len(data)} 个群的走火配置")
            else:
                logger.info("未找到走火配置文件，使用默认配置")
        except Exception as e:
            logger.error(f"加载走火配置失败: {e}")

    def _save_misfire_config(self):
        """保存走火配置"""
        try:
            import json

            self.data_dir.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.group_misfire, f, ensure_ascii=False, indent=2)
            logger.debug(f"已保存 {len(self.group_misfire)} 个群的走火配置")
        except Exception as e:
            logger.error(f"保存走火配置失败: {e}")

    def _create_chambers(self, bullet_count: int) -> List[bool]:
        """创建弹膛状态

        Args:
            bullet_count: 子弹数量

        Returns:
            弹膛状态列表，True表示有子弹
        """
        chambers = [False] * CHAMBER_COUNT
        if bullet_count > 0:
            positions = random.sample(range(CHAMBER_COUNT), bullet_count)
            for pos in positions:
                chambers[pos] = True
        return chambers

    def _get_random_bullet_count(self) -> int:
        """获取随机子弹数量

        Returns:
            1-6之间的随机整数
        """
        return random.randint(1, CHAMBER_COUNT)

    def _parse_bullet_count(self, message: str) -> Optional[int]:
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
            if 1 <= count <= CHAMBER_COUNT:
                return count
        except (ValueError, IndexError):
            pass
        return None

    def _check_misfire(self, group_id: int) -> bool:
        """检查是否触发随机走火

        Args:
            group_id: 群ID

        Returns:
            是否触发走火
        """
        if not self.group_misfire.get(group_id, False):
            return False
        return random.random() < self.misfire_prob

    async def _is_user_bannable(self, event: AstrMessageEvent, user_id: int) -> bool:
        """检查用户是否可以被禁言（不是群主或管理员）

        Args:
            event: 消息事件对象
            user_id: 要检查的用户ID

        Returns:
            是否可以被禁言
        """
        try:
            group_id = self._get_group_id(event)
            if not group_id:
                return False

            # 调用API获取群成员信息
            if hasattr(event.bot, "get_group_member_info"):
                member_info = await event.bot.get_group_member_info(
                    group_id=group_id, user_id=user_id, no_cache=True
                )

                # 检查角色
                role = (
                    member_info.get("role", "member")
                    if isinstance(member_info, dict)
                    else getattr(member_info, "role", "member")
                )

                # 群主和管理员不能被禁言
                if role in ["owner", "admin"]:
                    logger.info(f"用户 {user_id} 是{role}，跳过禁言")
                    return False

                return True

            # 如果无法获取信息，默认可以禁言（兼容旧版本）
            return True
        except Exception as e:
            logger.error(f"检查用户可禁言状态失败: {e}")
            # 出错时默认可以禁言，避免游戏卡住
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

    async def _ban_user(self, event: AstrMessageEvent, user_id: int) -> int:
        """禁言用户

        Args:
            event: 消息事件对象
            user_id: 要禁言的用户ID

        Returns:
            禁言时长（秒），如果禁言失败返回 0
        """
        group_id = self._get_group_id(event)
        if not group_id:
            logger.warning("❌ 无法获取群ID，跳过禁言")
            return 0

        # 检查是否可以禁言该用户
        if not await self._is_user_bannable(event, user_id):
            user_name = self._get_user_name(event)
            logger.info(f"⏭️ 用户 {user_name}({user_id}) 是管理员/群主，跳过禁言")
            return 0

        duration = random.randint(self.min_ban, self.max_ban)
        formatted_duration = self._format_ban_duration(duration)

        try:
            if hasattr(event.bot, "set_group_ban"):
                logger.info(f"🎯 正在禁言用户 {user_id}，时长 {formatted_duration}")
                await event.bot.set_group_ban(
                    group_id=group_id, user_id=user_id, duration=duration
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

    # ========== 独立指令 ==========

    @filter.command("装填")
    async def load_bullets(self, event: AstrMessageEvent):
        """装填子弹

        用法: [指令前缀]装填 [数量]
        不指定数量则随机装填1-6发子弹（所有用户可用）
        指定数量则装填固定子弹（仅限管理员）
        """
        try:
            group_id = self._get_group_id(event)
            if not group_id:
                yield event.plain_result("❌ 仅限群聊使用")
                return

            self._init_group(group_id)
            user_name = self._get_user_name(event)

            # 检查是否已有游戏
            if group_id in self.group_games:
                yield event.plain_result(f"💥 {user_name}，游戏还在进行中！")
                return

            # 解析子弹数量
            bullet_count = self._parse_bullet_count(event.message_str or "")

            # 如果指定了子弹数量，检查是否是管理员
            if bullet_count is not None:
                if not await self._is_group_admin(event):
                    yield event.plain_result(
                        f"😏 {user_name}，你又不是管理才不听你的！\n💡 请使用 /装填 进行随机装填"
                    )
                    return
            else:
                # 未指定数量，随机装填
                bullet_count = self._get_random_bullet_count()

            # 创建游戏
            chambers = self._create_chambers(bullet_count)
            self.group_games[group_id] = {
                "chambers": chambers,
                "current": 0,
                "start_time": datetime.datetime.now(),
            }

            # 设置超时
            await self._start_timeout(event, group_id)

            logger.info(f"用户 {user_name} 在群 {group_id} 装填 {bullet_count} 发子弹")

            # 使用YAML文本
            load_msg = text_manager.get_text("load_messages", sender_nickname=user_name)
            yield event.plain_result(
                f"🔫 {load_msg}\n"
                f"💀 {CHAMBER_COUNT} 弹膛，生死一线！\n"
                f"⚡ 限时 {self.timeout} 秒！"
            )
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
            group_id = self._get_group_id(event)
            if not group_id:
                yield event.plain_result("❌ 仅限群聊使用")
                return

            self._init_group(group_id)
            user_name = self._get_user_name(event)
            user_id = int(event.get_sender_id())

            # 检查游戏状态
            game = self.group_games.get(group_id)
            if not game:
                yield event.plain_result(f"⚠️ {user_name}，枪里没子弹！")
                return

            # 重置超时
            await self._start_timeout(event, group_id)

            # 执行射击
            chambers = game["chambers"]
            current = game["current"]

            if chambers[current]:
                # 中弹
                chambers[current] = False
                game["current"] = (current + 1) % CHAMBER_COUNT

                # 检查是否可禁言（管理员/群主免疫）
                if not await self._is_user_bannable(event, user_id):
                    # 管理员/群主免疫，直接显示免疫提示
                    logger.info(
                        f"⏭️ 用户 {user_name}({user_id}) 是管理员/群主，免疫中弹"
                    )
                    yield event.plain_result(
                        f"💥 枪声炸响！\n😱 {user_name} 中弹倒地！\n⚠️ 管理员/群主免疫！"
                    )
                else:
                    # 普通用户，执行禁言
                    ban_duration = await self._ban_user(event, user_id)
                    if ban_duration > 0:
                        formatted_duration = self._format_ban_duration(ban_duration)
                        ban_msg = f"🔇 禁言 {formatted_duration}"
                    else:
                        ban_msg = "⚠️ 禁言失败！"

                    logger.info(f"💥 用户 {user_name}({user_id}) 在群 {group_id} 中弹")

                    # 使用YAML文本
                    trigger_msg = text_manager.get_text("trigger_descriptions")
                    reaction_msg = text_manager.get_text(
                        "user_reactions", sender_nickname=user_name
                    )
                    yield event.plain_result(
                        f"💥 {trigger_msg}\n😱 {reaction_msg}\n{ban_msg}"
                    )
            else:
                # 空弹
                game["current"] = (current + 1) % CHAMBER_COUNT

                logger.info(f"用户 {user_name}({user_id}) 在群 {group_id} 空弹逃生")

                # 使用YAML文本
                miss_msg = text_manager.get_text(
                    "miss_messages", sender_nickname=user_name
                )
                yield event.plain_result(miss_msg)

            # 检查游戏结束
            remaining = sum(chambers)
            if remaining == 0:
                # 清理超时任务（如果存在）
                if group_id in self.timeout_tasks:
                    self.timeout_tasks[group_id].cancel()
                # 确保从字典中移除（无论是否存在）
                self.timeout_tasks.pop(group_id, None)

                # 清理游戏状态
                del self.group_games[group_id]
                logger.info(f"群 {group_id} 游戏结束")
                # 使用YAML文本
                end_msg = text_manager.get_text("game_end")
                yield event.plain_result(f"🏁 {end_msg}\n🔄 再来一局？")

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
            group_id = self._get_group_id(event)
            if not group_id:
                yield event.plain_result("❌ 仅限群聊使用")
                return

            game = self.group_games.get(group_id)
            if not game:
                yield event.plain_result(
                    "🔍 没有游戏进行中\n💡 使用 /装填 开始游戏（随机装填）\n💡 管理员可使用 /装填 [数量] 指定子弹"
                )
                return

            chambers = game["chambers"]
            current = game["current"]
            remaining = sum(chambers)

            status = "🎯 有子弹" if chambers[current] else "🍀 安全"

            yield event.plain_result(
                f"🔫 游戏进行中\n"
                f"📊 剩余子弹：{remaining}发\n"
                f"🎯 当前弹膛：第{current + 1}膛\n"
                f"{status}"
            )
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
            help_text = """🔫 左轮手枪对决 v1.0

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
            # 检查走火（不检查前缀，依赖框架指令系统处理指令）
            group_id = self._get_group_id(event)
            if group_id and self._check_misfire(group_id):
                user_name = self._get_user_name(event)
                user_id = int(event.get_sender_id())

                # 检查是否可禁言（管理员/群主免疫）
                if not await self._is_user_bannable(event, user_id):
                    # 管理员/群主免疫，直接显示免疫提示
                    logger.info(
                        f"⏭️ 群 {group_id} 用户 {user_name}({user_id}) 是管理员/群主，免疫随机走火"
                    )
                    yield event.plain_result(
                        f"💥 手枪走火！\n😱 {user_name} 不幸中弹！\n⚠️ 管理员/群主免疫！"
                    )
                else:
                    # 普通用户，执行禁言
                    ban_duration = await self._ban_user(event, user_id)
                    if ban_duration > 0:
                        formatted_duration = self._format_ban_duration(ban_duration)
                        ban_msg = f"🔇 禁言 {formatted_duration}！"
                    else:
                        ban_msg = "⚠️ 禁言失败！"

                    logger.info(
                        f"💥 群 {group_id} 用户 {user_name}({user_id}) 触发随机走火"
                    )

                    # 使用YAML文本
                    misfire_desc = text_manager.get_text("misfire_descriptions")
                    reaction_msg = text_manager.get_text(
                        "user_reactions", sender_nickname=user_name
                    )
                    yield event.plain_result(
                        f"💥 {misfire_desc}\n😱 {reaction_msg}\n{ban_msg}"
                    )
        except Exception as e:
            logger.error(f"随机走火监听失败: {e}")

    # ========== 辅助功能 ==========

    async def _start_timeout(self, event: AstrMessageEvent, group_id: int):
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

                    # 发送超时通知（使用bot对象）
                    try:
                        timeout_msg = text_manager.get_text("timeout")
                        if hasattr(bot, "send_group_msg"):
                            await bot.send_group_msg(
                                group_id=group_id,
                                message=f"⏰ {timeout_msg}\n⏱️ {self.timeout} 秒无人操作\n🏁 游戏已自动结束",
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

    def _register_ai_trigger(
        self, unique_id: str, action: str, event: AstrMessageEvent
    ):
        """注册AI触发器等待事件

        Args:
            unique_id: 唯一标识符
            action: 操作类型
            event: 消息事件对象
        """
        logger.info(f"AI trigger registered: {unique_id}, action={action}")
        self.ai_trigger_queue[unique_id] = {
            "action": action,
            "event": event,
            "timestamp": datetime.datetime.now(),
        }

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

    @filter.on_decorating_result(priority=10)
    async def _on_decorating_result(self, event: AstrMessageEvent):
        """消息装饰钩子 - 在消息发送前检查并执行待处理的AI触发器

        Args:
            event: 消息事件对象
        """
        try:
            # 生成唯一标识符
            unique_id = f"{event.get_sender_id()}_{getattr(event.message_obj, 'message_id', 'unknown')}"

            # 检查是否有待处理的触发器
            if unique_id in self.ai_trigger_queue:
                # 使用配置的延迟时间
                delay = self.ai_trigger_delay
                logger.info(
                    f"Decorating result, waiting {delay}s before executing AI trigger: {unique_id}"
                )
                await asyncio.sleep(delay)
                await self._execute_ai_trigger(unique_id)

        except Exception as e:
            logger.error(f"Decorating result hook failed: {e}")

    @filter.after_message_sent(priority=10)
    async def _on_message_sent(self, event: AstrMessageEvent):
        """消息发送后钩子 - 备用触发器检查

        Args:
            event: 消息事件对象
        """
        try:
            # 生成唯一标识符
            unique_id = f"{event.get_sender_id()}_{getattr(event.message_obj, 'message_id', 'unknown')}"

            # 检查是否有待处理的触发器（备用机制）
            if unique_id in self.ai_trigger_queue:
                logger.info(f"Message sent (backup), executing AI trigger: {unique_id}")
                await self._execute_ai_trigger(unique_id)

        except Exception as e:
            logger.error(f"Message sent hook failed: {e}")

    # ========== AI工具调用方法 ==========

    async def ai_start_game(
        self, event: AstrMessageEvent, bullets: Optional[int] = None
    ):
        """AI启动游戏 - 供AI工具调用

        Args:
            event: 消息事件对象
            bullets: 子弹数量(可选)
        """
        group_id = self._get_group_id(event)
        if not group_id:
            logger.warning("AI工具无法获取group_id")
            return

        try:
            self._init_group(group_id)
            user_name = self._get_user_name(event)

            # 检查是否已有游戏
            if group_id in self.group_games:
                await event.bot.send_group_msg(
                    group_id=group_id, message=f"💥 {user_name}，游戏还在进行中！"
                )
                return

            # 解析子弹数量
            if bullets is not None and 1 <= bullets <= CHAMBER_COUNT:
                # 用户指定了子弹数量，检查是否是管理员
                if not await self._is_group_admin(event):
                    await event.bot.send_group_msg(
                        group_id=group_id,
                        message=f"😏 {user_name}，你又不是管理才不听你的！\n💡 请使用 /装填 进行随机装填",
                    )
                    return
            else:
                # 未指定或无效数量，随机装填
                bullets = self._get_random_bullet_count()

            # 创建游戏
            chambers = self._create_chambers(bullets)
            self.group_games[group_id] = {
                "chambers": chambers,
                "current": 0,
                "start_time": datetime.datetime.now(),
            }

            # 设置超时
            await self._start_timeout(event, group_id)

            logger.info(f"AI: 用户 {user_name} 在群 {group_id} 装填 {bullets} 发子弹")

            # 使用YAML文本
            load_msg = text_manager.get_text("load_messages", sender_nickname=user_name)
            response_text = f"🎯 {user_name} 挑战命运！\n🔫 {load_msg}\n💀 谁敢扣动扳机？\n⚡ 限时 {self.timeout} 秒！"
            await event.bot.send_group_msg(group_id=group_id, message=response_text)

        except Exception as e:
            logger.error(f"AI启动游戏失败: {e}")
            await event.bot.send_group_msg(
                group_id=group_id, message="❌ 游戏启动失败，请重试"
            )

    async def ai_join_game(self, event: AstrMessageEvent):
        """AI参与游戏 - 供AI工具调用

        Args:
            event: 消息事件对象
        """
        group_id = self._get_group_id(event)
        if not group_id:
            logger.warning("AI工具无法获取group_id")
            return

        try:
            self._init_group(group_id)
            user_name = self._get_user_name(event)
            user_id = int(event.get_sender_id())

            # 检查游戏状态
            game = self.group_games.get(group_id)
            if not game:
                await event.bot.send_group_msg(
                    group_id=group_id, message=f"⚠️ {user_name}，枪里没子弹！"
                )
                return

            # 重置超时
            await self._start_timeout(event, group_id)

            # 执行射击
            chambers = game["chambers"]
            current = game["current"]
            hit = chambers[current]
            result_msg = ""

            if hit:
                # 中弹
                chambers[current] = False
                game["current"] = (current + 1) % CHAMBER_COUNT

                # 检查是否可禁言（管理员/群主免疫）
                if not await self._is_user_bannable(event, user_id):
                    logger.info(
                        f"⏭️ AI: 用户 {user_name}({user_id}) 是管理员/群主，免疫中弹"
                    )
                    result_msg = (
                        f"💥 枪声炸响！\n😱 {user_name} 中弹倒地！\n⚠️ 管理员/群主免疫！"
                    )
                else:
                    # 普通用户，执行禁言
                    ban_duration = await self._ban_user(event, user_id)
                    if ban_duration > 0:
                        formatted_duration = self._format_ban_duration(ban_duration)
                        ban_msg = f"🔇 禁言 {formatted_duration}"
                    else:
                        ban_msg = "⚠️ 禁言失败！"

                    logger.info(
                        f"💥 AI: 用户 {user_name}({user_id}) 在群 {group_id} 中弹"
                    )

                    # 使用YAML文本
                    trigger_msg = text_manager.get_text("trigger_descriptions")
                    reaction_msg = text_manager.get_text(
                        "user_reactions", sender_nickname=user_name
                    )
                    result_msg = f"💥 {trigger_msg}\n😱 {reaction_msg}\n{ban_msg}"
            else:
                # 空弹
                game["current"] = (current + 1) % CHAMBER_COUNT
                logger.info(f"AI: 用户 {user_name}({user_id}) 在群 {group_id} 空弹逃生")
                # 使用YAML文本
                result_msg = text_manager.get_text(
                    "miss_messages", sender_nickname=user_name
                )

            # 发送初步结果
            await event.bot.send_group_msg(group_id=group_id, message=result_msg)

            # 检查游戏结束
            remaining = sum(chambers)
            if remaining == 0:
                # 清理超时任务（如果存在）
                if group_id in self.timeout_tasks:
                    self.timeout_tasks[group_id].cancel()
                self.timeout_tasks.pop(group_id, None)

                # 清理游戏状态
                del self.group_games[group_id]
                logger.info(f"AI: 群 {group_id} 游戏结束")
                # 使用YAML文本
                end_msg = text_manager.get_text("game_end")
                await event.bot.send_group_msg(
                    group_id=group_id, message=f"🏁 {end_msg}\n🔄 再来一局？"
                )

        except Exception as e:
            logger.error(f"AI参与游戏失败: {e}")
            await event.bot.send_group_msg(
                group_id=group_id, message="❌ 操作失败，请重试"
            )

    async def ai_check_status(self, event: AstrMessageEvent):
        """AI查询游戏状态 - 供AI工具调用

        Args:
            event: 消息事件对象
        """
        group_id = self._get_group_id(event)
        if not group_id:
            logger.warning("AI工具无法获取group_id")
            return

        try:
            game = self.group_games.get(group_id)
            if not game:
                response_text = "🔍 没有游戏进行中\n💡 使用 /装填 开始游戏（随机装填）\n💡 管理员可使用 /装填 [数量] 指定子弹"
            else:
                chambers = game["chambers"]
                current = game["current"]
                remaining = sum(chambers)
                status = "🎯 有子弹" if chambers[current] else "🍀 安全"
                response_text = (
                    f"🔫 游戏进行中\n"
                    f"📊 剩余子弹：{remaining}发\n"
                    f"🎯 当前弹膛：第{current + 1}膛\n"
                    f"{status}"
                )
            await event.bot.send_group_msg(group_id=group_id, message=response_text)
        except Exception as e:
            logger.error(f"AI查询状态失败: {e}")
            await event.bot.send_group_msg(
                group_id=group_id, message="❌ 查询失败，请重试"
            )

    async def terminate(self):
        """插件卸载清理

        清理所有游戏状态和配置，确保插件安全卸载
        """
        try:
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
