from astrbot.api import FunctionTool, logger
from astrbot.api.event import AstrMessageEvent
from typing import Optional

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
        self.description = """Start a new game of Russian Roulette. Use this when user wants to play, start a new round, or says '再来一局' (play again). If bullet count is not specified, random bullets (1-6) will be loaded."""
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

    async def run(self, event: AstrMessageEvent, bullets: Optional[int] = None) -> str:
        """启动游戏逻辑 - 调用主插件方法完成所有操作"""
        try:
            if hasattr(self.plugin, "ai_start_game"):
                await self.plugin.ai_start_game(event, bullets)
                return "Tool executed successfully. The plugin has sent a response to the user."
            else:
                logger.error("AI工具无法找到 ai_start_game 方法")
                return "Error: Plugin method not found."
        except Exception as e:
            logger.error(f"AI工具 'start_revolver_game' 执行失败: {e}", exc_info=True)
            return f"Error: Tool execution failed: {e}"


class JoinRevolverGameTool(FunctionTool, BaseRevolverTool):
    """AI参与左轮手枪游戏的工具类"""

    def __init__(self, plugin_instance=None):
        """初始化工具

        Args:
            plugin_instance: 插件实例，用于访问游戏状态
        """
        self.name = "join_revolver_game"
        self.description = """Join the current Russian Roulette game by pulling the trigger. Use this when user says '我要玩', '我也要', '开枪', 'shoot', or wants to participate in an ongoing game."""
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
        """参与游戏逻辑 - 调用主插件方法完成所有操作"""
        try:
            if hasattr(self.plugin, "ai_join_game"):
                await self.plugin.ai_join_game(event)
                return "Tool executed successfully. The plugin has sent a response to the user."
            else:
                logger.error("AI工具无法找到 ai_join_game 方法")
                return "Error: Plugin method not found."
        except Exception as e:
            logger.error(f"AI工具 'join_revolver_game' 执行失败: {e}", exc_info=True)
            return f"Error: Tool execution failed: {e}"


class CheckRevolverStatusTool(FunctionTool, BaseRevolverTool):
    """AI查询左轮手枪游戏状态的工具类"""

    def __init__(self, plugin_instance=None):
        """初始化工具

        Args:
            plugin_instance: 插件实例，用于访问游戏状态
        """
        self.name = "check_revolver_status"
        self.description = """Check the current status of the Russian Roulette game. Use this when user asks about game status, wants to know remaining bullets, or says '状态', 'status', '游戏情况'."""
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
        """查询游戏状态逻辑 - 调用主插件方法完成所有操作"""
        try:
            if hasattr(self.plugin, "ai_check_status"):
                await self.plugin.ai_check_status(event)
                return "Tool executed successfully. The plugin has sent a response to the user."
            else:
                logger.error("AI工具无法找到 ai_check_status 方法")
                return "Error: Plugin method not found."
        except Exception as e:
            logger.error(f"AI工具 'check_revolver_status' 执行失败: {e}", exc_info=True)
            return f"Error: Tool execution failed: {e}"
