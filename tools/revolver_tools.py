from astrbot.api import FunctionTool
from astrbot.api.event import AstrMessageEvent
from dataclasses import dataclass, field
from typing import Optional
import random
import datetime

CHAMBER_COUNT = 6

@dataclass
class StartRevolverGameTool(FunctionTool):
    """AI启动左轮手枪游戏的工具类"""
    
    name: str = "start_revolver_game"
    description: str = "Start a new game of Russian Roulette. Use this when user wants to play, start a new round, or says '再来一局' (play again). If bullet count is not specified, random bullets (1-6) will be loaded."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "bullets": {
                    "type": "integer",
                    "description": "Number of bullets to load (1-6). If not provided, will load random bullets.",
                    "minimum": 1,
                    "maximum": 6
                }
            },
            "required": []
        }
    )
    
    def __post_init__(self):
        """初始化游戏状态存储"""
        self.group_games = {}
        self.group_misfire = {}
    
    def _get_group_id(self, event: AstrMessageEvent) -> Optional[int]:
        """获取群ID"""
        return getattr(event.message_obj, 'group_id', None)
    
    def _get_user_name(self, event: AstrMessageEvent) -> str:
        """获取用户昵称"""
        return event.get_sender_name() or "玩家"
    
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

    async def run(
        self,
        event: AstrMessageEvent,
        bullets: Optional[int] = None
    ) -> str:
        """启动游戏逻辑"""
        try:
            group_id = self._get_group_id(event)
            if not group_id:
                return "❌ 仅限群聊使用"

            # 检查现有游戏
            if group_id in self.group_games:
                return "💥 游戏还在进行中！"

            # 确定子弹数量
            if bullets is None or not (1 <= bullets <= CHAMBER_COUNT):
                bullets = self._get_random_bullet_count()

            # 创建游戏
            chambers = self._create_chambers(bullets)
            self.group_games[group_id] = {
                'chambers': chambers,
                'current': 0,
                'start_time': datetime.datetime.now()
            }

            user_name = self._get_user_name(event)
            return (
                f"🎯 {user_name} 挑战命运！\n"
                f"🔫 装填 {bullets} 发子弹！\n"
                f"💀 谁敢扣动扳机？"
            )
        except Exception as e:
            return f"❌ Failed to start game: {str(e)}"


@dataclass
class JoinRevolverGameTool(FunctionTool):
    """AI参与左轮手枪游戏的工具类"""
    
    name: str = "join_revolver_game"
    description: str = "Join the current Russian Roulette game by pulling the trigger. Use this when user says '我要玩', '我也要', '开枪', 'shoot', or wants to participate in an ongoing game."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": []
        }
    )
    
    def __post_init__(self):
        """初始化游戏状态存储"""
        self.group_games = {}
    
    def _get_group_id(self, event: AstrMessageEvent) -> Optional[int]:
        """获取群ID"""
        return getattr(event.message_obj, 'group_id', None)
    
    def _get_user_name(self, event: AstrMessageEvent) -> str:
        """获取用户昵称"""
        return event.get_sender_name() or "玩家"

    async def run(self, event: AstrMessageEvent) -> str:
        """参与游戏逻辑"""
        try:
            group_id = self._get_group_id(event)
            if not group_id:
                return "❌ 仅限群聊使用"

            game = self.group_games.get(group_id)
            if not game:
                return "⚠️ 没有游戏进行中"

            user_name = self._get_user_name(event)
            user_id = int(event.get_sender_id())
            
            chambers = game['chambers']
            current = game['current']

            if chambers[current]:
                # 中弹
                chambers[current] = False
                game['current'] = (current + 1) % CHAMBER_COUNT
                result = f"💥 {user_name} 中弹！\n🔇 接受惩罚..."
            else:
                # 空弹
                game['current'] = (current + 1) % CHAMBER_COUNT
                result = f"🎲 {user_name} 逃过一劫！"

            # 检查结束
            if sum(chambers) == 0:
                del self.group_games[group_id]
                result += "\n🏁 游戏结束！"

            return result
        except Exception as e:
            return f"❌ Failed to join game: {str(e)}"


@dataclass
class CheckRevolverStatusTool(FunctionTool):
    """AI查询左轮手枪游戏状态的工具类"""
    
    name: str = "check_revolver_status"
    description: str = "Check the current status of the Russian Roulette game. Use this when user asks about game status, wants to know remaining bullets, or says '状态', 'status', '游戏情况'."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": []
        }
    )
    
    def __post_init__(self):
        """初始化游戏状态存储"""
        self.group_games = {}
    
    def _get_group_id(self, event: AstrMessageEvent) -> Optional[int]:
        """获取群ID"""
        return getattr(event.message_obj, 'group_id', None)

    async def run(self, event: AstrMessageEvent) -> str:
        """查询游戏状态逻辑"""
        try:
            group_id = self._get_group_id(event)
            if not group_id:
                return "❌ 仅限群聊使用"

            game = self.group_games.get(group_id)
            if not game:
                return "🔍 没有游戏进行中"

            chambers = game['chambers']
            current = game['current']
            remaining = sum(chambers)
            
            danger = "🔴 危险" if chambers[current] else "🟢 安全"
            
            return (
                f"🔫 游戏进行中\n"
                f"📊 剩余：{remaining}发子弹\n"
                f"🎯 第{current + 1}膛\n"
                f"{danger}"
            )
        except Exception as e:
            return f"❌ Failed to check status: {str(e)}"
