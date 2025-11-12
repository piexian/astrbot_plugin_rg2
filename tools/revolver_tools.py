from astrbot.api import FunctionTool
from astrbot.api.event import AstrMessageEvent
from typing import Optional
import random
import datetime

CHAMBER_COUNT = 6


class BaseRevolverTool:
    """左轮手枪工具基类，包含通用辅助方法"""

    def _get_group_id(self, event: AstrMessageEvent) -> Optional[int]:
        """获取群ID"""
        return getattr(event.message_obj, "group_id", None)

    def _get_user_name(self, event: AstrMessageEvent) -> str:
        """获取用户昵称"""
        return event.get_sender_name() or "玩家"


class StartRevolverGameTool(FunctionTool, BaseRevolverTool):
    """AI启动左轮手枪游戏的工具类"""

    def __init__(self, plugin_instance=None):
        """初始化工具

        Args:
            plugin_instance: 插件实例，用于访问游戏状态
        """
        self.name = "start_revolver_game"
        self.description = """Start a new game of Russian Roulette. Use this when user wants to play, start a new round, or says '再来一局' (play again). If bullet count is not specified, random bullets (1-6) will be loaded.
        
        CRITICAL INSTRUCTION: When you receive the result from this tool, you must output it EXACTLY as given without ANY modification, rephrasing, or adding personal commentary. Do NOT add phrases like '我来帮你' or '游戏开始了' - just output the tool's result directly."""
        self.parameters = {
            "type": "object",
            "properties": {
                "bullets": {
                    "type": "integer",
                    "description": "Number of bullets to load (1-6). If not provided, will load random bullets.",
                    "minimum": 1,
                    "maximum": 6,
                }
            },
            "required": [],
        }
        self.plugin = plugin_instance

    def _get_random_bullet_count(self) -> int:
        """获取随机子弹数量"""
        return random.randint(1, CHAMBER_COUNT)

    def _create_chambers(self, bullet_count: int):
        """创建弹膛状态"""
        chambers = [False] * CHAMBER_COUNT
        if bullet_count > 0:
            positions = random.sample(range(CHAMBER_COUNT), bullet_count)
            for pos in positions:
                chambers[pos] = True
        return chambers

    async def run(self, event: AstrMessageEvent, bullets: Optional[int] = None) -> str:
        """启动游戏逻辑 - 只准备游戏数据，不输出结果"""
        try:
            group_id = self._get_group_id(event)
            if not group_id:
                return "error:❌ 仅限群聊使用"

            # 检查现有游戏
            if group_id in self.plugin.group_games:
                return "error:💥 游戏还在进行中！"

            # 确定子弹数量
            if bullets is None or not (1 <= bullets <= CHAMBER_COUNT):
                bullets = self._get_random_bullet_count()

            # 创建游戏
            chambers = self._create_chambers(bullets)
            self.plugin.group_games[group_id] = {
                "chambers": chambers,
                "current": 0,
                "start_time": datetime.datetime.now(),
            }

            # 启动超时机制
            if hasattr(self.plugin, "_start_timeout"):
                await self.plugin._start_timeout(event, group_id)

            return f"game_started:{group_id}:{bullets}"
        except Exception as e:
            return f"error:{str(e)}"


class JoinRevolverGameTool(FunctionTool, BaseRevolverTool):
    """AI参与左轮手枪游戏的工具类"""

    def __init__(self, plugin_instance=None):
        """初始化工具

        Args:
            plugin_instance: 插件实例，用于访问游戏状态
        """
        self.name = "join_revolver_game"
        self.description = """Join the current Russian Roulette game by pulling the trigger. Use this when user says '我要玩', '我也要', '开枪', 'shoot', or wants to participate in an ongoing game.
        
        CRITICAL INSTRUCTION: When you receive the result from this tool, you must output it EXACTLY as given without ANY modification, rephrasing, or adding personal touches. Even if the result contains emoji or specific phrasing, output it verbatim. Do NOT add explanations like '结果出来了' or '你开枪了' - just output the tool's result directly."""
        self.parameters = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "User's action to perform in the game. Common values: 'shoot' (开枪), 'join' (加入游戏), 'participate' (参与活动). If not specified, defaults to 'shoot'.",
                    "enum": ["shoot", "join", "participate"],
                }
            },
            "required": [],
        }
        self.plugin = plugin_instance

    async def run(self, event: AstrMessageEvent, action: str = "shoot") -> str:
        """参与游戏逻辑 - 只返回动作指令，不执行禁言"""
        try:
            group_id = self._get_group_id(event)
            if not group_id:
                return "error:❌ 仅限群聊使用"

            game = self.plugin.group_games.get(group_id)
            if not game:
                return "error:⚠️ 没有游戏进行中\n💡 使用 /装填 开始游戏（随机装填）\n💡 管理员可使用 /装填 [数量] 指定子弹"

            user_name = self._get_user_name(event)
            user_id = int(event.get_sender_id())

            chambers = game["chambers"]
            current = game["current"]
            hit = chambers[current]

            # 更新游戏状态
            if hit:
                chambers[current] = False
            game["current"] = (current + 1) % CHAMBER_COUNT

            # 检查游戏是否结束
            game_ended = sum(chambers) == 0
            if game_ended:
                if (
                    hasattr(self.plugin, "timeout_tasks")
                    and group_id in self.plugin.timeout_tasks
                ):
                    task = self.plugin.timeout_tasks[group_id]
                    if not task.done():
                        task.cancel()
                    self.plugin.timeout_tasks.pop(group_id, None)
                del self.plugin.group_games[group_id]

            # 返回动作指令
            action_code = "hit" if hit else "miss"
            return f"game_action:{group_id}:{user_id}:{user_name}:{action_code}:{game_ended}"

        except Exception as e:
            return f"error:{str(e)}"


class CheckRevolverStatusTool(FunctionTool, BaseRevolverTool):
    """AI查询左轮手枪游戏状态的工具类"""

    def __init__(self, plugin_instance=None):
        """初始化工具

        Args:
            plugin_instance: 插件实例，用于访问游戏状态
        """
        self.name = "check_revolver_status"
        self.description = """Check the current status of the Russian Roulette game. Use this when user asks about game status, wants to know remaining bullets, or says '状态', 'status', '游戏情况'.
        
        CRITICAL INSTRUCTION: When you receive the result from this tool, you must output it EXACTLY as given without ANY modification, rephrasing, or adding personal commentary. Even if the result looks like '没有游戏进行中', output it directly. Do NOT add phrases like '根据查询' or '我来告诉你' - just output the tool's result verbatim."""
        self.parameters = {
            "type": "object",
            "properties": {
                "detailed": {
                    "type": "boolean",
                    "description": "Whether to return detailed game status including current chamber position and game history. If true, provides more comprehensive information. Default is false for basic status.",
                }
            },
            "required": [],
        }
        self.plugin = plugin_instance

    async def run(self, event: AstrMessageEvent, detailed: bool = False) -> str:
        """查询游戏状态逻辑 - 只返回状态数据"""
        try:
            group_id = self._get_group_id(event)
            if not group_id:
                return "error:❌ 仅限群聊使用"

            game = self.plugin.group_games.get(group_id)
            if not game:
                return "no_game"

            chambers = game["chambers"]
            current = game["current"]
            remaining = sum(chambers)
            is_danger = chambers[current]

            return f"game_status:{group_id}:{remaining}:{current}:{is_danger}"

        except Exception as e:
            return f"error:{str(e)}"
