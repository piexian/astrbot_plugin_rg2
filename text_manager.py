"""
文本管理器 - 从 YAML 文件加载和管理游戏文本

设计说明：
- 所有自定义文本都通过 revolver_game_texts.yml 配置
- YAML 顶层每个 key 即为一个文本分类，value 为字符串列表
- 用户可在 YAML 中新增任意分类与任意条数，代码自动识别
"""

import random
import yaml
from pathlib import Path
from typing import Dict, List, Optional


# 内置兜底文本：仅在 YAML 缺失对应分类时使用
_DEFAULT_TEXTS: Dict[str, str] = {
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


class TextManager:
    """管理游戏文本的加载和获取"""

    def __init__(
        self, yaml_path: Optional[str] = None, custom_texts: Optional[List[Dict]] = None
    ):
        """初始化文本管理器

        Args:
            yaml_path: YAML 文件路径，默认为插件目录下的 revolver_game_texts.yml
            custom_texts: 来自 _conf_schema.json 中 ``custom_texts`` (template_list) 的值，
                每个元素形如 ``{"__template_key": "victory", "text": "你赢了！"}``。
                同分类下的用户文本会与 YAML 默认文本合并。
        """
        if yaml_path is None:
            yaml_path = Path(__file__).parent / "revolver_game_texts.yml"

        self.yaml_path = Path(yaml_path)
        self.custom_texts: List[Dict] = list(custom_texts or [])
        self.texts: Dict[str, List[str]] = {}
        self._load()

    def _load(self) -> None:
        """从 YAML 与 custom_texts 加载并合并所有文本分类。

        加载失败不会抛异常（只会记日志并回落到空集合），
        以保证 ``get_text`` 始终可用。
        """
        # 1) YAML 默认文本
        self.texts = {}
        try:
            if self.yaml_path and self.yaml_path.exists():
                with open(self.yaml_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                for k, v in data.items():
                    if not isinstance(v, list):
                        continue
                    # 只保留非空字符串，避免下游 .format() 崩溃
                    items = [s for s in v if isinstance(s, str) and s]
                    if items:
                        self.texts[k] = items
        except Exception as e:
            # 记录但不抛出，避免插件启动失败
            print(f"[TextManager] 加载 {self.yaml_path} 失败: {e}")

        # 2) 合并用户自定义 custom_texts（template_list 形式）
        for entry in self.custom_texts:
            if not isinstance(entry, dict):
                continue
            category = entry.get("__template_key")
            text = entry.get("text")
            if not category or not isinstance(text, str) or not text.strip():
                continue
            self.texts.setdefault(category, []).append(text)

    def get_text(self, category: str, **kwargs) -> str:
        """获取指定分类的随机文本

        Args:
            category: 文本分类名（与 YAML 顶层 key 或 custom_texts 中的分类一致）
            **kwargs: 格式化参数（如 sender_nickname、bullet_count 等）

        Returns:
            格式化后的文本；分类缺失时回落到内置默认值
        """
        candidates = self.texts.get(category)
        text = (
            random.choice(candidates)
            if candidates
            else _DEFAULT_TEXTS.get(category, "")
        )

        try:
            return text.format(**kwargs)
        except Exception:
            return text

    def reload_texts(self, custom_texts: Optional[List[Dict]] = None) -> None:
        """重新加载文本

        Args:
            custom_texts: 可选，更新 template_list 形式的用户自定义文本
        """
        if custom_texts is not None:
            self.custom_texts = list(custom_texts)
        self._load()

    @property
    def categories(self) -> List[str]:
        """当前已加载的所有文本分类名"""
        return list(self.texts.keys())


# 延迟初始化的全局引用
text_manager: Optional[TextManager] = None
