"""一次性登录：启动有头浏览器，等你扫码，登录态存进 profile 目录。

用法::

    python tools/login.py [profile目录]

之后所有解析加 --profile <同一个目录> 就复用登录态。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from douyin_dl.session import BrowserSession


def main() -> int:
    profile = sys.argv[1] if len(sys.argv) > 1 else str(
        Path(__file__).resolve().parents[1] / "chrome-profile"
    )
    Path(profile).mkdir(parents=True, exist_ok=True)

    print("=" * 56)
    print("  抖音登录（只需做一次）")
    print("=" * 56)
    print(f"  profile: {profile}")
    print()
    print("  浏览器即将打开抖音，请在弹出的窗口里扫码登录。")
    print("  登录成功后回到这里按回车。")
    print()

    # headless=False 才有窗口；user_data_dir 让 cookie 落盘
    with BrowserSession(headless=False, user_data_dir=profile) as s:
        cookies = s.export_cookies()
        names = {c["name"] for c in cookies}
        print(f"  当前 cookie {len(cookies)} 个")
        print(f"  已登录标志 (sessionid): {'有' if 'sessionid' in names else '无'}")
        print(f"  风控 cookie (UIFID_TEMP): {'有' if 'UIFID_TEMP' in names else '无'}")
        print()
        input("  在浏览器里登录完成后，按回车保存并退出 >>> ")

    print()
    print("  完成。以后用：")
    print(f'    python -m douyin_dl dl "链接" --profile "{profile}"')
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
