"""浏览器方案探测：
A) 页面上下文内 fetch detail 接口（自动带 cookie，无需自己算签名）
B) 打开视频页，拦截页面自己发出的 detail 响应
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ID = sys.argv[1] if len(sys.argv) > 1 else "7264095001330829366"
HEADLESS = "--headed" not in sys.argv

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-infobars",
]

STEALTH = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = window.chrome || {runtime:{}};
"""


def main() -> None:
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=HEADLESS, channel="chrome", args=ARGS)
            print("    [使用系统 Chrome]")
        except Exception:
            browser = p.chromium.launch(headless=HEADLESS, args=ARGS)
        ctx = browser.new_context(
            user_agent=UA,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 900},
        )
        ctx.add_init_script(STEALTH)
        page = ctx.new_page()

        hits = []

        def on_resp(resp):
            u = resp.url
            if "aweme/detail" in u or "aweme/post" in u:
                try:
                    hits.append((u, resp.status, resp.text()[:3000]))
                except Exception:  # noqa: BLE001
                    pass

        page.on("response", on_resp)

        print(">>> 打开首页建立会话")
        try:
            page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)
            print(f"    title={page.title()!r}")
            cookies = ctx.cookies()
            print(f"    cookie 数: {len(cookies)} -> {[c['name'] for c in cookies]}")
        except Exception as e:  # noqa: BLE001
            print(f"    首页异常: {type(e).__name__}: {e}")

        # ---------- A: 页面内 fetch ----------
        print("\n>>> A) 页面上下文 fetch detail")
        js = """
        async (id) => {
          const qs = new URLSearchParams({
            device_platform:'webapp', aid:'6383', channel:'channel_pc_web',
            aweme_id: id, pc_client_type:'1', version_code:'190500',
            version_name:'19.5.0', cookie_enabled:'true', platform:'PC',
            browser_language:'zh-CN', browser_platform:'Win32',
            browser_name:'Chrome', browser_version:'131.0.0.0'
          });
          const r = await fetch('/aweme/v1/web/aweme/detail/?' + qs, {credentials:'include'});
          const t = await r.text();
          return {status: r.status, body: t.slice(0, 3000)};
        }
        """
        try:
            res = page.evaluate(js, ID)
            print(f"    status={res['status']}")
            print(f"    body: {res['body'][:400]!r}")
            if res["status"] == 200 and res["body"].lstrip().startswith("{"):
                try:
                    d = json.loads(res["body"] + ("" if res["body"].rstrip().endswith(("}", "]")) else ""))
                except Exception:
                    d = None
        except Exception as e:  # noqa: BLE001
            print(f"    fetch 异常: {type(e).__name__}: {e}")

        # ---------- B: 打开视频页拦截 ----------
        print(f"\n>>> B) 打开视频页 https://www.douyin.com/video/{ID}")
        hits.clear()
        try:
            page.goto(
                f"https://www.douyin.com/video/{ID}",
                wait_until="domcontentloaded",
                timeout=45000,
            )
            for _ in range(15):
                page.wait_for_timeout(1000)
                if hits:
                    break
            print(f"    拦截到 {len(hits)} 个 aweme 接口响应")
            for u, st, body in hits[:3]:
                print(f"    - {st} {u[:110]}")
                print(f"      {body[:300]!r}")
            print(f"    title={page.title()!r}")
            txt = page.content()
            print(f"    页面长度 {len(txt)}，含 aweme_id: {'aweme_id' in txt}")
            for kw in ("登录", "验证", "captcha", "滑块"):
                if kw in txt:
                    print(f"    页面出现关键词: {kw}")
        except Exception as e:  # noqa: BLE001
            print(f"    视频页异常: {type(e).__name__}: {e}")

        # 保存 cookie 供纯 HTTP 复用
        Path("browser_cookies.json").write_text(
            json.dumps(ctx.cookies(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("\n已保存 browser_cookies.json")
        browser.close()


if __name__ == "__main__":
    main()
