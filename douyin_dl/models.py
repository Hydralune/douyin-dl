"""数据结构定义 —— 与具体平台解耦，方便以后接别的站点。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# Windows / POSIX 都不合法的文件名字符
_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t\x00-\x1f]')
_WS = re.compile(r"\s+")


def safe_filename(name: str, maxlen: int = 80, fallback: str = "untitled") -> str:
    """把任意标题清洗成合法文件名。"""
    name = _ILLEGAL.sub("_", name or "")
    name = _WS.sub(" ", name).strip(" .")
    # Windows 保留名
    if name.upper().split(".")[0] in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        name = "_" + name
    if len(name) > maxlen:
        name = name[:maxlen].rstrip(" .")
    return name or fallback


@dataclass
class Media:
    """一条可下载的媒体资源。"""
    kind: str            # video / image / audio / cover
    url: str
    quality: str = ""    # 例如 1080p / 720p / origin
    ext: str = ""        # mp4 / jpg / mp3
    index: int = 0
    size: int = 0        # 字节，未知为 0
    extra: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        q = f" [{self.quality}]" if self.quality else ""
        return f"<Media {self.kind}{q} {self.ext}>"


@dataclass
class Aweme:
    """一个作品（视频 / 图集 / 图文）。"""
    aweme_id: str
    desc: str = ""
    author_name: str = ""
    author_id: str = ""
    author_avatar: str = ""
    cover: str = ""
    duration_ms: int = 0
    create_time: int = 0
    medias: List[Media] = field(default_factory=list)
    music: Optional[Media] = None
    statistics: Dict[str, Any] = field(default_factory=dict)
    backend: str = ""                     # 命中的解析后端，排错用
    raw: Optional[Dict[str, Any]] = None  # 原始 item，二次开发用

    # ---------- 便捷属性 ----------
    @property
    def is_image_post(self) -> bool:
        return bool(self.medias) and all(m.kind == "image" for m in self.medias)

    @property
    def video(self) -> Optional[Media]:
        for m in self.medias:
            if m.kind == "video":
                return m
        return None

    @property
    def images(self) -> List[Media]:
        return [m for m in self.medias if m.kind == "image"]

    @property
    def duration_text(self) -> str:
        s = self.duration_ms // 1000
        return f"{s // 60:02d}:{s % 60:02d}"

    def title(self, maxlen: int = 60) -> str:
        base = self.desc or self.aweme_id
        return safe_filename(base, maxlen)

    def __str__(self) -> str:
        badge = "图集" if self.is_image_post else "视频"
        return f"[{badge}] {self.author_name} | {self.title(40)} | {len(self.medias)} 项"

    def to_dict(self) -> Dict[str, Any]:
        """导出为 JSON 可序列化结构（不含 raw）。"""
        return {
            "aweme_id": self.aweme_id,
            "desc": self.desc,
            "author": {
                "name": self.author_name,
                "id": self.author_id,
                "avatar": self.author_avatar,
            },
            "cover": self.cover,
            "duration_ms": self.duration_ms,
            "create_time": self.create_time,
            "type": "image" if self.is_image_post else "video",
            "medias": [
                {"kind": m.kind, "url": m.url, "quality": m.quality, "ext": m.ext}
                for m in self.medias
            ],
            "music": (
                {"url": self.music.url, "ext": self.music.ext} if self.music else None
            ),
            "statistics": self.statistics,
            "backend": self.backend,
        }
