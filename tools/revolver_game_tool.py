import asyncio
from astrbot.api import FunctionTool, logger
from astrbot.api.event import AstrMessageEvent
from typing import Optional


class BaseRevolverTool:
    """左轮手枪工具基类，包含通用辅助方法"""

    def _get_group_id(self, event: AstrMessageEvent) -> Optional[int]:
        """获取群ID"""
        return getattr(event.message_obj, "group_id", None)

    def _get_user_name(self, event: AstrMessageEvent) -> str:
        """获取用户昵称"""
        return event.get_sender_name() or "玩家"

    def _get_unique_id(self, event: AstrMessageEvent) -> str:
        """获取唯一标识符"""
        return f"{event.get_sender_id()}_{event.message_obj.message_id}"


class RevolverGameTool(FunctionTool, BaseRevolverTool):
    """AI统一触发器工具 - 左轮手枪游戏控制器w

    通过action参数控制不同操作：
    - start: 启动新游戏
    - join: 参与游戏开枪
    - status: 查询游戏状态
    """

    def __init__(self, plugin_instance=None):
        """初始化统一触发器工具

        Args:
            plugin_instance: 插件实例，用于触发游戏逻辑
        """
        self.name = "revolver_game"
        self.description = """左轮手枪轮盘赌游戏控制器 - 启动游戏、参与射击、查询状态

IMPORTANT: This is ONLY a trigger tool. The plugin handles all game logic, messaging, and user responses automatically.
DO NOT explain game rules, generate results, or describe game outcomes. The plugin will send all messages.

ACTIONS:
- "start": 启动新游戏（随机装填子弹）
- "join": 参与游戏开枪射击
- "status": 查询游戏进行状态

TRIGGER GUIDELINES - Use when users show ANY intent related to:
START action:
- 来玩/开始/开启左轮手枪游戏
- 装填子弹/准备游戏
- 再来一局/重新开始
- play roulette/let's play

JOIN action:
- 我要玩/我要参加/算我一个
- 开枪/扣动扳机/我来试试
- shoot me/my turn
- 我想参与

STATUS action:
- 游戏状态/情况怎么样
- 还剩几发子弹/现在什么情况
- what's the status/how many bullets left
- check game status

DELAY: Uses configured delay (default 5s) to complete AI response before triggering.

FLEXIBLE USAGE: Trust your judgment - if user seems to want any of these actions, use the appropriate action parameter."""

        self.parameters = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "操作类型: 'start'启动游戏, 'join'参与游戏, 'status'查询状态",
                    "enum": ["start", "join", "status"],
                },
            },
            "required": ["action"],
        }
        self.plugin = plugin_instance

    async def run(self, event: AstrMessageEvent, action: str) -> str:
        """统一触发器 - 根据action执行对应操作

        Args:
            event: 消息事件对象
            action: 操作类型 (start/join/status)

        Returns:
            操作结果状态信息
        """
        try:
            # 参数验证
            if action not in ["start", "join", "status"]:
                return f"PARAM_ERROR: Invalid action '{action}'. Must be 'start', 'join', or 'status'"

            # 获取对应的插件方法
            method_map = {
                "start": "ai_start_game",
                "join": "ai_join_game",
                "status": "ai_check_status",
            }

            method_name = method_map[action]
            if not hasattr(self.plugin, method_name):
                return f"SYSTEM_ERROR: Plugin method '{method_name}' unavailable"

            # 获取配置的延迟时间（默认5秒）作为超时时间
            timeout = getattr(self.plugin, "ai_trigger_delay", 5)

            # 生成唯一标识符
            unique_id = self._get_unique_id(event)

            # 在插件中注册等待事件
            if hasattr(self.plugin, "_register_ai_trigger"):
                self.plugin._register_ai_trigger(unique_id, action, event)
                return f"TRIGGER_QUEUED: {action} action queued for {unique_id}, timeout={timeout}s"
            else:
                # 回退到固定延迟方式
                await asyncio.sleep(timeout)
                await self._execute_action(action, event)
                return f"TRIGGER_SUCCESS: {action} action executed (fallback delay={timeout}s)"

        except Exception as e:
            error_msg = f"RevolverGameTool.{action} trigger failed"
            logger.error(f"{error_msg}: {e}")
            return f"SYSTEM_ERROR: Failed to trigger {action} action"

    async def _execute_action(self, action: str, event: AstrMessageEvent):
        """执行具体的游戏操作"""
        if action == "start":
            await self.plugin.ai_start_game(event, None)  # None表示随机装填
        elif action == "join":
            await self.plugin.ai_join_game(event)
        elif action == "status":
            await self.plugin.ai_check_status(event)
