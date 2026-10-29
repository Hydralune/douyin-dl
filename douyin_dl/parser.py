"""抖音解析核心。

后端策略
--------
browser      Playwright 起浏览器，在页面上下文里代发 detail 请求（主力）
share_page   老分享页 SSR，现已基本失效，保留作历史兜底
iteminfo     老 iteminfo 接口，已废弃，保留作最后兜底

为什么不自己算签名
------------------
实测浏览器发出的 detail 请求带这些风控参数：
  a_bogus(184字符) / x-secsdk-web-signature / uifid(query 参数) / fp / verifyFp / timestamp
纯 requests 即便把 29 个 cookie 全灌进去，仍然 403 Blocked by ArgusSecurityPlugin。
a_bogus 是 RC4 + 自定义 base64 + 浏览器指纹的复合算法，且平均每季度变更一次，
移植成本远高于收益。浏览器方案让页面自己的 SDK 算签名，天然免疫改版。

性能策略
--------
浏览器启动约 8 秒，但会话内单次 fetch 只要 1 秒左右。
所以真正重要的是：一次会话解析多个作品 + detail 结果缓存。

http_cookie 后端保留但默认关闭（见 BACKENDS），因为当前抖音要求 a_bogus。
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode

from .client import HttpClient, UA_DESKTOP, UA_MOBILE
from .models import Aweme, Media
from .resolver import normalize
from .session import (
    COOKIE_CACHE,
    DETAIL_API,
    DETAIL_PARAMS,
    BrowserSession,
    SessionError,
)

log = logging.getLogger("douyin_dl.parser")

SHARE_PAGE = "https://www.iesdouyin.com/share/video/{id}/"
SHARE_NOTE = "https://www.iesdouyin.com/share/note/{id}/"
ITEMINFO = "https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/"

PLAY_ENDPOINT = "https://www.iesdouyin.com/aweme/v1/play/?{q}"

# detail 接口的内容级过滤原因 -> 人话
FILTER_REASONS = {
    "core_dep": "作品不可访问（已删除 / 私密 / 作者关闭详情）",
    "risk": "作品被风控限制",
    "sensitive": "内容受限",
}


# ----------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------
def dig(obj: Any, *path: str, default: Any = None) -> Any:
    """安全按路径取值：dig(item, 'video', 'play_addr', 'url_list')。"""
    cur = obj
    for key in path:
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(key)
        elif isinstance(cur, list) and key.isdigit():
            idx = int(key)
            cur = cur[idx] if 0 <= idx < len(cur) else None
        else:
            return default
    return default if cur is None else cur


def pick(obj: Any, *names: str, default: Any = None) -> Any:
    """同层多个候选键名（兼容下划线与驼峰）。"""
    if not isinstance(obj, dict):
        return default
    for n in names:
        if n in obj and obj[n] not in (None, "", [], {}):
            return obj[n]
    return default


def json_after(text: str, marker: str) -> Optional[Any]:
    """从 marker 之后抠出第一个括号配平的 JSON 对象。"""
    i = text.find(marker)
    if i < 0:
        return None
    i = text.find("{", i)
    if i < 0:
        return None

    depth, in_str, esc = 0, False, False
    for j in range(i, len(text)):
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[i : j + 1])
                    except json.JSONDecodeError:
                        return None
    return None


def find_item_list(obj: Any, _depth: int = 0) -> Optional[List[Dict[str, Any]]]:
    """在任意嵌套结构里找到第一个非空 item_list。"""
    if _depth > 12 or obj is None:
        return None
    if isinstance(obj, dict):
        for key in ("item_list", "itemList", "aweme_list"):
            v = obj.get(key)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        for v in obj.values():
            got = find_item_list(v, _depth + 1)
            if got:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = find_item_list(v, _depth + 1)
            if got:
                return got
    return None


def no_watermark(url: str) -> str:
    """把带水印的播放地址改成无水印。"""
    if not url:
        return url
    if "playwm" in url:
        url = url.replace("/playwm/", "/play/").replace("playwm", "play")
    url = re.sub(r"[?&]watermark=1", "", url)
    return url


def build_play_url(uri: str, ratio: str = "1080p") -> str:
    q = urlencode({"video_id": uri, "ratio": ratio, "line": "0"})
    return PLAY_ENDPOINT.format(q=q)


def guess_quality(gear: str, bitrate: int) -> str:
    if gear:
        m = re.search(r"(2160|1440|1080|720|540|480|360)", gear)
        if m:
            return m.group(1) + "p"
    if bitrate >= 4_000_000:
        return "1080p"
    if bitrate >= 2_000_000:
        return "720p"
    if bitrate > 0:
        return "540p"
    return ""


# ----------------------------------------------------------------------
# 主类
# ----------------------------------------------------------------------
class DouyinParser:
    """可复用的抖音解析器。

    最简用法::

        from douyin_dl import DouyinParser
        p = DouyinParser()
        aweme = p.parse("https://v.douyin.com/xxxxx/")
        print(aweme.video.url)
        p.close()

    或者用上下文管理器（自动关浏览器）::

        with DouyinParser() as p:
            aweme = p.parse(url)
    """

    BACKENDS = ("browser", "share_page", "iteminfo")

    def __init__(
        self,
        client: Optional[HttpClient] = None,
        cookie: Optional[str] = None,
        cookie_file: Optional[str] = None,
        proxy: Optional[str] = None,
        prefer: Optional[str] = None,
        timeout: float = 15.0,
        headless: bool = True,
        browser_channel: str = "chrome",
        cookie_cache: Optional[str] = None,
        user_data_dir: Optional[str] = None,
        cdp_endpoint: Optional[str] = None,
        use_cache: bool = True,
        cache_ttl: int = 3600,
        cache_dir: Optional[str] = None,
        on_step: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.on_step = on_step
        self.client = client or HttpClient(
            cookie=cookie, cookie_file=cookie_file, proxy=proxy, timeout=timeout
        )
        self.prefer = prefer
        self.headless = headless
        self.browser_channel = browser_channel
        self.cookie_cache_path = cookie_cache
        self.user_data_dir = user_data_dir
        self.cdp_endpoint = cdp_endpoint
        self.use_cache = use_cache
        self.cache_ttl = cache_ttl
        self.cache_dir = (
            Path(cache_dir) if cache_dir else Path.home() / ".douyin_dl" / "detail_cache"
        )
        self._session: Optional[BrowserSession] = None
        self.last_errors: Dict[str, str] = {}

        # 如果外部直接给了 cookie，就跳过缓存
        if cookie or cookie_file:
            self._skip_cache = False
        else:
            self._skip_cache = True

        self._load_cached_cookies()

    # ------------------------------------------------------------------
    def __enter__(self) -> "DouyinParser":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except Exception:  # noqa: BLE001
                pass
            self._session = None

    # ------------------------------------------------------------------
    def _step(self, level: str, message: str) -> None:
        """向上层上报解析进度。level: info / ok / warn / err"""
        log.debug("[%s] %s", level, message)
        if self.on_step:
            try:
                self.on_step(level, message)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    def _load_cached_cookies(self) -> None:
        """把磁盘上的会话 cookie 灌进 requests。"""
        if self._skip_cache:
            return
        cookies = BrowserSession.load_cookie_cache(
            self.cookie_cache_path and __import__("pathlib").Path(self.cookie_cache_path)
        )
        if not cookies:
            return
        for c in cookies:
            try:
                self.client.session.cookies.set(
                    c["name"], c["value"], domain=c.get("domain", "").lstrip(".") or None
                )
            except Exception:  # noqa: BLE001
                pass
        log.debug("已载入缓存 cookie %d 个", len(cookies))

    def _session_or_raise(self) -> BrowserSession:
        if self._session is None:
            self._session = BrowserSession(
                headless=self.headless,
                channel=self.browser_channel,
                user_data_dir=self.user_data_dir,
                cdp_endpoint=self.cdp_endpoint,
            )
            self._session.ensure_ready()
        return self._session

    # ------------------------------------------------------------------
    # detail 结果缓存：同一作品短时间内重复解析直接命中磁盘
    # ------------------------------------------------------------------
    def _cache_path(self, aweme_id: str) -> Path:
        return self.cache_dir / f"{aweme_id}.json"

    def _cache_get(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        if not self.use_cache:
            return None
        p = self._cache_path(aweme_id)
        if not p.is_file():
            return None
        try:
            if time.time() - p.stat().st_mtime > self.cache_ttl:
                return None
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def _cache_put(self, aweme_id: str, item: Dict[str, Any]) -> None:
        if not self.use_cache:
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_path(aweme_id).write_text(
                json.dumps(item, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as e:  # noqa: BLE001
            log.debug("写缓存失败: %s", e)

    # ------------------------------------------------------------------
    def parse(self, text: str, backend: Optional[str] = None) -> Aweme:
        """解析任意输入（分享文案 / URL / 裸 ID），返回 Aweme。"""
        self._step("info", "识别输入内容…")
        aweme_id, _url = normalize(text, self.client)
        if not aweme_id:
            self._step("err", "没能从输入里提取到作品 ID")
            raise ValueError(f"无法从输入里提取作品 ID: {text!r}")
        self._step("ok", f"作品 ID: {aweme_id}")
        return self.parse_aweme_id(aweme_id, backend=backend)

    def parse_aweme_id(self, aweme_id: str, backend: Optional[str] = None) -> Aweme:
        if backend:
            order = [backend]
        else:
            order = [self.prefer] if self.prefer else []
            order += [b for b in self.BACKENDS if b != self.prefer]

        self.last_errors.clear()
        for name in order:
            fn = getattr(self, f"_be_{name}", None)
            if fn is None:
                continue
            self._step("info", f"尝试解析后端 [{name}]")
            try:
                item = fn(aweme_id)
                if not item:
                    self.last_errors[name] = "空结果"
                    self._step("warn", f"后端 [{name}] 返回空结果")
                    continue
                aweme = self._normalize(item)
                if not aweme.medias:
                    self.last_errors[name] = "无可下载资源"
                    self._step("warn", f"后端 [{name}] 未找到可下载资源")
                    continue
                aweme.backend = name
                self._step("ok", f"解析成功 [{name}] · {aweme.author_name} · {len(aweme.medias)} 项资源")
                log.info("解析成功 backend=%s id=%s", name, aweme_id)
                return aweme
            except Exception as e:  # noqa: BLE001
                self.last_errors[name] = f"{type(e).__name__}: {e}"
                self._step("warn", f"后端 [{name}] 失败: {e}")
                log.debug("后端 %s 失败: %s", name, e)

        detail = "; ".join(f"{k}={v}" for k, v in self.last_errors.items())
        self._step("err", "全部后端失败")
        raise RuntimeError(f"全部后端失败 (id={aweme_id}): {detail}")

    # ------------------------------------------------------------------
    # 后端 1：缓存 cookie + requests
    # ------------------------------------------------------------------
    def _be_http_cookie(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        params = dict(DETAIL_PARAMS, aweme_id=str(aweme_id))
        try:
            r = self.client.get(
                DETAIL_API,
                params=params,
                headers={
                    "User-Agent": UA_DESKTOP,
                    "Referer": f"https://www.douyin.com/video/{aweme_id}",
                    "Accept": "application/json, text/plain, */*",
                },
            )
        except Exception as e:  # noqa: BLE001
            log.debug("http_cookie 请求异常: %s", e)
            return None

        if r.status_code == 403:
            raise SessionError("cookie 失效（403 Uifid Not Found），需重建会话")
        if r.status_code != 200:
            return None
        try:
            data = r.json()
        except Exception:  # noqa: BLE001
            return None
        return self._extract_detail(data)

    # ------------------------------------------------------------------
    # 后端 2：浏览器代发
    # ------------------------------------------------------------------
    def _be_browser(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        cached = self._cache_get(aweme_id)
        if cached:
            self._step("ok", "命中本地缓存，跳过网络请求")
            log.debug("命中缓存 id=%s", aweme_id)
            return cached

        first_time = self._session is None
        self._step(
            "info",
            "启动浏览器并建立会话…（首次约 8 秒）" if first_time else "复用已有浏览器会话",
        )
        s = self._session_or_raise()
        self._step("ok", "浏览器会话就绪（UIFID / ttwid 已下发）")

        self._step("info", "请求作品详情接口…")
        data = s.fetch_detail(str(aweme_id))
        self._step("ok", "接口返回 200，开始解析数据")

        item = self._extract_detail(data)
        if item is not None:
            self._cache_put(aweme_id, item)
            self._step("ok", "数据结构解析完成，已写入缓存")
        return item

    @staticmethod
    def _extract_detail(data: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(data, dict):
            return None
        detail = data.get("aweme_detail")
        if isinstance(detail, dict):
            return detail
        lst = data.get("aweme_details")
        if isinstance(lst, list) and lst:
            return lst[0]

        # 明确的无数据情形
        fd = data.get("filter_detail") or {}
        reason = (fd or {}).get("filter_reason") or ""
        msg = (fd or {}).get("detail_msg") or ""
        if reason:
            hint = FILTER_REASONS.get(reason, reason)
            raise RuntimeError(f"{hint}{(' / ' + msg) if msg else ''} [{reason}]")
        return None

    # ------------------------------------------------------------------
    # 后端 3：分享页（历史兜底）
    # ------------------------------------------------------------------
    def _be_share_page(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        for tpl in (SHARE_PAGE, SHARE_NOTE):
            url = tpl.format(id=aweme_id)
            try:
                html = self.client.get_text(
                    url,
                    headers={"User-Agent": UA_MOBILE, "Referer": "https://www.douyin.com/"},
                )
            except Exception as e:  # noqa: BLE001
                log.debug("分享页 %s 请求失败: %s", url, e)
                continue

            data = json_after(html, "_ROUTER_DATA")
            if data:
                items = find_item_list(data)
                if items:
                    return items[0]
        return None

    # ------------------------------------------------------------------
    # 后端 4：老 iteminfo（已废弃）
    # ------------------------------------------------------------------
    def _be_iteminfo(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        url = ITEMINFO + "?" + urlencode({"item_ids": aweme_id})
        try:
            data = self.client.get_json(
                url, headers={"User-Agent": UA_MOBILE, "Referer": "https://www.douyin.com/"}
            )
        except Exception:  # noqa: BLE001
            return None
        if isinstance(data, dict):
            lst = data.get("item_list")
            if isinstance(lst, list) and lst:
                return lst[0]
        return None

    # ------------------------------------------------------------------
    # 归一化
    # ------------------------------------------------------------------
    def _normalize(self, item: Dict[str, Any]) -> Aweme:
        aweme_id = str(pick(item, "aweme_id", "awemeId", "id", default="") or "")
        desc = pick(item, "desc", "description", default="") or ""

        author = pick(item, "author", "authorInfo", default={}) or {}
        author_name = pick(author, "nickname", "nickName", "name", default="") or ""
        author_id = str(
            pick(author, "sec_uid", "secUid", "uid", "unique_id", default="") or ""
        )
        avatar = ""
        av = pick(author, "avatar_thumb", "avatarThumb", "avatar_medium", default={})
        if isinstance(av, dict):
            urls = pick(av, "url_list", "urlList", default=[])
            avatar = urls[0] if urls else ""

        video = pick(item, "video", default={}) or {}
        cover = ""
        cov = pick(video, "cover", "origin_cover", "dynamic_cover", default={}) or {}
        if isinstance(cov, dict):
            urls = pick(cov, "url_list", "urlList", default=[])
            cover = urls[0] if urls else ""

        duration = int(pick(video, "duration", default=0) or 0)
        create_time = int(pick(item, "create_time", "createTime", default=0) or 0)

        medias: List[Media] = []

        # ---------- 图集 ----------
        images = pick(item, "images", default=None)
        image_urls: List[str] = []
        if isinstance(images, list) and images:
            for img in images:
                if not isinstance(img, dict):
                    continue
                urls = (
                    pick(img, "url_list", "urlList", default=[])
                    or pick(img, "download_url_list", "downloadUrlList", default=[])
                )
                if urls:
                    image_urls.append(urls[-1])
        elif isinstance(images, dict):
            for img in pick(images, "images", "display_image", default=[]) or []:
                if isinstance(img, dict):
                    urls = pick(img, "url_list", "urlList", default=[])
                    if urls:
                        image_urls.append(urls[-1])

        if image_urls:
            for i, u in enumerate(image_urls, 1):
                medias.append(Media(kind="image", url=u, quality="origin", ext="jpg", index=i))
        else:
            # 所有清晰度档位都输出，按码率降序；medias[0] 就是最高清
            medias.extend(self._all_videos(video))

        # ---------- 音乐 ----------
        music_media = None
        music = pick(item, "music", default={}) or {}
        if isinstance(music, dict):
            play = pick(music, "play_url", "playUrl", default={}) or {}
            m_urls = pick(play, "url_list", "urlList", default=[]) if isinstance(play, dict) else []
            if m_urls:
                music_media = Media(
                    kind="audio",
                    url=m_urls[0],
                    ext="mp3",
                    extra={"title": pick(music, "title", default="")},
                )

        stats = pick(item, "statistics", default={}) or {}

        return Aweme(
            aweme_id=aweme_id,
            desc=desc,
            author_name=author_name,
            author_id=author_id,
            author_avatar=avatar,
            cover=cover,
            duration_ms=duration,
            create_time=create_time,
            medias=medias,
            music=music_media,
            statistics={
                "digg": pick(stats, "digg_count", default=0),
                "comment": pick(stats, "comment_count", default=0),
                "share": pick(stats, "share_count", default=0),
                "collect": pick(stats, "collect_count", default=0),
                "play": pick(stats, "play_count", default=0),
            },
            raw=item,
        )

    # ------------------------------------------------------------------
    def _all_videos(self, video: Dict[str, Any]) -> List[Media]:
        """把 bit_rate 里所有档位都转成 Media，按码率从高到低。

        同一清晰度可能有多条 CDN 线路，只保留码率最高那条，避免下拉框里
        出现一堆重复的 1080p。
        """
        if not isinstance(video, dict) or not video:
            return []

        scored: List[tuple[int, str, str]] = []  # (bitrate, quality, url)

        bit_rates = pick(video, "bit_rate", "bitRateList", default=None)
        if isinstance(bit_rates, list):
            for br in bit_rates:
                if not isinstance(br, dict):
                    continue
                addr = pick(br, "play_addr", "playAddr", default={}) or {}
                urls = (
                    pick(addr, "url_list", "urlList", default=[])
                    if isinstance(addr, dict) else []
                )
                if not urls:
                    continue
                rate = int(pick(br, "bit_rate", "bitRate", default=0) or 0)
                gear = str(pick(br, "gear_name", "gearName", default="") or "")
                quality = guess_quality(gear, rate) or "default"
                scored.append((rate, quality, no_watermark(urls[0])))

        # 没有 bit_rate 时退化到主播放地址
        if not scored:
            addr = pick(video, "play_addr", "playAddr", default={}) or {}
            urls = (
                pick(addr, "url_list", "urlList", default=[])
                if isinstance(addr, dict) else []
            )
            uri = str(pick(addr, "uri", default="") or "") if isinstance(addr, dict) else ""
            if urls:
                scored.append((0, "default", no_watermark(urls[0])))
            elif uri:
                for ratio in ("1080p", "720p", "540p"):
                    scored.append((0, ratio, build_play_url(uri, ratio)))

        # 排序 + 同清晰度去重（保留码率最高的）
        scored.sort(key=lambda x: x[0], reverse=True)
        seen: set[str] = set()
        out: List[Media] = []
        for rate, quality, url in scored:
            key = quality
            if key in seen:
                continue
            seen.add(key)
            m = Media(kind="video", url=url, quality=quality, ext="mp4")
            m.extra["bitrate"] = rate
            out.append(m)

        # 保证至少有一档
        if not out:
            addr = pick(video, "play_addr", "playAddr", default={}) or {}
            urls = (
                pick(addr, "url_list", "urlList", default=[])
                if isinstance(addr, dict) else []
            )
            if urls:
                out.append(Media(kind="video", url=no_watermark(urls[0]),
                                 quality="default", ext="mp4"))
        return out

    # ------------------------------------------------------------------
    def _best_video(self, video: Dict[str, Any]) -> Optional[Media]:
        """兼容旧接口：返回最高清那一档。"""
        videos = self._all_videos(video)
        return videos[0] if videos else None

    # ------------------------------------------------------------------
    def parse_many(self, texts: List[str]) -> List[Any]:
        """批量解析，单条失败不中断。"""
        out = []
        for t in texts:
            try:
                out.append((t, self.parse(t)))
            except Exception as e:  # noqa: BLE001
                out.append((t, e))
        return out
