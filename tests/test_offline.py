"""离线自测：不依赖网络，验证链接解析与文件名清洗。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from douyin_dl.models import safe_filename
from douyin_dl.parser import build_play_url, no_watermark
from douyin_dl.resolver import extract_aweme_id, extract_first_url

CASES = [
    # (输入, 期望 ID)
    ("https://v.douyin.com/iRNBho6u/", None),                       # 短链要联网才知道
    ("https://www.douyin.com/video/7300000000000000001", "7300000000000000001"),
    ("https://www.douyin.com/discover?modal_id=7300000000000000002", "7300000000000000002"),
    ("https://www.iesdouyin.com/share/video/7300000000000000004/?region=CN", "7300000000000000004"),
    ("https://www.douyin.com/note/7300000000000000005", "7300000000000000005"),
    ("7300000000000000003", "7300000000000000003"),
    ("douyin:7300000000000000006", "7300000000000000006"),
    (
        "7.43 复制打开抖音，看看【某作者】的作品 https://www.douyin.com/video/7300000000000000007 abc",
        "7300000000000000007",
    ),
    ("https://www.douyin.com/user/MS4wLjABAAAA123?modal_id=7300000000000000008", "7300000000000000008"),
    ("随便一段没有链接的文字", None),
]


def main() -> int:
    failed = 0
    print("== extract_aweme_id ==")
    for text, expect in CASES:
        got = extract_aweme_id(text)
        ok = got == expect
        if not ok:
            failed += 1
        print(f"  {'OK ' if ok else 'FAIL'} {text[:58]:60} -> {got}")

    print("\n== extract_first_url ==")
    raw = "7.43 复制打开抖音 https://v.douyin.com/abc123/ 看看"
    got = extract_first_url(raw)
    ok = got == "https://v.douyin.com/abc123/"
    failed += 0 if ok else 1
    print(f"  {'OK ' if ok else 'FAIL'} {got}")

    print("\n== no_watermark ==")
    wm = "https://aweme.snssdk.com/aweme/v1/playwm/?video_id=v0300&ratio=720p&line=0"
    expect = "https://aweme.snssdk.com/aweme/v1/play/?video_id=v0300&ratio=720p&line=0"
    got = no_watermark(wm)
    ok = got == expect
    failed += 0 if ok else 1
    print(f"  {'OK ' if ok else 'FAIL'} {got}")
    cdn = "https://v3-web.douyinvod.com/abc/video/tos/cn/x/?a=6383"
    ok2 = no_watermark(cdn) == cdn
    failed += 0 if ok2 else 1
    print(f"  {'OK ' if ok2 else 'FAIL'} CDN 直链保持不变")

    print("\n== build_play_url ==")
    u = build_play_url("v0300fg10000abc", "1080p")
    ok = "video_id=v0300fg10000abc" in u and "1080p" in u
    failed += 0 if ok else 1
    print(f"  {'OK ' if ok else 'FAIL'} {u}")

    print("\n== safe_filename ==")
    danger = 'a/b\\c:d*e?f"g<h>i|j\n\t 测试标题  ' * 3
    got = safe_filename(danger, 40)
    bad = any(ch in got for ch in '\\/:*?"<>|\n\t')
    ok = (not bad) and len(got) <= 40 and got
    failed += 0 if ok else 1
    print(f"  {'OK ' if ok else 'FAIL'} {got!r}")
    ok2 = safe_filename("CON") == "_CON"
    failed += 0 if ok2 else 1
    print(f"  {'OK ' if ok2 else 'FAIL'} Windows 保留名 CON -> {safe_filename('CON')}")

    print(f"\n{'全部通过' if failed == 0 else f'{failed} 项失败'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
