"""把 douyin-download skill 安装到 agent 的 skills 目录。

安装做的事：
1. 复制 skill/ 到 <skills_dir>/douyin-download/
2. 在 ~/.douyin_dl/install.json 记录项目根（skill 靠它找到项目本体）
3. 跑一次 check_env 验证装上就能用

用法::

    python tools/install_skill.py                    # 自动选目录
    python tools/install_skill.py --target DIR       # 指定 skills 目录
    python tools/install_skill.py --uninstall        # 卸载
    python tools/install_skill.py --list             # 只看装在哪
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SKILL_SRC = PROJECT / "skill"
SKILL_NAME = "douyin-download"
REGISTRY = Path.home() / ".douyin_dl" / "install.json"

# skills 目录，按优先级。
# DSH 的扫描根（见 @deepseek-ai/dsh-skill-filesystem）：
#   <project>/.dsh/skills, <project>/.agents/skills,
#   ~/.dsh/skills,         ~/.agents/skills
# 后面两个是 Claude Code 的惯例，一并支持。
CANDIDATES = [
    Path.home() / ".dsh" / "skills",
    Path.home() / ".agents" / "skills",
    Path.home() / ".claude" / "skills",
]


def pick_target(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    # 优先 DSH 自己的用户级目录：~/.dsh/skills（source=user-dsh，优先级最高）
    dsh_home = Path.home() / ".dsh"
    if dsh_home.is_dir():
        t = dsh_home / "skills"
        t.mkdir(parents=True, exist_ok=True)
        return t
    for c in CANDIDATES:
        if c.is_dir():
            return c
    CANDIDATES[0].mkdir(parents=True, exist_ok=True)
    return CANDIDATES[0]


def do_install(target: Path) -> int:
    if not SKILL_SRC.is_dir():
        print(f"✗ 找不到 skill 源目录: {SKILL_SRC}")
        return 1

    dest = target / SKILL_NAME
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SKILL_SRC, dest)

    # 记录项目根，skill 的脚本靠这个定位
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(
        json.dumps({"root": str(PROJECT.resolve())}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"✓ skill 已安装: {dest}")
    print(f"✓ 项目根已记录: {REGISTRY}")

    # 装完立刻验证一次
    chk = dest / "scripts" / "check_env.py"
    if chk.is_file():
        print("\n验证:")
        try:
            r = subprocess.run(
                [sys.executable, str(chk), "--json", "--no-network"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=120,
            )
            data = json.loads(r.stdout or "{}")
            print(f"  ready={data.get('ready')}  python={data.get('python', {}).get('version')}")
            deps = data.get("deps", {})
            ok_deps = [k for k, v in deps.items() if v.get("ok")]
            print(f"  依赖 {len(ok_deps)}/{len(deps)} 可用: {', '.join(ok_deps)}")
            issues = data.get("issues", [])
            if issues:
                print("  待处理:")
                for i in issues:
                    print(f"    · {i}")
            else:
                print("  无阻塞问题")
        except Exception as e:  # noqa: BLE001
            print(f"  验证脚本执行失败: {e}")

    print("\n在 agent 里这样用：")
    print(f'  python "{dest / "scripts" / "agent_dl.py"}" "<链接>" --json')
    print("\n如果 agent 没立刻识别到，重启会话或新建一个对话。")
    return 0


def do_uninstall(target: Path) -> int:
    dest = target / SKILL_NAME
    if dest.exists():
        shutil.rmtree(dest)
        print(f"✓ 已删除 {dest}")
    else:
        print(f"· 没有安装到 {dest}")
    if REGISTRY.is_file():
        REGISTRY.unlink()
        print(f"✓ 已清除 {REGISTRY}")
    return 0


def do_list() -> int:
    print(f"项目根: {PROJECT}")
    print(f"skill 源: {SKILL_SRC}")
    print(f"安装记录: {REGISTRY} {'(存在)' if REGISTRY.is_file() else '(无)'}")
    found = False
    for c in CANDIDATES:
        d = c / SKILL_NAME
        if d.is_dir():
            found = True
            print(f"✓ 已安装: {d}")
    if not found:
        print("· 尚未安装到任何已知 skills 目录")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="安装 douyin-download skill")
    ap.add_argument("--target", help="skills 目录（默认自动探测 ~/.claude/skills）")
    ap.add_argument("--uninstall", action="store_true", help="卸载")
    ap.add_argument("--list", action="store_true", help="查看安装状态")
    args = ap.parse_args()

    if args.list:
        return do_list()

    target = pick_target(args.target)
    print(f"skills 目录: {target}\n")
    if args.uninstall:
        return do_uninstall(target)
    return do_install(target)


if __name__ == "__main__":
    sys.exit(main())
