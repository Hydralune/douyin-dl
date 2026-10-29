"""下载历史 —— 落盘 JSON，进程重启后仍可回溯。

记录结构刻意保持扁平，方便以后导出 CSV 或喂给别的程序。
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("douyin_dl.history")

DEFAULT_PATH = Path.home() / ".douyin_dl" / "history.json"
MAX_RECORDS = 2000


@dataclass
class HistoryRecord:
    aweme_id: str
    title: str = ""
    author: str = ""
    platform: str = "douyin"
    kind: str = "video"            # video / image
    quality: str = ""
    files: List[str] = field(default_factory=list)
    size: int = 0
    ts: float = field(default_factory=time.time)
    url: str = ""

    # ---------- 展示用 ----------
    @property
    def time_text(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.ts))

    @property
    def exists(self) -> bool:
        """文件还在不在（用户可能手动删了）。"""
        if not self.files:
            return False
        return any(Path(f).exists() for f in self.files)

    @property
    def primary_file(self) -> Optional[str]:
        for f in self.files:
            if Path(f).exists():
                return f
        return self.files[0] if self.files else None

    @property
    def size_text(self) -> str:
        n = float(self.size)
        if n <= 0:
            return "-"
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.1f}{unit}"
            n /= 1024
        return f"{n:.1f}TB"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "HistoryRecord":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


class History:
    """极简 JSON 历史库。线程安全靠调用方串行（GUI 里都在主线程操作）。"""

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self._records: List[HistoryRecord] = []
        self.load()

    # ------------------------------------------------------------------
    def load(self) -> None:
        self._records = []
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log.warning("历史文件损坏，已忽略: %s", e)
            return
        items = raw.get("records", raw) if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                self._records.append(HistoryRecord.from_dict(item))
            except Exception:  # noqa: BLE001
                continue
        log.debug("载入 %d 条历史", len(self._records))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "records": [r.to_dict() for r in self._records]}
        tmp = self.path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)   # 原子替换，避免写一半断电
        except Exception as e:  # noqa: BLE001
            log.warning("写历史失败: %s", e)

    # ------------------------------------------------------------------
    def add(self, rec: HistoryRecord) -> None:
        # 同一作品同一批文件视为重复，用新记录覆盖旧的
        key = (rec.aweme_id, tuple(sorted(rec.files)))
        self._records = [
            r for r in self._records if (r.aweme_id, tuple(sorted(r.files))) != key
        ]
        self._records.append(rec)
        if len(self._records) > MAX_RECORDS:
            self._records = self._records[-MAX_RECORDS:]
        self.save()

    def all(self) -> List[HistoryRecord]:
        """按时间倒序。"""
        return sorted(self._records, key=lambda r: r.ts, reverse=True)

    def has(self, aweme_id: str) -> bool:
        return any(r.aweme_id == aweme_id for r in self._records)

    def remove(self, aweme_id: str) -> int:
        before = len(self._records)
        self._records = [r for r in self._records if r.aweme_id != aweme_id]
        removed = before - len(self._records)
        if removed:
            self.save()
        return removed

    def remove_record(self, rec: HistoryRecord) -> None:
        self._records = [
            r for r in self._records
            if not (r.aweme_id == rec.aweme_id and r.files == rec.files)
        ]
        self.save()

    def clear(self, delete_files: bool = False) -> int:
        n = len(self._records)
        if delete_files:
            for r in self._records:
                for f in r.files:
                    try:
                        Path(f).unlink(missing_ok=True)
                    except Exception:  # noqa: BLE001
                        pass
        self._records = []
        self.save()
        return n

    # ------------------------------------------------------------------
    def add_from_aweme(
        self,
        aweme: Any,
        files: List[Path],
        quality: str = "",
        url: str = "",
        platform: str = "douyin",
    ) -> HistoryRecord:
        """从解析结果 + 落盘文件生成一条历史。"""
        size = 0
        paths: List[str] = []
        for f in files:
            try:
                if f.exists():
                    size += f.stat().st_size
                paths.append(str(f.resolve()))
            except Exception:  # noqa: BLE001
                paths.append(str(f))
        rec = HistoryRecord(
            aweme_id=getattr(aweme, "aweme_id", ""),
            title=getattr(aweme, "desc", "")[:200],
            author=getattr(aweme, "author_name", ""),
            platform=platform,
            kind="image" if getattr(aweme, "is_image_post", False) else "video",
            quality=quality,
            files=paths,
            size=size,
            url=url,
        )
        self.add(rec)
        return rec


def open_in_explorer(path: str | Path) -> bool:
    """在资源管理器里定位文件。"""
    p = Path(path)
    try:
        if p.exists():
            if os.name == "nt":
                os.startfile(str(p))  # type: ignore[attr-defined]
            return True
        parent = p.parent
        if parent.exists():
            os.startfile(str(parent))  # type: ignore[attr-defined]
            return True
    except Exception as e:  # noqa: BLE001
        log.warning("打开失败: %s", e)
    return False
