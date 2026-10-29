"""诊断：浏览器发出的 detail 请求完整 URL 是否带 a_bogus，以及 cookie 灌入是否生效。"""
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from douyin_dl.session import DETAIL_API, DETAIL_PARAMS, BrowserSession

ID = sys.argv[1] if len(sys.argv) > 1 else "7700973189773479183"


def main() -> None:
    urls_seen = []

    with BrowserSession() as s:
        page = s._page
        assert page is not None

        def on_req(req):
            if "aweme/detail" in req.url:
                urls_seen.append(req.url)

        page.on("request", on_req)

        print(">>> 触发一次 detail（走页面 fetch）")
        s.fetch_detail(ID)
        page.wait_for_timeout(1500)

        print(f"\n捕获 {len(urls_seen)} 个 detail 请求")
        for u in urls_seen[:3]:
            qs = parse_qs(urlparse(u).query)
            print(f"\n  host={urlparse(u).netloc}")
            print(f"  参数: {sorted(qs.keys())}")
            print(f"  有 a_bogus: {'a_bogus' in qs}")
            print(f"  有 X-Bogus: {'X-Bogus' in qs}")
            if "a_bogus" in qs:
                print(f"  a_bogus={qs['a_bogus'][0][:60]}... (len={len(qs['a_bogus'][0])})")
            print(f"  完整 URL 前 400: {u[:400]}")

        # 导出 cookie，测纯 requests
        cookies = s.export_cookies()
        print(f"\n>>> cookie {len(cookies)} 个")
        key_names = {c["name"] for c in cookies}
        for n in ("ttwid", "UIFID_TEMP", "UIFID", "msToken", "odin_tt",
                  "__ac_signature", "s_v_web_id", "bd_ticket_guard_client_data"):
            print(f"    {n:32} {'有' if n in key_names else '无'}")

        import requests

        rs = requests.Session()
        rs.headers.update({"User-Agent": s._page.context.browser.browser_type.name})
        from douyin_dl.session import UA

        rs.headers.update({"User-Agent": UA, "Referer": "https://www.douyin.com/"})
        ok = 0
        for c in cookies:
            try:
                rs.cookies.set(c["name"], c["value"], domain=c["domain"] or None)
                ok += 1
            except Exception as e:  # noqa: BLE001
                print(f"    ! cookie {c['name']} 灌入失败: {e}")
        print(f"    成功灌入 {ok}/{len(cookies)}")

        r = rs.get(DETAIL_API, params=dict(DETAIL_PARAMS, aweme_id=ID), timeout=15)
        print(f"\n>>> 纯 requests: status={r.status_code} len={len(r.content)}")
        print(f"    body: {r.text[:200]!r}")


if __name__ == "__main__":
    main()
