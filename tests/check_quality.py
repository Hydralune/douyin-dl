"""验证清晰度分档是否正确。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from douyin_dl import DouyinParser

URL = sys.argv[1] if len(sys.argv) > 1 else "https://v.douyin.com/MnQz35HAVu4/"


def main() -> int:
    with DouyinParser() as p:
        a = p.parse(URL)
        print(f"作品: {a.author_name} · {a.title(30)}")
        print(f"档位数: {len(a.medias)}")
        for i, m in enumerate(a.medias):
            br = m.extra.get("bitrate", 0) if isinstance(m.extra, dict) else 0
            print(f"  [{i}] {m.quality:>8}  {br // 1000:>5}k  {m.url[:64]}")
        print(f"\naweme.video 默认取: {a.video.quality if a.video else '无'}")
        print(f"is_image_post: {a.is_image_post}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
