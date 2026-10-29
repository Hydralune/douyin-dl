"""HTTP 客户端 —— 统一 UA / 重试 / 代理 / Cookie / 限速。"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("douyin_dl.http")

# 移动端 UA：分享页走这个最干净
UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": UA_MOBILE,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.douyin.com/",
}


class HttpClient:
    """带重试和限速的会话封装。

    用法::

        c = HttpClient(cookie="ttwid=xxx; odin_tt=yyy")
        html = c.get("https://www.iesdouyin.com/share/video/123/").text
    """

    def __init__(
        self,
        timeout: float = 15.0,
        retries: int = 3,
        proxy: Optional[str] = None,
        cookie: Optional[str] = None,
        cookie_file: Optional[str | Path] = None,
        headers: Optional[Dict[str, str]] = None,
        delay: float = 0.4,
    ) -> None:
        self.timeout = timeout
        self.delay = delay
        self._last = 0.0

        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        if headers:
            self.session.headers.update(headers)

        # 连接层重试（只重试幂等的 GET）
        retry = Retry(
            total=retries,
            backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=16)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})

        raw_cookie = cookie
        if cookie_file:
            p = Path(cookie_file)
            if p.is_file():
                raw_cookie = p.read_text(encoding="utf-8").strip()
                log.debug("从 %s 载入 cookie", p)
        if raw_cookie:
            self.set_cookie(raw_cookie)

    # ------------------------------------------------------------------
    def set_cookie(self, raw: str) -> None:
        """接受两种格式：'a=1; b=2' 或浏览器导出的 JSON 数组。"""
        raw = (raw or "").strip()
        if not raw:
            return
        if raw.startswith("["):  # Cookie-Editor 导出的 JSON
            try:
                for item in json.loads(raw):
                    self.session.cookies.set(
                        item["name"], item["value"], domain=item.get("domain") or None
                    )
                return
            except Exception:  # noqa: BLE001
                log.warning("cookie JSON 解析失败，按字符串处理")
        for part in raw.split(";"):
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            self.session.cookies.set(k.strip(), v.strip())

    def _throttle(self) -> None:
        if self.delay <= 0:
            return
        gap = time.monotonic() - self._last
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last = time.monotonic()

    # ------------------------------------------------------------------
    def get(self, url: str, **kw: Any) -> requests.Response:
        self._throttle()
        kw.setdefault("timeout", self.timeout)
        kw.setdefault("allow_redirects", True)
        r = self.session.get(url, **kw)
        log.debug("GET %s -> %s (%s bytes)", url, r.status_code, len(r.content))
        return r

    def get_text(self, url: str, **kw: Any) -> str:
        r = self.get(url, **kw)
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        return r.text

    def get_json(self, url: str, **kw: Any) -> Any:
        r = self.get(url, **kw)
        r.raise_for_status()
        return r.json()
