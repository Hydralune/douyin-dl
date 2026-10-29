"""演示模式：启动 GUI、自动解析一个作品、保持窗口开着（供外部截图）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gui  # noqa: E402

URL = sys.argv[1] if len(sys.argv) > 1 else "https://v.douyin.com/MnQz35HAVu4/"
HOLD_MS = int(sys.argv[2]) if len(sys.argv) > 2 else 90000


def main() -> int:
    app = gui.App()
    app.geometry("1010x780+40+30")
    app.txt_input.delete("1.0", "end")
    app.txt_input.insert("1.0", URL)
    app.txt_input.tag_remove("sel", "1.0", "end")
    app._on_text_change()
    app.after(300, app._on_parse)
    app.after(HOLD_MS, app.destroy)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
