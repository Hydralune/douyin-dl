"""浏览器会话 —— 抖音签名的免逆向方案。

为什么需要浏览器
----------------
抖音 2024 年后把三个纯 HTTP 入口全部封死：
  - 分享页 SSR 数据被剥离，只剩空壳
  - /aweme/v1/web/aweme/detail/ 需要 Argus 风控的 UIFID cookie + a_bogus 签名
  - 老 iteminfo 接口已废弃返回空

但浏览器里页面自己会带上全部合法签名去请求详情接口。
所以策略是：**让浏览器建立会话并代发请求，我们只截取 JSON**。

更进一步：会话 cookie 导出后，短时间内可以脱离浏览器直接走 requests，
这样绝大多数请求是毫秒级的，只有会话过期才重新起浏览器。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("douyin_dl.session")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-infobars",
    "--disable-dev-shm-usage",
]

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = window.chrome || {runtime: {}};
"""

DETAIL_JS = """
async (id) => {
  const qs = new URLSearchParams({
    device_platform: 'webapp', aid: '6383', channel: 'channel_pc_web',
    aweme_id: id, pc_client_type: '1', version_code: '190500',
    version_name: '19.5.0', cookie_enabled: 'true', platform: 'PC',
    browser_language: 'zh-CN', browser_platform: 'Win32',
    browser_name: 'Chrome', browser_version: '131.0.0.0',
    screen_width: '1440', screen_height: '900',
    browser_online: 'true', engine_name: 'Blink', engine_version: '131.0.0.0',
    os_name: 'Windows', os_version: '10',
    cpu_core_num: '8', device_memory: '8', effective_type: '4g',
    round_trip_time: '50'
  });
  const r = await fetch('/aweme/v1/web/aweme/detail/?' + qs, {
    credentials: 'include',
    headers: {'Accept': 'application/json, text/plain, */*'}
  });
  return {status: r.status, body: await r.text()};
}
"""

DETAIL_API = "https://www.douyin.com/aweme/v1/web/aweme/detail/"

DETAIL_PARAMS = {
    "device_platform": "webapp",
    "aid": "6383",
    "channel": "channel_pc_web",
    "pc_client_type": "1",
    "version_code": "190500",
    "version_name": "19.5.0",
    "cookie_enabled": "true",
    "platform": "PC",
    "browser_language": "zh-CN",
    "browser_platform": "Win32",
    "browser_name": "Chrome",
    "browser_version": "131.0.0.0",
    "screen_width": "1440",
    "screen_height": "900",
    "browser_online": "true",
    "engine_name": "Blink",
    "engine_version": "131.0.0.0",
    "os_name": "Windows",
    "os_version": "10",
    "cpu_core_num": "8",
    "device_memory": "8",
    "effective_type": "4g",
    "round_trip_time": "50",
}

COOKIE_CACHE = Path.home() / ".douyin_dl" / "cookies.json"
COOKIE_TTL = 30 * 60  # 30 分钟


class SessionError(RuntimeError):
    pass


class BrowserSession:
    """一次启动，多次复用。用作上下文管理器或手动 close()。

    用法::

        with BrowserSession() as s:
            data = s.fetch_detail("7700973189773479183")
            s.save_cookie_cache()
    """

    def __init__(
        self,
        headless: bool = True,
        channel: str = "chrome",
        timeout: int = 60,
        warmup_url: str = "https://www.douyin.com/",
        user_data_dir: Optional[str | Path] = None,
        cdp_endpoint: Optional[str] = None,
    ) -> None:
        self.headless = headless
        self.channel = channel
        self.timeout = timeout
        self.warmup_url = warmup_url
        self.user_data_dir = Path(user_data_dir) if user_data_dir else None
        self.cdp_endpoint = cdp_endpoint

        self._pw = None
        self._browser = None
        self._ctx = None
        self._page = None
        self._ready = False
        self._owns_browser = True   # False = 接管了外部浏览器，不能关

    # ------------------------------------------------------------------
    def __enter__(self) -> "BrowserSession":
        self.ensure_ready()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    def ensure_ready(self) -> None:
        """确保浏览器已启动且会话可用（幂等）。"""
        if self._ready and self._page is not None:
            return

        from playwright.sync_api import sync_playwright

        if self._pw is None:
            self._pw = sync_playwright().start()

        # ---- 模式 A：接管已运行的 Chrome（带登录态）----
        if self.cdp_endpoint:
            try:
                self._browser = self._pw.chromium.connect_over_cdp(self.cdp_endpoint)
                self._ctx = (
                    self._browser.contexts[0]
                    if self._browser.contexts
                    else self._browser.new_context()
                )
                self._owns_browser = False
                log.info("已接管外部浏览器 %s", self.cdp_endpoint)
            except Exception as e:  # noqa: BLE001
                raise SessionError(
                    f"连接 CDP 失败 ({self.cdp_endpoint}): {e}\n"
                    f"请用 --remote-debugging-port=9222 启动 Chrome"
                ) from e
            self._page = self._ctx.new_page()
            self._page.set_default_timeout(self.timeout * 1000)
            self._warmup()
            return

        # ---- 模式 B：自己启动浏览器 ----
        # 优先用系统 Chrome；失败再退回 playwright 自带 chromium
        last_err: Optional[Exception] = None
        for kwargs in (
            {"channel": self.channel},
            {"channel": "msedge"},
            {},
        ):
            try:
                if self.user_data_dir:
                    self._ctx = self._pw.chromium.launch_persistent_context(
                        str(self.user_data_dir),
                        headless=self.headless,
                        args=LAUNCH_ARGS,
                        user_agent=UA,
                        locale="zh-CN",
                        timezone_id="Asia/Shanghai",
                        viewport={"width": 1440, "height": 900},
                        **kwargs,
                    )
                    self._browser = None
                else:
                    self._browser = self._pw.chromium.launch(
                        headless=self.headless, args=LAUNCH_ARGS, **kwargs
                    )
                    self._ctx = self._browser.new_context(
                        user_agent=UA,
                        locale="zh-CN",
                        timezone_id="Asia/Shanghai",
                        viewport={"width": 1440, "height": 900},
                    )
                self._ctx.add_init_script(STEALTH_JS)
                log.debug("浏览器已启动: %s", kwargs or "bundled chromium")
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                self._browser = None
                self._ctx = None
                continue

        if self._ctx is None:
            raise SessionError(
                f"无法启动浏览器: {last_err}. "
                f"请安装 Chrome，或执行 python -m playwright install chromium"
            )

        self._page = self._ctx.new_page()
        self._page.set_default_timeout(self.timeout * 1000)
        self._warmup()

    # ------------------------------------------------------------------
    def _warmup(self) -> None:
        """访问首页，让服务端下发 UIFID / ttwid / msToken 等风控 cookie。"""
        assert self._page is not None and self._ctx is not None
        try:
            self._page.goto(
                self.warmup_url, wait_until="domcontentloaded", timeout=self.timeout * 1000
            )
            self._page.wait_for_timeout(2500)
        except Exception as e:  # noqa: BLE001
            log.warning("首页预热失败（继续尝试）: %s", e)

        names = {c["name"] for c in self._ctx.cookies()}
        log.info(
            "会话就绪，cookie %d 个 (UIFID=%s)",
            len(names),
            "UIFID_TEMP" in names or "UIFID" in names,
        )
        self._ready = True

    # ------------------------------------------------------------------
    def fetch_detail(self, aweme_id: str) -> Dict[str, Any]:
        """在页面上下文里请求 detail 接口，返回解析后的 JSON。"""
        self.ensure_ready()
        assert self._page is not None

        res = self._page.evaluate(DETAIL_JS, str(aweme_id))
        status, body = res.get("status"), res.get("body", "")

        if status != 200:
            # 会话可能被判失效，重建一次
            log.warning("detail 返回 %s，重建会话后重试", status)
            self.reset()
            res = self._page.evaluate(DETAIL_JS, str(aweme_id))  # type: ignore[union-attr]
            status, body = res.get("status"), res.get("body", "")

        if status != 200:
            raise SessionError(f"detail 接口返回 {status}: {body[:200]}")

        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise SessionError(f"detail 返回非 JSON: {body[:200]}") from e

    # ------------------------------------------------------------------
    def export_cookies(self) -> List[Dict[str, Any]]:
        self.ensure_ready()
        assert self._ctx is not None
        return self._ctx.cookies()

    def save_cookie_cache(self, path: Optional[Path] = None) -> Path:
        path = Path(path) if path else COOKIE_CACHE
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"ts": time.time(), "cookies": self.export_cookies()}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("cookie 已缓存 -> %s", path)
        return path

    @staticmethod
    def load_cookie_cache(path: Optional[Path] = None) -> Optional[List[Dict[str, Any]]]:
        path = Path(path) if path else COOKIE_CACHE
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        if time.time() - data.get("ts", 0) > COOKIE_TTL:
            log.debug("cookie 缓存已过期")
            return None
        return data.get("cookies") or None

    # ------------------------------------------------------------------
    def reset(self) -> None:
        """丢弃当前上下文，下次调用重建。"""
        try:
            if self._page is not None:
                self._page.close()
        except Exception:  # noqa: BLE001
            pass
        if self._owns_browser:
            try:
                if self._ctx is not None:
                    self._ctx.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                if self._browser is not None:
                    self._browser.close()
            except Exception:  # noqa: BLE001
                pass
        self._ctx = self._browser = self._page = None
        self._ready = False

    def close(self) -> None:
        self.reset()
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._pw = None
