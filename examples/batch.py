"""批量下载示例：浏览器只启一次，之后每条约 1 秒。

用法::

    python examples/batch.py urls.txt -o downloads
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from douyin_dl import DouyinParser, Downloader


def read_urls(path: Path) -> list[str]:
    urls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def main() -> int:
    ap = argparse.ArgumentParser(description="批量下载抖音作品")
    ap.add_argument("file", help="链接文件，一行一个")
    ap.add_argument("-o", "--out", default="downloads")
    ap.add_argument("--music", action="store_true", help="同时下背景音乐")
    ap.add_argument("--zip", action="store_true", help="图集打包 zip")
    ap.add_argument("--headed", action="store_true", help="显示浏览器")
    ap.add_argument("--profile", help="持久化 profile 目录（保存登录态）")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    urls = read_urls(Path(args.file))
    if not urls:
        print("链接文件是空的")
        return 1

    print(f"共 {len(urls)} 条待处理\n")

    ok, fail = 0, 0
    t_all = time.time()

    # 关键：parser 在循环外创建，浏览器只启动一次
    with DouyinParser(
        headless=not args.headed,
        user_data_dir=args.profile,
        use_cache=not args.no_cache,
    ) as parser:
        dl = Downloader(out_dir=args.out)

        for i, url in enumerate(urls, 1):
            t0 = time.time()
            short = url if len(url) <= 60 else url[:57] + "..."
            print(f"[{i}/{len(urls)}] {short}")
            try:
                aweme = parser.parse(url)
                files = dl.save(aweme)
                if args.zip and aweme.is_image_post:
                    z = dl.save_zip(aweme, delete_dir=True)
                    if z:
                        files.append(z)
                if args.music:
                    m = dl.save_music(aweme)
                    if m:
                        files.append(m)

                cost = time.time() - t0
                print(
                    f"   OK  {aweme.author_name} | {aweme.duration_text} | "
                    f"{len(files)} 文件 | {cost:.1f}s"
                )
                ok += 1
            except Exception as e:  # noqa: BLE001
                print(f"   FAIL {type(e).__name__}: {e}")
                fail += 1

    total = time.time() - t_all
    print(f"\n成功 {ok} / 失败 {fail} / 共 {len(urls)}，总耗时 {total:.1f}s")
    print(f"输出目录: {Path(args.out).resolve()}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
