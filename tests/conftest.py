"""pytest 配置:把 plugins/ 目录加入 sys.path,使 ``google_search_plugin`` 成为
可导入的顶层包。
"""

from __future__ import annotations

import sys
from pathlib import Path

# tests/ 在 plugin 根下;plugin 根的父目录是 plugins/
_PLUGIN_PARENT = Path(__file__).resolve().parent.parent.parent
if str(_PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_PARENT))
