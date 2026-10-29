"""突破探测：
1) 换蜘蛛 UA 看 share 页是否触发 SSR 数据（isSpider 标志）
2) 先拿 cookie（ttwid/uifid）再打 detail 接口
3) dump _SSR_DATA 内容
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from douyin_dl.client import HttpClient, UA_DESKTOP

ID = sys.argv[1] if len(sys.argv) > 1 else "7264095001330829366"

SPIDERS = {
    "baiduspider": "Mozilla/5.0 (compatible; Baiduspider/2.0; +http://www.baidu.com/search/spider.html)",
    "googlebot": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "bingbot": "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "bytespider": "Mozilla/5.0 (Linux; Android 5.0) AppleWebKit/537.36 (KHTML, like Gecko) Mobile Safari/537.36 (compatible; Bytespider; spider-feedback@bytedance.com)",
    "wechat": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.49(0x18003131) NetType/WIFI Language/zh_CN",
    "weibo": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Weibo (iPhone14,3__weibo__14.1.0__iphone__os17.5)",
}

SHARE = f"https://www.iesdouyin.com/share/video/{ID}/"


def probe_spiders() -> None:
    c = HttpClient(delay=0.9)
    print("### 1) 蜘蛛 / 特殊 UA 探测 share 页")
    for name, ua in SPIDERS.items():
        try:
            r = c.get(SHARE, headers={"User-Agent": ua, "Referer": "https://www.douyin.com/"})
        except Exception as e:  # noqa: BLE001
            print(f"  {name:12} 异常 {e}")
            continue
        txt = r.text
        spider = re.search(r'"isSpider":\s*(true|false)', txt)
        has_res = "videoInfoRes" in txt
        ids = re.findall(r'"aweme_id":"?(\d{15,25})', txt)
        plays = re.findall(r'https?://[^"\\\s]*?(?:douyinvod|aweme\.snssdk\.com\/aweme)[^"\\\s]{10,}', txt)
        print(
            f"  {name:12} len={len(r.content):7} isSpider={spider.group(1) if spider else '?':5} "
            f"videoInfoRes={has_res} ids={ids[:2]} plays={len(plays)}"
        )


def probe_cookie_flow() -> None:
    print("\n### 2) 先取 cookie 再打 detail")
    c = HttpClient(delay=1.0)
    try:
        r = c.get("https://www.douyin.com/", headers={"User-Agent": UA_DESKTOP})
        print(f"  首页 status={r.status_code} len={len(r.content)}")
        print(f"  cookies: {dict(c.session.cookies)}")
        print(f"  set-cookie 头: {r.headers.get('set-cookie', '')[:300]}")
    except Exception as e:  # noqa: BLE001
        print(f"  首页异常: {e}")

    # ttwid 注册接口
    try:
        r = c.session.post(
            "https://ttwid.bytedance.com/ttwid/union/register/",
            json={
                "region": "cn",
                "aid": 1768,
                "needFid": False,
                "service": "www.ixigua.com",
                "migrate_info": {"ticket": "", "source": "node"},
                "cbUrlProtocol": "https",
                "union": True,
            },
            timeout=10,
        )
        print(f"  ttwid 注册 status={r.status_code} len={len(r.content)}")
        print(f"  resp: {r.text[:200]!r}")
        print(f"  cookies2: {dict(c.session.cookies)}")
    except Exception as e:  # noqa: BLE001
        print(f"  ttwid 注册异常: {e}")

    # 带 cookie 打 detail
    for host in ("https://www.douyin.com", "https://www.iesdouyin.com"):
        url = f"{host}/aweme/v1/web/aweme/detail/"
        try:
            r = c.get(
                url,
                params={"device_platform": "webapp", "aid": "6383", "aweme_id": ID,
                        "channel": "channel_pc_web", "pc_client_type": "1",
                        "version_code": "190500", "version_name": "19.5.0",
                        "cookie_enabled": "true", "platform": "PC"},
                headers={"User-Agent": UA_DESKTOP, "Referer": f"https://www.douyin.com/video/{ID}",
                         "Accept": "application/json, text/plain, */*"},
            )
            print(f"  {host} status={r.status_code} len={len(r.content)}")
            print(f"    body: {r.text[:200]!r}")
        except Exception as e:  # noqa: BLE001
            print(f"  {host} 异常 {e}")


def probe_ssr_data() -> None:
    print("\n### 3) _SSR_DATA 内容")
    c = HttpClient(delay=0.5)
    r = c.get(SHARE, headers={"Referer": "https://www.douyin.com/"})
    txt = r.text
    for marker in ("_SSR_DATA", "_ROUTER_DATA"):
        i = txt.find(marker)
        if i < 0:
            print(f"  {marker}: 未出现")
            continue
        print(f"  {marker} @ {i}: ...{txt[i:i+700]}...")
        print()


if __name__ == "__main__":
    probe_spiders()
    probe_cookie_flow()
    probe_ssr_data()
