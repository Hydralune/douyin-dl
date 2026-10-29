"""dump 各种页面，判断哪条路径还带真实数据。"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from douyin_dl.client import HttpClient, UA_DESKTOP, UA_MOBILE

ID = sys.argv[1] if len(sys.argv) > 1 else "7264095001330829366"

URLS = [
    ("share_video_mobile", f"https://www.iesdouyin.com/share/video/{ID}/", UA_MOBILE),
    ("share_video_q", f"https://www.iesdouyin.com/share/video/{ID}/?region=CN&mid=0&u_code=0&titleType=title", UA_MOBILE),
    ("m_douyin", f"https://m.douyin.com/share/video/{ID}", UA_MOBILE),
    ("www_video_desktop", f"https://www.douyin.com/video/{ID}", UA_DESKTOP),
    ("www_shipin", f"https://www.douyin.com/shipin/{ID}", UA_DESKTOP),
    ("iesdouyin_web_api", f"https://www.iesdouyin.com/aweme/v1/web/aweme/detail/?aweme_id={ID}&aid=6383&device_platform=webapp", UA_MOBILE),
    ("snssdk_detail", f"https://aweme.snssdk.com/aweme/v1/aweme/detail/?aweme_id={ID}&aid=1128&version_code=290&device_platform=android", UA_MOBILE),
]

MARKERS = ["_ROUTER_DATA", "RENDER_DATA", "__INITIAL_STATE__", "aweme_id", "aweme_detail", "play_addr", "_SSR_DATA"]


def main() -> None:
    c = HttpClient(delay=0.8)
    for name, url, ua in URLS:
        print(f"\n{'='*70}\n[{name}] {url}")
        try:
            r = c.get(url, headers={"User-Agent": ua, "Referer": "https://www.douyin.com/"})
        except Exception as e:  # noqa: BLE001
            print(f"  异常 {type(e).__name__}: {e}")
            continue
        txt = r.text
        print(f"  status={r.status_code} len={len(r.content)} ct={r.headers.get('content-type','')[:40]}")
        for m in MARKERS:
            if m in txt:
                print(f"  含标记: {m}  x{txt.count(m)}")

        # 抠 _ROUTER_DATA 内容
        if "_ROUTER_DATA" in txt:
            i = txt.find("_ROUTER_DATA")
            print(f"  _ROUTER_DATA 附近: {txt[i:i+400]!r}")

        ids = re.findall(r'"aweme_id"\s*:\s*"?(\d{15,25})"?', txt)
        play = re.findall(r'https?://[^"\\\s]*?(?:douyinvod|aweme\.snssdk|iesdouyin)[^"\\\s]{20,}', txt)
        print(f"  aweme_id 命中: {list(dict.fromkeys(ids))[:5]}")
        print(f"  play url 命中: {len(play)} 条 {play[:2]}")
        if not ids and not play:
            print(f"  前 200: {txt[:200]!r}")


if __name__ == "__main__":
    main()
