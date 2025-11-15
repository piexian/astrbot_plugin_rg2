"""
文本管理器 - 从YAML文件或配置加载和管理游戏文本
"""

import yaml
import random
from pathlib import Path
from typing import Dict, List, Optional


class TextManager:
    """管理游戏文本的加载和获取"""

    def __init__(self, yaml_path: str = None, config: Optional[Dict] = None):
        """初始化文本管理器

        Args:
            yaml_path: YAML文件路径，默认为当前目录下的revolver_game_texts.yml
            config: 配置字典，包含自定义文本列表
        """
        if yaml_path is None:
            yaml_path = Path(__file__).parent / "revolver_game_texts.yml"

        self.yaml_path = Path(yaml_path)
        self.config = config or {}
        self.use_config = bool(self.config)
        self.texts: Dict[str, List[str]] = {}

        # 加载文本
        if self.use_config:
            # 使用配置中的自定义文本
            self._load_from_config()
        else:
            # 加载 YAML 文件
            self._load_from_yaml()

    def _load_from_config(self):
        """从配置加载文本"""
        for category in [
            "misfire_descriptions",
            "user_reactions",
            "trigger_descriptions",
            "miss_messages",
            "game_status",
            "load_messages",
            "game_end",
            "timeout",
            "warnings",
            "victory",
            "defeat",
        ]:
            if category in self.config and isinstance(self.config[category], list):
                self.texts[category] = self.config[category]
            else:
                self.texts[category] = []

    def _load_from_yaml(self):
        """从YAML文件加载文本"""
        try:
            if self.yaml_path.exists():
                with open(self.yaml_path, "r", encoding="utf-8") as f:
                    self.texts = yaml.safe_load(f) or {}
            else:
                self.texts = {}
        except Exception as e:
            raise RuntimeError(f"加载文本文件失败: {e}")

    def get_text(self, category: str, **kwargs) -> str:
        """获取指定类别的随机文本

        Args:
            category: 文本类别（如'misfire_descriptions', 'user_reactions'等）
            **kwargs: 格式化参数，如sender_nickname等

        Returns:
            格式化后的文本
        """
        if category not in self.texts or not self.texts[category]:
            return self._get_default_text(category, **kwargs)

        text = random.choice(self.texts[category])

        try:
            return text.format(**kwargs)
        except Exception as e:
            raise ValueError(f"文本格式化失败: {e}")

    def _get_default_text(self, category: str, **kwargs) -> str:
        """获取默认文本"""
        defaults = {
            "misfire_descriptions": "砰！手枪走火！",
            "user_reactions": "{sender_nickname} 浑身一颤",
            "trigger_descriptions": "扳机扣动，枪声炸响",
            "miss_messages": "咔哒！{sender_nickname} 空弹逃生！",
            "game_status": "游戏进行中",
            "load_messages": "{bullet_count}弹上膛，杀机四伏",
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
        """重新加载文本"""
        if self.use_config:
            self._load_from_config()
        else:
            self._load_from_yaml()


# 延迟初始化，等待 config 传入
text_manager = None
