"""多入口抓真实 aweme_id：首页滚动 / 搜索页 / 发现页 / 热榜。"""
import json
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
ARGS = ["--disable-blink-features=AutomationControlled", "--no-first-run",
        "--no-default-browser-check", "--disable-infobars"]
STEALTH = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});window.chrome=window.chrome||{runtime:{}};"

ENTRANCES = [
    ("search", "https://www.douyin.com/search/" + quote("美食")),
    ("discover", "https://www.douyin.com/discover"),
    ("hot", "https://www.douyin.com/hot"),
    ("home", "https://www.douyin.com/"),
]


def extract_ids(obj, out, depth=0):
    if depth > 16 or len(out) > 60:
        return
    if isinstance(obj, dict):
        v = obj.get("aweme_id") or obj.get("awemeId")
        if isinstance(v, str) and v.isdigit() and 15 <= len(v) <= 25:
            out.add(v)
        for vv in obj.values():
            extract_ids(vv, out, depth + 1)
    elif isinstance(obj, list):
        for vv in obj:
            extract_ids(vv, out, depth + 1)


def main() -> None:
    found: set[str] = set()
    api_hits: list[str] = []

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
            if "/aweme/v1/web/" not in u:
                return
            ct = resp.headers.get("content-type", "")
            if "json" not in ct:
                return
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001
                return
            before = len(found)
            extract_ids(data, found)
            if len(found) > before and len(api_hits) < 12:
                short = u.split("?")[0].replace("https://", "")
                if short not in api_hits:
                    api_hits.append(short)

        page.on("response", on_resp)

        for name, url in ENTRANCES:
            if len(found) >= 3:
                break
            print(f">>> 入口 {name}: {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:  # noqa: BLE001
                print(f"    导航失败 {type(e).__name__}: {e}")
                continue
            for i in range(8):
                page.wait_for_timeout(1000)
                try:
                    page.mouse.wheel(0, 1600)
                except Exception:  # noqa: BLE001
                    pass
                if len(found) >= 3:
                    break
            print(f"    累计 {len(found)} 个 id")

        print(f"\n接口命中: {api_hits}")
        print(f"共 {len(found)} 个 id: {list(found)[:10]}")

        cookies = ctx.cookies()
        browser.close()

    Path("browser_cookies.json").write_text(
        json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    Path("real_ids.json").write_text(
        json.dumps(sorted(found), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"已保存 {len(cookies)} cookie / {len(found)} 个 id")
    if found:
        print("\n下一步可用真实 ID 验证:", list(found)[0])


if __name__ == "__main__":
    main()
