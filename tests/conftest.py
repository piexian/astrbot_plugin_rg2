import sys
from pathlib import Path

# 插件包位于 AstrBot 同级目录，加入父目录使 astrbot_plugin_rg2 可作为包导入
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
