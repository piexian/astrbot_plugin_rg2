"""
插件元数据定义
"""

from dataclasses import dataclass


@dataclass
class StarMetadata:
    """插件的元数据"""

    name: str = "astrbot_plugin_rg2"
    display_name: str = "🔫 左轮手枪对决"
    version: str = "1.0.0"
    author: str = "piexian"
    description: str = (
        "一个刺激的群聊轮盘赌游戏插件，支持管理员装填子弹、用户开枪对决、随机走火等功能"
    )
    repo: str = "https://github.com/piexian/astrbot_plugin_rg2"


# 全局元数据实例
metadata = StarMetadata()
