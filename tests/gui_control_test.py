"""GUI 交互自测：解析 -> 下载 -> 暂停 -> 继续 -> 取消 -> 历史 -> 删除。

自动点按钮，验证状态机和按钮文字。
"""
from __future__ import annotations

import shutil
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

URL = "https://v.douyin.com/MnQz35HAVu4/"
OUT = ROOT / "tests" / "_gui_dl"
SHOTS = ROOT / "tests" / "_shots"
SHOTS.mkdir(parents=True, exist_ok=True)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'✓' if ok else '✗'} {name}  {detail}")


def grab(app, name: str) -> None:
    try:
        from PIL import ImageGrab

        app.update_idletasks()
        app.update()
        app.attributes("-topmost", True)
        app.lift()
        app.update()
        ImageGrab.grab().save(SHOTS / name)
        app.attributes("-topmost", False)
        print(f"    截图 {name}")
    except Exception as e:  # noqa: BLE001
        print(f"    截图失败: {e}")


def main() -> int:
    import gui

    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)

    # 删除任务时不弹确认框
    gui.messagebox.askyesnocancel = lambda *a, **k: False  # type: ignore[assignment]

    app = gui.App()
    app.geometry("1020x790+30+20")
    app.out_dir = OUT
    app.rate_limit = 2 * 1024 * 1024      # 限速 2MB/s，35MB 要 17 秒，够测暂停
    app.lbl_outdir.configure(text=f"输出目录  {OUT}")

    st: dict = {"p1": 0, "p2": 0, "p3": 0}

    def task() -> gui.Task:
        return app.tasks[0]

    # ---- 0.5s 填入并解析 ----
    def s_parse() -> None:
        app.txt_input.delete("1.0", "end")
        app.txt_input.insert("1.0", URL)
        app.txt_input.tag_remove("sel", "1.0", "end")
        app._on_text_change()
        app._on_parse()

    # ---- 20s 检查解析结果并开始下载 ----
    def s_start_dl() -> None:
        t = task()
        check("解析完成", t.status == "parsed", f"status={t.status}")
        check("清晰度档位", len(getattr(t.row, "media_map", [])) >= 2,
              f"{len(t.row.media_map)} 档")
        check("按钮文字=下载", t.row.btn_main.cget("text") == "下载")
        app.download_task(t)
        check("进入下载中", t.status == "downloading")

    # ---- 24s 暂停 ----
    def s_pause() -> None:
        t = task()
        st["p1"] = t.received
        app.pause_task(t)
        check("暂停后状态", t.status == "paused", f"status={t.status}")
        check("按钮文字=继续", t.row.btn_main.cget("text") == "继续")
        check("取消按钮出现", t.row.btn_cancel.winfo_ismapped())
        grab(app, "c1_paused.png")

    # ---- 27s 验证真停住 + 继续 ----
    def s_resume() -> None:
        t = task()
        drift = t.received - st["p1"]
        check("暂停期间进度不动", drift < 256 * 1024, f"漂移 {drift / 1024:.0f}KB")
        st["p2"] = t.received
        app.resume_task(t)
        check("继续后状态", t.status == "downloading")
        check("按钮文字=暂停", t.row.btn_main.cget("text") == "暂停")

    # ---- 30s 验证增长 + 取消 ----
    def s_cancel() -> None:
        t = task()
        grew = t.received - st["p2"]
        check("继续后进度增长", grew > 100 * 1024, f"新增 {grew / 1024 / 1024:.2f}MB")
        app.cancel_task(t)

    # ---- 33s 检查取消 ----
    def s_after_cancel() -> None:
        t = task()
        check("取消后状态", t.status == "cancelled", f"status={t.status}")
        check("按钮文字=重新下载", t.row.btn_main.cget("text") == "重新下载")
        check("取消按钮隐藏", not t.row.btn_cancel.winfo_ismapped())
        grab(app, "c2_cancelled.png")
        app._show_history()

    # ---- 36s 检查历史窗口 ----
    def s_history() -> None:
        wins = [w for w in app.winfo_children()
                if w.winfo_class() == "Toplevel"]
        hist = [w for w in wins if "历史" in (w.title() or "")]
        check("历史窗口打开", bool(hist), f"{len(wins)} 个 Toplevel")
        for w in hist:
            w.destroy()

    # ---- 38s 重新下载完成 + 写历史 ----
    def s_redownload() -> None:
        t = task()
        app.download_task(t)

    def s_final() -> None:
        t = task()
        check("重新下载完成", t.status == "done", f"status={t.status}")
        check("文件已落盘", any(OUT.glob("*.mp4")),
              f"{len(list(OUT.glob('*.mp4')))} 个 mp4")
        recs = app.history.all()
        check("历史已记录", len(recs) > 0, f"{len(recs)} 条")
        if recs:
            check("历史文件存在", recs[0].exists, recs[0].size_text)
        grab(app, "c3_done.png")

        # 删除任务（不删文件）
        app.delete_task(t)
        check("任务已移除", len(app.tasks) == 0, f"{len(app.tasks)} 个")
        app.after(300, app.destroy)

    app.after(500, s_parse)
    app.after(20000, s_start_dl)
    app.after(24000, s_pause)
    app.after(27000, s_resume)
    app.after(30000, s_cancel)
    app.after(33000, s_after_cancel)
    app.after(36000, s_history)
    app.after(37500, s_redownload)
    app.after(55000, s_final)
    app.after(120000, lambda: (print("!! 总超时"), app.destroy()))

    try:
        app.mainloop()
    except Exception:
        traceback.print_exc()
        return 1

    print("\n=== 汇总 ===")
    bad = [n for n, ok, _ in results if not ok]
    print(f"  通过 {len(results) - len(bad)} / {len(results)}")
    if bad:
        print("  失败:", ", ".join(bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
