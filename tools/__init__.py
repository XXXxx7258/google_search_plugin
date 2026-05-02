"""tools 子包

注:本包将在阶段 4 重构 — ``abbreviation_tool.py`` 旧 BaseTool 实现会被
迁到 ``plugin.py`` 的 ``@Tool`` handler 后删除;``rewrite_output.py`` 保留为辅助函数。

阶段 3 期间不再 eagerly import ``abbreviation_tool``(它仍在用 ``from src.plugin_system``,
触发 ImportError),改为按需导入。
"""
