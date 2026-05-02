#!/usr/bin/env python3
"""google_search_plugin v3.x → v4.0.0 配置迁移脚本

唯一变化:section ``[model_config]`` → ``[models]``。
原因:Pydantic v2 把 ``model_config`` 占用为 BaseModel 的元数据属性,
插件在 PluginConfigBase 子类里不能再用这个名字。

用法:
    python scripts/migrate_v3_to_v4.py [config_path]

如不传参数,默认操作脚本所在目录上一级的 ``config.toml``。

幂等;原文件会备份为 ``<name>.toml.v3-backup``(如已存在,加时间戳后缀)。
"""

from __future__ import annotations

import re
import shutil
import sys
import time
from pathlib import Path

# Windows GBK 终端不认 ✓⚠❌ 等 unicode 符号,强制 stdout 用 utf-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

_SECTION_PATTERN = re.compile(r"^\[model_config\]\s*$", re.MULTILINE)
_SECTION_REPLACEMENT = "[models]"


def _resolve_default_config_path() -> Path:
    """脚本默认操作 plugin 根目录下的 config.toml(脚本上一级)。"""
    return Path(__file__).resolve().parent.parent / "config.toml"


def _next_backup_path(target: Path) -> Path:
    """生成不冲突的备份文件名(原 + .v3-backup,冲突则加时间戳)。"""
    candidate = target.with_suffix(target.suffix + ".v3-backup")
    if not candidate.exists():
        return candidate
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return target.with_suffix(target.suffix + f".v3-backup.{timestamp}")


def migrate(target: Path) -> int:
    """执行迁移。返回非零代码表示失败。"""
    if not target.exists():
        print(f"[ERROR] 找不到配置文件: {target}", file=sys.stderr)
        return 1
    if target.is_dir():
        print(f"[ERROR] 路径是目录而非文件: {target}", file=sys.stderr)
        return 1

    text = target.read_text(encoding="utf-8")

    if _SECTION_PATTERN.search(text) is None:
        # 已经是 v4 或本来就没这一节
        if "[models]" in text:
            print(f"[OK] 已是 v4 格式,无需迁移: {target}")
            return 0
        print(
            f"[WARN] 未发现 [model_config] section,也未发现 [models]——可能配置不完整\n"
            f"       路径: {target}",
            file=sys.stderr,
        )
        return 0

    # 备份原文件
    backup = _next_backup_path(target)
    shutil.copy2(target, backup)

    # rename section
    new_text, count = _SECTION_PATTERN.subn(_SECTION_REPLACEMENT, text)
    if count != 1:
        print(
            f"[ERROR] 异常:期望替换 1 次,实际 {count} 次。已回滚,未修改原文件。",
            file=sys.stderr,
        )
        backup.unlink(missing_ok=True)
        return 2

    target.write_text(new_text, encoding="utf-8")
    print(f"[OK] 迁移完成: {target}")
    print(f"     [model_config] -> [models] (替换 {count} 处)")
    print(f"     原文件备份: {backup}")
    print()
    print("下一步:启动 bot 验证插件能正常加载。")
    print("如有问题,可以从备份恢复:")
    print(f"  cp '{backup}' '{target}'")
    return 0


def main() -> int:
    if len(sys.argv) > 2:
        print("用法: python scripts/migrate_v3_to_v4.py [config_path]", file=sys.stderr)
        return 2
    target = Path(sys.argv[1]) if len(sys.argv) == 2 else _resolve_default_config_path()
    return migrate(target.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
