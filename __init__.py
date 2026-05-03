"""google_search_plugin 包标识。

Runner 通过 _manifest.json 发现插件,通过 plugin.py 的 create_plugin() 工厂
实例化。本文件存在仅为支持 ``from .config import ...`` 等相对导入
(以及 tests/ 下用 ``from google_search_plugin.pipelines.x import y`` 测试)。
"""
