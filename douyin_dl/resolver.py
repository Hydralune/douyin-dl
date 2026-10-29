"""链接处理：从分享文本里抠出 aweme_id，展开 v.douyin.com 短链。"""
from __future__ import annotations

import re
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .client import HttpClient

# 短链域名，需要先请求一次拿 302 Location
SHORT_HOSTS = {
    "v.douyin.com",
    "v.iesdouyin.com",
    "v.kuaishou.com",   # 预留，别的平台复用
}

# 抖音所有已知的 URL 形态
_ID_PATTERNS = (
    re.compile(r"/video/(\d{15,25})"),
    re.compile(r"/note/(\d{15,25})"),
    re.compile(r"/share/video/(\d{15,25})"),
    re.compile(r"/share/note/(\d{15,25})"),
    re.compile(r"/discover\?[^#]*modal_id=(\d{15,25})"),
    re.compile(r"[?&]modal_id=(\d{15,25})"),
    re.compile(r"[?&]aweme_id=(\d{15,25})"),
    re.compile(r"[?&]item_ids=(\d{15,25})"),
    re.compile(r"/aweme/v1/web/aweme/detail/\?[^ ]*aweme_id=(\d{15,25})"),
)

# 纯数字 / 带前缀的 ID 写法
_BARE_ID = re.compile(r"^\s*(?:douyin[_:]?)?(\d{15,25})\s*$", re.I)

URL_IN_TEXT = re.compile(r"https?://[^\s\u4e00-\u9fff\"'<>）)，,]+")


def extract_aweme_id(text: str) -> Optional[str]:
    """从分享文本、URL、或裸 ID 里提取作品 ID。"""
    if not text:
        return None
    text = text.strip()

    m = _BARE_ID.match(text)
    if m:
        return m.group(1)

    for pat in _ID_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1)
    return None


def extract_first_url(text: str) -> Optional[str]:
    """分享文本往往是大段文案 + 一个短链，把链接挑出来。"""
    if not text:
        return None
    m = URL_IN_TEXT.search(text)
    return m.group(0) if m else None


def is_short_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
    except Exception:  # noqa: BLE001
        return False
    return host in SHORT_HOSTS


def expand_short_url(url: str, client: HttpClient) -> str:
    """请求短链，从 302 的 Location 里拿真实地址。失败则原样返回。"""
    if not is_short_url(url):
        return url
    try:
        r = client.get(url, allow_redirects=False)
        loc = r.headers.get("Location")
        if loc:
            return loc
        # 没给 Location 说明直接返回了 HTML，从正文里找
        found = extract_first_url(r.text) or ""
        return found or url
    except Exception:  # noqa: BLE001
        return url


def normalize(text: str, client: Optional[HttpClient] = None) -> Tuple[Optional[str], str]:
    """把任意输入归一化成 (aweme_id, 展开后的 url)。

    - 输入裸 ID        -> (id, "")
    - 输入完整/分享 URL -> (id, real_url)
    - 输入分享文案      -> (id, real_url)
    """
    raw = (text or "").strip()

    aweme_id = extract_aweme_id(raw)
    if aweme_id:
        return aweme_id, ""

    url = extract_first_url(raw) or raw
    if not url.startswith("http"):
        return None, ""

    if client and is_short_url(url):
        url = expand_short_url(url, client)

    aweme_id = extract_aweme_id(url)
    if aweme_id:
        return aweme_id, url

    # 有些短链 302 到 /share/video/ 形态，上面已覆盖；
    # 再兜一层：解析 query
    qs = parse_qs(urlparse(url).query)
    for key in ("modal_id", "aweme_id", "item_ids", "vid"):
        if key in qs and re.fullmatch(r"\d{15,25}", qs[key][0]):
            return qs[key][0], url

    return None, url
