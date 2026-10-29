"""实测下载的 暂停 / 继续 / 取消 是否真的生效。"""
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from douyin_dl import DouyinParser  # noqa: E402
from douyin_dl.downloader import (  # noqa: E402
    DownloadCancelled,
    DownloadControl,
    Downloader,
)

URL = "https://v.douyin.com/MnQz35HAVu4/"
OUT = ROOT / "tests" / "_dl_test"


def main() -> int:
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)

    with DouyinParser() as p:
        aweme = p.parse(URL)
    print(f"作品: {aweme.title(30)}")

    state = {"recv": 0, "total": 0, "err": None, "done": False}
    ctl = DownloadControl()

    def on_prog(name: str, recv: int, total: int) -> None:
        state["recv"] = recv
        state["total"] = total

    dl = Downloader(out_dir=OUT, show_progress=False, on_progress=on_prog)

    def run() -> None:
        try:
            dl.save(aweme, control=ctl)
            state["done"] = True
        except DownloadCancelled:
            state["err"] = "cancelled"
        except Exception as e:  # noqa: BLE001
            state["err"] = f"{type(e).__name__}: {e}"

    t = threading.Thread(target=run, daemon=True)
    t.start()

    # --- 阶段 1：下载 2 秒 ---
    time.sleep(2.0)
    r1 = state["recv"]
    print(f"[1] 下载 2s 后: {r1 / 1024 / 1024:.2f} MB / {state['total'] / 1024 / 1024:.1f} MB")

    # --- 阶段 2：暂停 2 秒，进度应几乎不动 ---
    ctl.pause()
    print("[2] 已暂停")
    time.sleep(2.0)
    r2 = state["recv"]
    drift = (r2 - r1) / 1024
    print(f"    暂停期间增长: {drift:.1f} KB  (允许少量缓冲残留)")
    paused_ok = drift < 256  # 小于 256KB 视为真停住

    # --- 阶段 3：继续 ---
    ctl.resume()
    print("[3] 已继续")
    time.sleep(2.0)
    r3 = state["recv"]
    grew = (r3 - r2) / 1024 / 1024
    print(f"    继续 2s 新增: {grew:.2f} MB")
    resume_ok = grew > 0.1

    # --- 阶段 4：取消 ---
    ctl.cancel()
    print("[4] 已取消，等待线程退出")
    t.join(timeout=10)
    print(f"    线程退出: {not t.is_alive()}  结果: {state['err']}")
    cancel_ok = state["err"] == "cancelled"

    parts = list(OUT.glob("*.part"))
    finals = list(OUT.glob("*.mp4"))
    print(f"    残留 .part: {len(parts)}  完整 mp4: {len(finals)}")

    # --- 阶段 5：重新下载（验证续传） ---
    print("[5] 重新下载（应走 Range 续传）")
    ctl2 = DownloadControl()
    dl2 = Downloader(out_dir=OUT, show_progress=False, on_progress=on_prog)
    start_size = parts[0].stat().st_size if parts else 0
    print(f"    续传起点: {start_size / 1024 / 1024:.2f} MB")
    st2 = {"done": False, "err": None}

    def run2() -> None:
        try:
            dl2.save(aweme, control=ctl2)
            st2["done"] = True
        except Exception as e:  # noqa: BLE001
            st2["err"] = f"{type(e).__name__}: {e}"

    t2 = threading.Thread(target=run2, daemon=True)
    t2.start()
    t2.join(timeout=120)
    final_mp4 = list(OUT.glob("*.mp4"))
    size = final_mp4[0].stat().st_size if final_mp4 else 0
    print(f"    完成: {st2['done']}  错误: {st2['err']}")
    print(f"    最终文件: {len(final_mp4)} 个, {size / 1024 / 1024:.2f} MB")
    resume_dl_ok = st2["done"] and size > 30 * 1024 * 1024

    print("\n=== 结论 ===")
    print(f"  暂停生效 : {'✓' if paused_ok else '✗'}")
    print(f"  继续生效 : {'✓' if resume_ok else '✗'}")
    print(f"  取消生效 : {'✓' if cancel_ok else '✗'}")
    print(f"  续传下载 : {'✓' if resume_dl_ok else '✗'}")
    return 0 if all([paused_ok, resume_ok, cancel_ok, resume_dl_ok]) else 1


if __name__ == "__main__":
    sys.exit(main())
