"""对真实分享链接跑完整链路：展开 -> 拿 id -> 浏览器解析 -> 打印视频地址。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from douyin_dl.client import HttpClient
from douyin_dl.resolver import expand_short_url, extract_aweme_id

RAW = sys.argv[1] if len(sys.argv) > 1 else "https://v.douyin.com/MnQz35HAVu4/"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
ARGS = ["--disable-blink-features=AutomationControlled", "--no-first-run",
        "--no-default-browser-check", "--disable-infobars"]
STEALTH = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});window.chrome=window.chrome||{runtime:{}};"

DETAIL_JS = """
async (id) => {
  const qs = new URLSearchParams({
    device_platform:'webapp', aid:'6383', channel:'channel_pc_web',
    aweme_id: id, pc_client_type:'1', version_code:'190500',
    version_name:'19.5.0', cookie_enabled:'true', platform:'PC',
    browser_language:'zh-CN', browser_platform:'Win32',
    browser_name:'Chrome', browser_version:'131.0.0.0'
  });
  const r = await fetch('/aweme/v1/web/aweme/detail/?' + qs, {credentials:'include'});
  return {status: r.status, body: await r.text()};
}
"""


def main() -> None:
    c = HttpClient(delay=0.3)

    print(f"输入: {RAW}")
    aweme_id = extract_aweme_id(RAW)
    if not aweme_id:
        real = expand_short_url(RAW, c)
        print(f"短链展开 -> {real}")
        aweme_id = extract_aweme_id(real)
    print(f"aweme_id = {aweme_id}")
    if not aweme_id:
        print("!! 提取失败")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome", args=ARGS)
        ctx = browser.new_context(
            user_agent=UA, locale="zh-CN", timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 900},
        )
        ctx.add_init_script(STEALTH)
        page = ctx.new_page()

        print("\n>>> 建立会话")
        page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        names = [ck["name"] for ck in ctx.cookies()]
        print(f"    cookie: {len(names)} 个, 含 UIFID={'UIFID_TEMP' in names or 'UIFID' in names}")

        print("\n>>> 请求 detail")
        res = page.evaluate(DETAIL_JS, aweme_id)
        print(f"    http={res['status']} len={len(res['body'])}")

        try:
            data = json.loads(res["body"])
        except Exception as e:  # noqa: BLE001
            print(f"    JSON 失败: {e}\n    {res['body'][:400]}")
            browser.close()
            return

        detail = data.get("aweme_detail")
        if not detail:
            fd = data.get("filter_detail") or {}
            print(f"    无数据 status={data.get('status_code')} "
                  f"reason={fd.get('filter_reason')} msg={fd.get('detail_msg')!r}")
            browser.close()
            return

        Path(f"detail_{aweme_id}.json").write_text(
            json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("\n===== 解析成功 =====")
        print(f"  desc      : {detail.get('desc')!r}")
        author = detail.get("author") or {}
        print(f"  author    : {author.get('nickname')} / {author.get('sec_uid')}")
        video = detail.get("video") or {}
        print(f"  duration  : {video.get('duration')} ms")
        print(f"  图集?     : {'是' if detail.get('images') else '否'}")

        pa = video.get("play_addr") or {}
        urls = pa.get("url_list") or []
        print(f"\n  play_addr.uri = {pa.get('uri')}")
        for i, u in enumerate(urls, 1):
            print(f"  url[{i}] {u[:150]}")

        brs = video.get("bit_rate") or []
        print(f"\n  bit_rate 共 {len(brs)} 档:")
        for b in brs:
            addr = b.get("play_addr") or {}
            u = (addr.get("url_list") or [""])[0]
            print(f"    {b.get('gear_name'):22} rate={b.get('bit_rate'):>9} "
                  f"q={b.get('quality_type')} {u[:80]}")

        mu = detail.get("music") or {}
        print(f"\n  music.title = {mu.get('title')}")
        print(f"  music.url   = {str((mu.get('play_url') or {}).get('url_list', ['']))[:120]}")

        # 立刻用真实 UA + referer 试探能否下载（HEAD 请求）
        import requests

        print("\n>>> 试探直链可下载性 (HTTP HEAD)")
        if urls:
            try:
                r = requests.head(urls[0], headers={"User-Agent": UA,
                                                    "Referer": "https://www.douyin.com/"},
                                  allow_redirects=True, timeout=15)
                print(f"    status={r.status_code} type={r.headers.get('content-type')} "
                      f"len={r.headers.get('content-length')}")
            except Exception as e:  # noqa: BLE001
                print(f"    异常 {e}")

        Path("browser_cookies.json").write_text(
            json.dumps(ctx.cookies(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        browser.close()


if __name__ == "__main__":
    main()
