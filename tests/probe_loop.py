"""完整闭环验证：
1) 浏览器开抖音首页，拦截推荐 feed 拿到真实 aweme_id
2) 导出 cookie，用纯 requests 打 detail 接口（验证能否脱离浏览器）
3) 打印视频地址
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
ARGS = ["--disable-blink-features=AutomationControlled", "--no-first-run",
        "--no-default-browser-check", "--disable-infobars"]
STEALTH = """
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
window.chrome = window.chrome || {runtime:{}};
"""


def extract_ids(obj, out, depth=0):
    """递归找 aweme_id。"""
    if depth > 14 or len(out) > 40:
        return
    if isinstance(obj, dict):
        v = obj.get("aweme_id")
        if isinstance(v, str) and v.isdigit() and len(v) >= 15:
            out.add(v)
        for vv in obj.values():
            extract_ids(vv, out, depth + 1)
    elif isinstance(obj, list):
        for vv in obj:
            extract_ids(vv, out, depth + 1)


def main() -> None:
    found: set[str] = set()
    feed_samples = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome", args=ARGS)
        ctx = browser.new_context(
            user_agent=UA, locale="zh-CN", timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 900},
        )
        ctx.add_init_script(STEALTH)
        page = ctx.new_page()

        def on_resp(resp):
            u = resp.url
            if "/aweme/v1/web/" in u and "detail" not in u:
                try:
                    data = resp.json()
                except Exception:  # noqa: BLE001
                    return
                before = len(found)
                extract_ids(data, found)
                if len(found) > before and len(feed_samples) < 2:
                    feed_samples.append(u[:100])

        page.on("response", on_resp)

        print(">>> 打开抖音首页，收集推荐流 ID")
        page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=60000)
        for _ in range(12):
            page.wait_for_timeout(1000)
            if len(found) >= 3:
                break
        print(f"    收集到 {len(found)} 个 aweme_id: {list(found)[:6]}")
        if feed_samples:
            print(f"    feed 接口样例: {feed_samples[0]}")

        cookies = ctx.cookies()
        browser.close()

    if not found:
        print("!! 没拿到 ID，无法继续验证")
        return

    Path("browser_cookies.json").write_text(
        json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"    已保存 {len(cookies)} 个 cookie")

    # ---------- 用纯 requests 验证 ----------
    print("\n>>> 纯 requests 打 detail（复用浏览器 cookie）")
    import requests

    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": "https://www.douyin.com/",
                      "Accept": "application/json, text/plain, */*"})
    for c in cookies:
        s.cookies.set(c["name"], c["value"], domain=c.get("domain", "").lstrip(".") or None)

    for aweme_id in list(found)[:3]:
        params = {
            "device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
            "aweme_id": aweme_id, "pc_client_type": "1", "version_code": "190500",
            "version_name": "19.5.0", "cookie_enabled": "true", "platform": "PC",
            "browser_language": "zh-CN", "browser_platform": "Win32",
            "browser_name": "Chrome", "browser_version": "131.0.0.0",
        }
        try:
            r = s.get("https://www.douyin.com/aweme/v1/web/aweme/detail/",
                      params=params, timeout=15)
            j = r.json()
            detail = j.get("aweme_detail")
            if detail:
                video = detail.get("video") or {}
                pa = video.get("play_addr") or {}
                urls = pa.get("url_list") or []
                print(f"  [{aweme_id}] OK  desc={str(detail.get('desc'))[:40]!r}")
                print(f"      作者: {(detail.get('author') or {}).get('nickname')}")
                print(f"      时长: {video.get('duration')} ms")
                print(f"      播放地址: {urls[0][:120] if urls else '无'}")
                print(f"      bit_rate 档数: {len(video.get('bit_rate') or [])}")
                Path(f"detail_{aweme_id}.json").write_text(
                    json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            else:
                fd = j.get("filter_detail") or {}
                print(f"  [{aweme_id}] 无数据 status={j.get('status_code')} "
                      f"reason={fd.get('filter_reason')} msg={fd.get('detail_msg')!r}")
        except Exception as e:  # noqa: BLE001
            print(f"  [{aweme_id}] 异常 {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
