"""GUI 自动化自测：启动界面 -> 填链接 -> 解析 -> 截图 -> 下载 -> 截图 -> 退出。

不需要人手操作，用于验证整条链路。运行：

    python tests/gui_selftest.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

URL = "https://v.douyin.com/MnQz35HAVu4/"
OUT = ROOT / "tests" / "_shots"
OUT.mkdir(parents=True, exist_ok=True)


def grab(app, name: str) -> None:
    """全屏截图（窗口级 bbox 在 DPI 缩放下不可靠）。"""
    try:
        from PIL import ImageGrab

        app.update_idletasks()
        app.update()
        app.attributes("-topmost", True)
        app.lift()
        app.update()
        img = ImageGrab.grab()
        app.attributes("-topmost", False)
        p = OUT / name
        img.save(p)
        print(f"  截图 -> {p}  ({img.size[0]}x{img.size[1]})")
    except Exception as e:  # noqa: BLE001
        print(f"  截图失败: {e}")


def main() -> int:
    import gui

    app = gui.App()
    app.geometry("1020x790+30+20")

    state = {"phase": 0}

    def log(*a):
        print(" ", *a)

    def phase_parse() -> None:
        state["phase"] = 1
        log("填入链接并触发解析")
        app.txt_input.delete("1.0", "end")
        app.txt_input.insert("1.0", URL)
        app.txt_input.tag_remove("sel", "1.0", "end")
        app._on_text_change()
        grab(app, "1_input.png")
        app._on_parse()

    def phase_check_parsed() -> None:
        task = app.tasks[0] if app.tasks else None
        if task is None:
            log("!! 没有任务")
            app.destroy()
            return
        log(f"任务状态: {task.status}")
        grab(app, "2_parsed.png")
        if task.status == "parsed":
            log("解析成功，触发下载")
            app.download_task(task)
            app.after(1500, lambda: grab(app, "3_downloading.png"))
            app.after(2000, lambda: check_download(task, 0))
        else:
            log(f"解析未成功: {task.error}")
            app.after(500, app.destroy)

    def check_download(task, n: int) -> None:
        if task.status == "done":
            log("下载完成")
            grab(app, "4_done.png")
            app.after(300, app.destroy)
            return
        if n < 40:
            app.after(1000, lambda: check_download(task, n + 1))
        else:
            log(f"下载超时，最终状态 {task.status} {task.progress:.0f}%")
            grab(app, "4_timeout.png")
            app.destroy()

    app.after(600, phase_parse)
    app.after(22000, phase_check_parsed)
    app.after(120000, lambda: (log("总超时，强制退出"), app.destroy()))

    try:
        app.mainloop()
    except Exception:
        traceback.print_exc()
        return 1
    print("自测结束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
