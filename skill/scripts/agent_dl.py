#!/usr/bin/env python
"""Skill 入口 —— 抖音解析下载，结果以 JSON 输出到 stdout。

这个文件只是薄封装：找到项目根、把参数转给 douyin_dl.cli 的 agent 子命令。
真正逻辑在 douyin_dl/ 里。

用法::

    python scripts/agent_dl.py "<分享链接>" --json
    python scripts/agent_dl.py "<链接>" --json --quality 720p --music -o D:/videos
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def find_project_root() -> Path | None:
    """按优先级找项目根：环境变量 -> 逐级上溯找 douyin_dl 包。"""
    env = os.environ.get("DOUYIN_DL_ROOT")
    if env:
        p = Path(env).expanduser()
        if (p / "douyin_dl" / "parser.py").is_file():
            return p

    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "douyin_dl" / "parser.py").is_file():
            return parent
        if (parent / "parser.py").is_file() and parent.name == "douyin_dl":
            return parent.parent
    return None


def fail(code: str, message: str, hint: str = "") -> int:
    print(
        json.dumps(
            {"ok": False, "code": code, "error": message, "hint": hint},
            ensure_ascii=False,
        )
    )
    return 2


def main() -> int:
    root = find_project_root()
    if root is not None and str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        from douyin_dl.cli import main as cli_main
    except ImportError as e:
        return fail(
            "ENV",
            f"找不到 douyin_dl 包: {e}",
            "设置 DOUYIN_DL_ROOT 指向项目根，或先 pip install -e .",
        )

    argv = sys.argv[1:]
    if not argv:
        return fail("USAGE", "缺少链接参数", '用法: agent_dl.py "<链接>" --json')

    # 默认就是 JSON 模式；用户显式 --text 时才走人类可读
    return cli_main(["agent"] + argv)


if __name__ == "__main__":
    sys.exit(main())
