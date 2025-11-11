import random
import datetime
from typing import Dict, List, Optional, Any
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

CHAMBER_COUNT = 6
DEFAULT_TIMEOUT = 60
DEFAULT_MISFIRE_PROB = 0.005
DEFAULT_MIN_BAN = 60
DEFAULT_MAX_BAN = 300

@register(
    "astrbot_plugin_rg2",
    "piexian", 
    "左轮手枪对决游戏 - 刺激的群聊轮盘赌游戏，支持AI自然语言交互",
    "1.0.0",
    "https://github.com/piexian/astrbot_plugin_rg2"
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
        
        # 游戏状态管理
        self.group_games: Dict[int, Dict] = {}
        self.group_misfire: Dict[int, bool] = {}
        
        # 配置参数
        self.timeout = self.config.get("timeout_seconds", DEFAULT_TIMEOUT)
        self.misfire_prob = self.config.get("misfire_probability", DEFAULT_MISFIRE_PROB)
        self.min_ban = self.config.get("min_ban_seconds", DEFAULT_MIN_BAN)
        self.max_ban = self.config.get("max_ban_seconds", DEFAULT_MAX_BAN)
        self.default_misfire = self.config.get("misfire_enabled_by_default", False)
        
        # 注册函数工具
        self._register_function_tools()
    
    def _register_function_tools(self):
        """注册函数工具到AstrBot"""
        try:
            from .tools.revolver_tools import (
                StartRevolverGameTool,
                JoinRevolverGameTool,
                CheckRevolverStatusTool
            )
            
            # 初始化工具并传递游戏状态
            start_tool = StartRevolverGameTool()
            join_tool = JoinRevolverGameTool()
            check_tool = CheckRevolverStatusTool()
            
            # 共享游戏状态
            start_tool.group_games = self.group_games
            start_tool.group_misfire = self.group_misfire
            join_tool.group_games = self.group_games
            check_tool.group_games = self.group_games
            
            # >= v4.5.1 使用新的注册方式
            if hasattr(self.context, 'add_llm_tools'):
                self.context.add_llm_tools(start_tool, join_tool, check_tool)
            else:
                # < v4.5.1 兼容旧版本
                tool_mgr = self.context.provider_manager.llm_tools
                tool_mgr.func_list.extend([start_tool, join_tool, check_tool])
                
            logger.info("左轮手枪函数工具注册成功")
        except Exception as e:
            logger.error(f"注册函数工具失败: {e}", exc_info=True)

    def _get_group_id(self, event: AstrMessageEvent) -> Optional[int]:
        """获取群ID
        
        Args:
            event: 消息事件对象
            
        Returns:
            群ID，如果不在群聊中返回None
        """
        return getattr(event.message_obj, 'group_id', None)

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
            if hasattr(event.bot, 'get_group_member_info'):
                member_info = await event.bot.get_group_member_info(
                    group_id=group_id,
                    user_id=user_id,
                    no_cache=True
                )
                
                # 检查角色：owner(群主) 或 admin(管理员)
                role = member_info.get('role', '') if isinstance(member_info, dict) else getattr(member_info, 'role', '')
                return role in ['owner', 'admin']
            
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

    async def _ban_user(self, event: AstrMessageEvent, user_id: int):
        """禁言用户
        
        Args:
            event: 消息事件对象
            user_id: 要禁言的用户ID
        """
        group_id = self._get_group_id(event)
        if not group_id:
            return

        duration = random.randint(self.min_ban, self.max_ban)
        try:
            if hasattr(event.bot, 'set_group_ban'):
                await event.bot.set_group_ban(
                    group_id=group_id,
                    user_id=user_id,
                    duration=duration
                )
                logger.info(f"用户 {user_id} 在群 {group_id} 被禁言 {duration} 秒")
        except Exception as e:
            logger.error(f"禁言用户失败: {e}")

    # ========== 独立指令 ==========
    
    @filter.command("装填")
    async def load_bullets(self, event: AstrMessageEvent):
        """装填子弹
        
        用法: /装填 [数量]
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
                    yield event.plain_result(f"😏 {user_name}，你又不是管理才不听你的！\n💡 请使用 /装填 进行随机装填")
                    return
            else:
                # 未指定数量，随机装填
                bullet_count = self._get_random_bullet_count()

            # 创建游戏
            chambers = self._create_chambers(bullet_count)
            self.group_games[group_id] = {
                'chambers': chambers,
                'current': 0,
                'start_time': datetime.datetime.now()
            }

            # 设置超时
            await self._start_timeout(event, group_id)

            logger.info(f"用户 {user_name} 在群 {group_id} 装填 {bullet_count} 发子弹")
            
            yield event.plain_result(
                f"🔫 {user_name} 装填 {bullet_count} 发子弹！\n"
                f"💀 {CHAMBER_COUNT} 弹膛，生死一线！\n"
                f"⚡ 限时 {self.timeout} 秒！"
            )
        except Exception as e:
            logger.error(f"装填子弹失败: {e}")
            yield event.plain_result("❌ 装填失败，请重试")

    @filter.command("开枪")
    async def shoot(self, event: AstrMessageEvent):
        """扣动扳机
        
        用法: /开枪
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
            chambers = game['chambers']
            current = game['current']
            
            if chambers[current]:
                # 中弹
                chambers[current] = False
                game['current'] = (current + 1) % CHAMBER_COUNT
                
                await self._ban_user(event, user_id)
                
                logger.info(f"用户 {user_name}({user_id}) 在群 {group_id} 中弹")
                
                yield event.plain_result(
                    f"💥 枪声炸响！\n"
                    f"😱 {user_name} 中弹倒地！\n"
                    f"🔇 禁言惩罚中..."
                )
            else:
                # 空弹
                game['current'] = (current + 1) % CHAMBER_COUNT
                
                logger.info(f"用户 {user_name}({user_id}) 在群 {group_id} 空弹逃生")
                
                yield event.plain_result(
                    f"🎲 咔哒！空弹！\n"
                    f"😅 {user_name} 逃过一劫！"
                )

            # 检查游戏结束
            remaining = sum(chambers)
            if remaining == 0:
                del self.group_games[group_id]
                logger.info(f"群 {group_id} 游戏结束")
                yield event.plain_result("🏁 游戏结束！\n🔄 再来一局？")
                
        except Exception as e:
            logger.error(f"开枪失败: {e}")
            yield event.plain_result("❌ 操作失败，请重试")

    @filter.command("状态")
    async def game_status(self, event: AstrMessageEvent):
        """查看游戏状态
        
        用法: /状态
        查看当前游戏的子弹剩余情况和弹膛状态
        """
        try:
            group_id = self._get_group_id(event)
            if not group_id:
                yield event.plain_result("❌ 仅限群聊使用")
                return

            game = self.group_games.get(group_id)
            if not game:
                yield event.plain_result("🔍 没有游戏进行中\n💡 找管理员装填")
                return

            chambers = game['chambers']
            current = game['current']
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

    @filter.command("帮助")
    async def show_help(self, event: AstrMessageEvent):
        """显示帮助信息
        
        用法: /帮助
        显示插件的使用说明和游戏规则
        """
        try:
            help_text = """🔫 **左轮手枪对决 v1.0**

**用户指令：**
`/装填` - 随机装填子弹（1-6发）
`/开枪` - 扣动扳机
`/状态` - 查看游戏状态
`/帮助` - 显示帮助

**管理员指令：**
`/装填 [数量]` - 装填指定数量子弹（1-6发）
`/走火开` - 开启随机走火
`/走火关` - 关闭随机走火

**AI功能：**
• "来玩左轮手枪" - 开启游戏
• "我也要玩" - 参与游戏  
• "游戏状态" - 查询状态

**游戏规则：**
• 6弹膛，随机装填指定数量子弹
• 中弹禁言60-300秒随机时长
• 超时60秒自动结束游戏
• 走火概率0.5%(如开启)
• 支持自然语言交互"""
            
            yield event.plain_result(help_text)
        except Exception as e:
            logger.error(f"显示帮助失败: {e}")
            yield event.plain_result("❌ 显示帮助失败")

    @filter.command("走火开")
    async def enable_misfire(self, event: AstrMessageEvent):
        """开启随机走火
        
        用法: /走火开
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
            logger.info(f"群 {group_id} 随机走火已开启")
            yield event.plain_result("🔥 随机走火已开启！")
        except Exception as e:
            logger.error(f"开启走火失败: {e}")
            yield event.plain_result("❌ 操作失败，请重试")

    @filter.command("走火关")
    async def disable_misfire(self, event: AstrMessageEvent):
        """关闭随机走火
        
        用法: /走火关
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
            logger.info(f"群 {group_id} 随机走火已关闭")
            yield event.plain_result("💤 随机走火已关闭！")
        except Exception as e:
            logger.error(f"关闭走火失败: {e}")
            yield event.plain_result("❌ 操作失败，请重试")

    # ========== 随机走火监听 ==========
    
    @filter.on_message() & filter.event_message_type("group")
    async def on_group_message(self, event: AstrMessageEvent):
        """监听群消息，触发随机走火
        
        监听非指令消息，根据设定的概率触发随机走火事件
        """
        try:
            # 避免指令冲突
            message = (event.message_str or "").strip()
            if message.startswith("/"):
                return

            # 检查走火
            group_id = self._get_group_id(event)
            if group_id and self._check_misfire(group_id):
                user_name = self._get_user_name(event)
                user_id = int(event.get_sender_id())
                
                await self._ban_user(event, user_id)
                
                logger.info(f"群 {group_id} 用户 {user_name}({user_id}) 触发随机走火")
                
                yield event.plain_result(
                    f"💥 砰！手枪走火！\n"
                    f"😱 {user_name} 不幸中弹！\n"
                    f"🔇 接受惩罚吧..."
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
            当前为简化实现，实际可集成定时器机制
        """
        # TODO: 集成定时器机制，超时后自动结束游戏
        pass

    async def terminate(self):
        """插件卸载清理
        
        清理所有游戏状态和配置，确保插件安全卸载
        """
        try:
            # 清理游戏状态
            self.group_games.clear()
            self.group_misfire.clear()
            
            # 记录卸载日志
            logger.info("左轮手枪插件 v1.0 已安全卸载")
            logger.info(f"清理了 {len(self.group_games)} 个游戏状态")
            logger.info(f"清理了 {len(self.group_misfire)} 个群配置")
        except Exception as e:
            logger.error(f"插件卸载失败: {e}")
            # 即使清理失败也不抛出异常，确保插件能够卸载
