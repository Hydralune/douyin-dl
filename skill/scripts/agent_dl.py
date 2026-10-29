#!/usr/bin/env python
"""Skill 入口 —— 抖音解析下载，结果以 JSON 输出到 stdout。

薄封装：定位项目根，把参数转给 douyin_dl.cli 的 agent 子命令。

用法::

    python scripts/agent_dl.py "<分享链接>" --json
    python scripts/agent_dl.py "<链接>" --json --quality 720p --music -o D:/videos
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from _root import ensure_on_path
except ImportError:  # 兼容被当作包导入的情况
    from ._root import ensure_on_path  # type: ignore[no-redef]


def fail(code: str, message: str, hint: str = "") -> int:
    print(json.dumps({"ok": False, "code": code, "error": message, "hint": hint},
                     ensure_ascii=False))
    return 2


def main() -> int:
    root = ensure_on_path()
    if root is None:
        return fail(
            "ENV",
            "找不到 douyin-dl 项目根目录",
            "设置环境变量 DOUYIN_DL_ROOT 指向项目，或先跑 python tools/install_skill.py",
        )

    try:
        from douyin_dl.cli import main as cli_main
    except ImportError as e:
        return fail("ENV", f"导入 douyin_dl 失败: {e}", f"项目根: {root}")

    argv = sys.argv[1:]
    if not argv:
        return fail("USAGE", "缺少链接参数", '用法: agent_dl.py "<链接>" --json')

    return cli_main(["agent"] + argv)


if __name__ == "__main__":
    sys.exit(main())
