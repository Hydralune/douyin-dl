"""定位 douyin-dl 项目根目录。

skill 被安装到 ~/.claude/skills/ 之后，脚本和项目本体就不在一起了，
所以按优先级多渠道查找：

1. 环境变量 DOUYIN_DL_ROOT
2. 安装记录 ~/.douyin_dl/install.json（由 tools/install_skill.py 写入）
3. 从本文件逐级向上（开发态：项目内 skill/scripts/）
4. 常见位置
5. 已 pip install 的情况
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _valid(p: Path) -> bool:
    try:
        return (p / "douyin_dl" / "parser.py").is_file()
    except Exception:  # noqa: BLE001
        return False


def find_root() -> Path | None:
    # 1. 环境变量
    env = os.environ.get("DOUYIN_DL_ROOT")
    if env:
        p = Path(env).expanduser()
        if _valid(p):
            return p.resolve()

    # 2. 安装记录
    reg = Path.home() / ".douyin_dl" / "install.json"
    if reg.is_file():
        try:
            data = json.loads(reg.read_text(encoding="utf-8"))
            p = Path(str(data.get("root", ""))).expanduser()
            if str(p) and _valid(p):
                return p.resolve()
        except Exception:  # noqa: BLE001
            pass

    # 3. 向上找（项目内开发态）
    here = Path(__file__).resolve()
    for parent in here.parents:
        if _valid(parent):
            return parent

    # 4. 常见位置
    for cand in (
        Path.home() / "douyin-dl",
        Path.home() / ".dsh" / "profiles" / "desktop" / "douyin-dl",
        Path.cwd() / "douyin-dl",
        Path.cwd(),
    ):
        if _valid(cand):
            return cand.resolve()

    # 5. 已安装为包
    try:
        import douyin_dl  # noqa: F401

        pkg = Path(douyin_dl.__file__).resolve().parent  # .../douyin_dl
        if _valid(pkg.parent):
            return pkg.parent
    except Exception:  # noqa: BLE001
        pass
    return None


def ensure_on_path() -> Path | None:
    """把项目根加进 sys.path，返回根路径（找不到返回 None）。"""
    root = find_root()
    if root is not None and str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def register(root: Path) -> Path:
    """写入安装记录（install_skill.py 用）。"""
    reg = Path.home() / ".douyin_dl" / "install.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(
        json.dumps({"root": str(root.resolve())}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return reg
