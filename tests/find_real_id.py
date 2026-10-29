"""抓几个真实 aweme_id 用来验证解析链路。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from douyin_dl.client import HttpClient, UA_DESKTOP, UA_MOBILE

CANDIDATES = [
    # 热榜视频（老的 billboard 接口）
    ("hot_billboard_aweme", "https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/aweme/"),
    # 热榜主题，附带视频
    ("hot_billboard_word", "https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/"),
    # 抖音 web 首页推荐的 SSR 数据
    ("douyin_home", "https://www.douyin.com/"),
    # 抖音热点页
    ("douyin_hot", "https://www.douyin.com/hot"),
]


def main() -> None:
    c = HttpClient(delay=0.6)
    for name, url in CANDIDATES:
        print(f"\n=== {name}: {url}")
        try:
            r = c.get(url, headers={"User-Agent": UA_MOBILE if "iesdouyin" in url else UA_DESKTOP})
            print(f"  status={r.status_code} len={len(r.content)} type={r.headers.get('content-type','')[:40]}")
            txt = r.text
            if txt.lstrip().startswith("{"):
                import json

                try:
                    data = json.loads(txt)
                    print(f"  json keys: {list(data.keys())[:10]}")
                    print(f"  preview: {txt[:300]}")
                except Exception:
                    print(f"  preview: {txt[:300]}")
            else:
                import re

                ids = re.findall(r'"aweme_id"\s*:\s*"(\d{15,25})"', txt)
                ids += re.findall(r'/video/(\d{15,25})', txt)
                uniq = list(dict.fromkeys(ids))
                print(f"  找到 {len(uniq)} 个 id: {uniq[:8]}")
                if not uniq:
                    print(f"  preview: {txt[:200]!r}")
        except Exception as e:  # noqa: BLE001
            print(f"  异常: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
