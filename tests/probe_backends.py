"""后端连通性探测：看每个后端返回什么，方便排错。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from douyin_dl.client import HttpClient
from douyin_dl.parser import (
    ITEMINFO,
    SHARE_PAGE,
    WEB_DETAIL,
    find_item_list,
    json_after,
)

AWEME_ID = sys.argv[1] if len(sys.argv) > 1 else "7300000000000000001"


def main() -> None:
    c = HttpClient(delay=0.5)

    print(f"=== 目标 id: {AWEME_ID} ===")

    # 1) 分享页
    url = SHARE_PAGE.format(id=AWEME_ID)
    print(f"\n[1] share_page  {url}")
    try:
        r = c.get(url, headers={"Referer": "https://www.douyin.com/"})
        print(f"    status={r.status_code} len={len(r.content)} final={r.url}")
        html = r.text
        data = json_after(html, "_ROUTER_DATA")
        print(f"    _ROUTER_DATA: {'有' if data else '无'}")
        if data:
            items = find_item_list(data)
            print(f"    item_list: {len(items) if items else 0}")
            if items:
                it = items[0]
                print(f"    keys: {list(it.keys())[:14]}")
                print(f"    desc: {str(it.get('desc'))[:60]}")
        else:
            print(f"    前 300 字: {html[:300]!r}")
    except Exception as e:  # noqa: BLE001
        print(f"    异常: {type(e).__name__}: {e}")

    # 2) web detail
    print(f"\n[2] web_detail {WEB_DETAIL}?aweme_id={AWEME_ID}")
    try:
        r = c.get(
            WEB_DETAIL,
            params={"device_platform": "webapp", "aid": "6383", "aweme_id": AWEME_ID,
                    "channel": "channel_pc_web"},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Referer": f"https://www.douyin.com/video/{AWEME_ID}",
            },
        )
        print(f"    status={r.status_code} len={len(r.content)}")
        print(f"    body: {r.text[:260]}")
    except Exception as e:  # noqa: BLE001
        print(f"    异常: {type(e).__name__}: {e}")

    # 3) iteminfo
    print(f"\n[3] iteminfo   {ITEMINFO}?item_ids={AWEME_ID}")
    try:
        r = c.get(ITEMINFO, params={"item_ids": AWEME_ID})
        print(f"    status={r.status_code} len={len(r.content)}")
        print(f"    body: {r.text[:260]}")
    except Exception as e:  # noqa: BLE001
        print(f"    异常: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
