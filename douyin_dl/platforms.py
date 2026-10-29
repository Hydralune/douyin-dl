"""平台注册表 —— 为多站点扩展预留的统一入口。

新增一个平台只需三步：
1. 写一个 Parser 类，实现 `parse(text) -> Aweme`
2. 在 PLATFORMS 里登记
3. 把 ready 改成 True

GUI 和 CLI 都从这里读列表，不写死。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class Platform:
    key: str                       # 内部标识
    name: str                      # 显示名
    hint: str = ""                 # 输入框提示
    accent: str = "#FE2C55"        # 主题色（抖音红）
    ready: bool = False            # 是否已实现
    note: str = ""                 # 未实现时的说明
    link_patterns: List[str] = field(default_factory=list)
    factory: Optional[Callable[..., Any]] = None

    @property
    def status_text(self) -> str:
        return "可用" if self.ready else (self.note or "即将支持")


# ----------------------------------------------------------------------
def _douyin_factory(**kw: Any) -> Any:
    from .parser import DouyinParser

    return DouyinParser(**kw)


PLATFORMS: Dict[str, Platform] = {
    "douyin": Platform(
        key="douyin",
        name="抖音",
        hint="粘贴分享链接、分享文案，或直接填作品 ID",
        accent="#FE2C55",
        ready=True,
        link_patterns=["v.douyin.com", "douyin.com/video", "douyin.com/note"],
        factory=_douyin_factory,
    ),
    "xiaohongshu": Platform(
        key="xiaohongshu",
        name="小红书",
        hint="粘贴笔记链接（xhslink.com 或 xiaohongshu.com 分享页）",
        accent="#FF2442",
        ready=False,
        note="即将支持 · 需处理 x-s/x-t 签名",
        link_patterns=["xhslink.com", "xiaohongshu.com"],
    ),
    "bilibili": Platform(
        key="bilibili",
        name="B站",
        hint="粘贴视频链接（BV 号或 b23.tv 短链）",
        accent="#00A1D6",
        ready=False,
        note="即将支持 · 公开接口，最容易接",
        link_patterns=["b23.tv", "bilibili.com/video"],
    ),
    "kuaishou": Platform(
        key="kuaishou",
        name="快手",
        hint="粘贴快手分享链接",
        accent="#FF6600",
        ready=False,
        note="即将支持 · 解析 __APOLLO_STATE__",
        link_patterns=["v.kuaishou.com", "kuaishou.com"],
    ),
    "youtube": Platform(
        key="youtube",
        name="YouTube",
        hint="粘贴视频链接（含 youtu.be）",
        accent="#FF0000",
        ready=False,
        note="即将支持 · 计划包装 yt-dlp",
        link_patterns=["youtube.com", "youtu.be"],
    ),
    "weibo": Platform(
        key="weibo",
        name="微博",
        hint="粘贴微博视频链接",
        accent="#E6162D",
        ready=False,
        note="即将支持 · 公开接口",
        link_patterns=["weibo.com", "m.weibo.cn"],
    ),
}


def get(key: str) -> Optional[Platform]:
    return PLATFORMS.get(key)


def ready_platforms() -> List[Platform]:
    return [p for p in PLATFORMS.values() if p.ready]


def detect(text: str) -> Optional[Platform]:
    """根据链接猜平台，猜不到返回 None。"""
    low = (text or "").lower()
    for p in PLATFORMS.values():
        for pat in p.link_patterns:
            if pat in low:
                return p
    return None
