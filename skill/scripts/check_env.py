#!/usr/bin/env python
"""环境自检 —— 依赖 / 浏览器 / ffmpeg / 登录态 / 网络。

用法::

    python scripts/check_env.py --json
    python scripts/check_env.py            # 人类可读
    python scripts/check_env.py --json --no-network
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


def main() -> int:
    root = ensure_on_path()
    if root is None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "ready": False,
                    "code": "ENV",
                    "error": "找不到 douyin-dl 项目根目录",
                    "hint": "设置 DOUYIN_DL_ROOT，或跑 python tools/install_skill.py",
                },
                ensure_ascii=False,
            )
        )
        return 2

    try:
        from douyin_dl.envcheck import main as env_main
    except ImportError as e:
        print(json.dumps({"ok": False, "ready": False, "code": "ENV",
                          "error": f"导入 douyin_dl 失败: {e}"}, ensure_ascii=False))
        return 2

    return env_main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
