"""用微信 UA 拿 share 页，dump 完整 item 结构，确定字段名。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from douyin_dl.client import HttpClient
from douyin_dl.parser import find_item_list, json_after

ID = sys.argv[1] if len(sys.argv) > 1 else "7264095001330829366"

UA_WECHAT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.49(0x18003131) "
    "NetType/WIFI Language/zh_CN"
)

SHARE = f"https://www.iesdouyin.com/share/video/{ID}/"


def main() -> None:
    c = HttpClient(delay=0.5)
    r = c.get(SHARE, headers={"User-Agent": UA_WECHAT, "Referer": "https://www.douyin.com/"})
    print(f"status={r.status_code} len={len(r.content)}")

    data = json_after(r.text, "_ROUTER_DATA")
    if not data:
        print("_ROUTER_DATA 没抠出来")
        return

    items = find_item_list(data)
    print(f"item_list: {len(items) if items else 0}")
    if not items:
        # 打印 loaderData 的键，看结构
        print("loaderData keys:", list(data.get("loaderData", {}).keys()))
        Path("dump_router.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("已写 dump_router.json")
        return

    item = items[0]
    Path("dump_item.json").write_text(
        json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("已写 dump_item.json:", len(json.dumps(item, ensure_ascii=False)), "字符")

    print("\n--- 顶层键 ---")
    for k, v in item.items():
        t = type(v).__name__
        brief = ""
        if isinstance(v, str):
            brief = v[:60]
        elif isinstance(v, (int, float)):
            brief = str(v)
        elif isinstance(v, list):
            brief = f"len={len(v)}"
        elif isinstance(v, dict):
            brief = f"keys={list(v.keys())[:8]}"
        print(f"  {k:24} {t:6} {brief}")

    print("\n--- author ---")
    for k, v in (item.get("author") or {}).items():
        if isinstance(v, str):
            print(f"  {k:24} {v[:60]}")
        elif isinstance(v, dict):
            print(f"  {k:24} keys={list(v.keys())[:8]}")
        elif isinstance(v, list):
            print(f"  {k:24} len={len(v)}")

    print("\n--- video ---")
    video = item.get("video") or {}
    for k, v in video.items():
        if isinstance(v, str):
            print(f"  {k:24} {v[:70]}")
        elif isinstance(v, dict):
            print(f"  {k:24} keys={list(v.keys())[:10]}")
        elif isinstance(v, list):
            print(f"  {k:24} len={len(v)}")
        else:
            print(f"  {k:24} {v}")

    print("\n--- video.play_addr ---")
    pa = video.get("play_addr") or {}
    print(json.dumps(pa, ensure_ascii=False, indent=2)[:1500])

    print("\n--- video.bit_rate ---")
    br = video.get("bit_rate") or []
    if isinstance(br, list):
        print(f"  共 {len(br)} 档")
        for b in br[:5]:
            if isinstance(b, dict):
                print(
                    f"    gear={b.get('gear_name')} rate={b.get('bit_rate')} "
                    f"quality={b.get('quality_type')} "
                    f"url={str((b.get('play_addr') or {}).get('url_list', [''])[0])[:90]}"
                )

    print("\n--- images ---")
    imgs = item.get("images")
    if imgs:
        print(f"  共 {len(imgs)} 张")
        if isinstance(imgs[0], dict):
            print(f"  第一张 keys: {list(imgs[0].keys())}")
            print(f"  url_list: {str(imgs[0].get('url_list'))[:300]}")
    else:
        print("  无（视频作品）")

    print("\n--- music ---")
    mu = item.get("music") or {}
    print(f"  keys: {list(mu.keys())[:12]}")
    print(f"  play_url: {str(mu.get('play_url'))[:300]}")


if __name__ == "__main__":
    main()
