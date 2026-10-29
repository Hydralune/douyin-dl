#!/usr/bin/env python
"""环境自检 —— 依赖 / 浏览器 / ffmpeg / 登录态 / 网络。

用法::

    python scripts/check_env.py --json
    python scripts/check_env.py            # 人类可读
    python scripts/check_env.py --json --no-network
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def find_project_root() -> Path | None:
    env = os.environ.get("DOUYIN_DL_ROOT")
    if env:
        p = Path(env).expanduser()
        if (p / "douyin_dl" / "parser.py").is_file():
            return p
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "douyin_dl" / "parser.py").is_file():
            return parent
    return None


def main() -> int:
    root = find_project_root()
    if root is not None and str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        from douyin_dl.envcheck import main as env_main
    except ImportError as e:
        print(
            json.dumps(
                {"ok": False, "ready": False, "code": "ENV", "error": f"找不到 douyin_dl 包: {e}"},
                ensure_ascii=False,
            )
        )
        return 2
    return env_main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
