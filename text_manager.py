"""
文本管理器 - 从YAML文件加载和管理游戏文本
"""

import yaml
import random
from pathlib import Path
from typing import Dict, List


class TextManager:
    """管理游戏文本的加载和获取"""

    def __init__(self, yaml_path: str = None):
        """初始化文本管理器

        Args:
            yaml_path: YAML文件路径，默认为当前目录下的revolver_game_texts.yml
        """
        if yaml_path is None:
            yaml_path = Path(__file__).parent / "revolver_game_texts.yml"

        self.yaml_path = Path(yaml_path)
        self.texts: Dict[str, List[str]] = {}
        self._load_texts()

    def _load_texts(self):
        """加载YAML文本文件"""
        try:
            if self.yaml_path.exists():
                with open(self.yaml_path, "r", encoding="utf-8") as f:
                    self.texts = yaml.safe_load(f) or {}
            else:
                self.texts = {}
        except Exception as e:
            print(f"加载文本文件失败: {e}")
            self.texts = {}

    def get_text(self, category: str, **kwargs) -> str:
        """获取指定类别的随机文本

        Args:
            category: 文本类别（如'misfire_descriptions', 'user_reactions'等）
            **kwargs: 格式化参数，如sender_nickname等

        Returns:
            格式化后的文本
        """
        if category not in self.texts or not self.texts[category]:
            # 返回默认文本
            return self._get_default_text(category, **kwargs)

        # 随机选择一条文本
        text = random.choice(self.texts[category])

        # 格式化文本
        try:
            return text.format(**kwargs)
        except Exception as e:
            print(f"文本格式化失败: {e}")
            return text

    def _get_default_text(self, category: str, **kwargs) -> str:
        """获取默认文本（当YAML文件加载失败或类别不存在时使用）

        Args:
            category: 文本类别
            **kwargs: 格式化参数

        Returns:
            默认文本
        """
        defaults = {
            "misfire_descriptions": "砰！手枪走火！",
            "user_reactions": "{sender_nickname} 浑身一颤",
            "trigger_descriptions": "扳机扣动，枪声炸响",
            "miss_messages": "咔哒！{sender_nickname} 空弹逃生！",
            "game_status": "游戏进行中",
            "load_messages": "子弹上膛，杀机四伏",
            "game_end": "游戏结束，胜负已分",
            "timeout": "时间到！游戏结束",
            "warnings": "警告：极度危险！",
            "victory": "恭喜！你活下来了！",
            "defeat": "接受惩罚吧！",
        }

        text = defaults.get(category, "")
        try:
            return text.format(**kwargs)
        except Exception:
            return text

    def reload_texts(self):
        """重新加载文本文件"""
        self._load_texts()


# 全局文本管理器实例
text_manager = TextManager()
