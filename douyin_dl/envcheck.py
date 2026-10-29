"""环境自检 —— 输出 JSON，agent 和人都能看。

用于回答："这台机器现在能不能跑？缺什么？登录态在不在？"
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REQUIRED = {
    "requests": "HTTP 请求",
    "playwright": "浏览器自动化（核心，缺了无法解析）",
    "tqdm": "进度条（CLI 用）",
    "rich": "CLI 美化（可选）",
    "PIL": "截图/封面（可选）",
}

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]


def _dep_status() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for mod, desc in REQUIRED.items():
        try:
            m = importlib.import_module(mod)
            ver = getattr(m, "__version__", "") or getattr(m, "VERSION", "")
            out[mod] = {"ok": True, "version": str(ver), "desc": desc}
        except Exception as e:  # noqa: BLE001
            out[mod] = {"ok": False, "error": str(e), "desc": desc}
    return out


def _find_browser() -> Dict[str, Any]:
    found: List[Dict[str, str]] = []
    for p in CHROME_PATHS:
        if Path(p).is_file():
            found.append({"path": p, "kind": "chrome" if "chrome" in p.lower() else "edge"})
    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        exe = shutil.which(name)
        if exe and not any(f["path"] == exe for f in found):
            found.append({"path": exe, "kind": name})

    # playwright 自带 chromium
    pw_chromium = ""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            pw_chromium = p.chromium.executable_path
    except Exception:  # noqa: BLE001
        pass

    return {
        "ok": bool(found) or Path(pw_chromium).is_file() if pw_chromium else bool(found),
        "system": found,
        "playwright_chromium": pw_chromium if pw_chromium and Path(pw_chromium).is_file() else "",
        "note": "" if found else "没有系统 Chrome/Edge，需要跑 python -m playwright install chromium",
    }


def _login_status(profile: str = "") -> Dict[str, Any]:
    cache = Path.home() / ".douyin_dl" / "cookies.json"
    prof = Path(profile).expanduser() if profile else Path.cwd() / "chrome-profile"
    prof_exists = prof.is_dir() and any(prof.iterdir()) if prof.is_dir() else False
    return {
        "profile_dir": str(prof),
        "profile_exists": bool(prof_exists),
        "cookie_cache": str(cache) if cache.is_file() else "",
        "logged_in": bool(cache.is_file()) or bool(prof_exists),
        "note": (
            "已有登录态/会话缓存"
            if (cache.is_file() or prof_exists)
            else "未登录。公开视频不登录也能下（最高 1080p）；"
                 "想要更高或受限内容，跑 python tools/login.py 扫码一次"
        ),
    }


def _network(timeout: float = 6.0) -> Dict[str, Any]:
    res: Dict[str, Any] = {}
    try:
        import requests

        for name, url in (
            ("douyin", "https://www.douyin.com/"),
            ("iesdouyin", "https://www.iesdouyin.com/"),
        ):
            try:
                r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
                res[name] = {"ok": r.status_code < 500, "status": r.status_code}
            except Exception as e:  # noqa: BLE001
                res[name] = {"ok": False, "error": type(e).__name__}
    except Exception as e:  # noqa: BLE001
        res["error"] = str(e)
    return res


def check(profile: str = "", with_network: bool = True) -> Dict[str, Any]:
    deps = _dep_status()
    browser = _find_browser()
    ffmpeg = shutil.which("ffmpeg") or ""
    net = _network() if with_network else {}

    issues: List[str] = []
    for name, st in deps.items():
        if not st.get("ok"):
            issues.append(f"缺少依赖 {name} → pip install {name if name != 'PIL' else 'Pillow'}")
    if not browser["ok"]:
        issues.append("没有可用浏览器 → python -m playwright install chromium")
    if not ffmpeg:
        issues.append("没有 ffmpeg（只影响音视频合并/转码，抖音下载不需要）")
    if with_network:
        for k, v in net.items():
            if isinstance(v, dict) and not v.get("ok"):
                issues.append(f"访问 {k} 失败，检查网络/代理")

    hard_ok = deps.get("requests", {}).get("ok") and deps.get("playwright", {}).get("ok") and browser["ok"]

    return {
        "ok": bool(hard_ok),
        "ready": bool(hard_ok),
        "python": {
            "version": sys.version.split()[0],
            "executable": sys.executable,
            "ok": sys.version_info >= (3, 10),
        },
        "deps": deps,
        "browser": browser,
        "ffmpeg": {"ok": bool(ffmpeg), "path": ffmpeg},
        "login": _login_status(profile),
        "network": net,
        "issues": issues,
        "platform": sys.platform,
    }


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    json_mode = "--json" in argv
    no_net = "--no-network" in argv
    profile = ""
    for i, a in enumerate(argv):
        if a == "--profile" and i + 1 < len(argv):
            profile = argv[i + 1]

    data = check(profile=profile, with_network=not no_net)

    if json_mode:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"Python {data['python']['version']}  ({data['python']['executable']})")
        print("\n依赖:")
        for name, st in data["deps"].items():
            mark = "✓" if st.get("ok") else "✗"
            print(f"  {mark} {name:12} {st.get('version', st.get('error', ''))}")
        print("\n浏览器:")
        for b in data["browser"]["system"]:
            print(f"  ✓ {b['kind']:6} {b['path']}")
        if data["browser"]["playwright_chromium"]:
            print(f"  ✓ playwright chromium")
        print(f"\nffmpeg: {'✓ ' + data['ffmpeg']['path'] if data['ffmpeg']['ok'] else '✗ 未安装'}")
        lg = data["login"]
        print(f"\n登录态: {'✓ ' + lg['note'] if lg['logged_in'] else '— ' + lg['note']}")
        if data["network"]:
            print("\n网络:")
            for k, v in data["network"].items():
                if isinstance(v, dict):
                    print(f"  {'✓' if v.get('ok') else '✗'} {k} {v.get('status', v.get('error', ''))}")
        if data["issues"]:
            print("\n待处理:")
            for i in data["issues"]:
                print(f"  · {i}")
        else:
            print("\n一切就绪。")

    return 0 if data["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
