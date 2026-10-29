"""配置加载 —— CLI 参数 > 环境变量 > 配置文件 > 默认值。

配置文件查找顺序（找到就用，后面的不覆盖前面的显式值）：
  1. 命令行 --config 指定的路径
  2. 当前目录 ./config.toml
  3. ~/.douyin_dl/config.toml

环境变量统一前缀 DOUYIN_DL_，用双下划线表示层级：
  DOUYIN_DL_DOWNLOAD__OUT_DIR=D:\\videos
  DOUYIN_DL_BROWSER__HEADLESS=0
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULTS: Dict[str, Any] = {
    "download": {
        "out_dir": "downloads",
        "quality": "",          # 空 = 最高清
        "music": False,
        "zip_images": False,
        "rate_limit_mb": 0,     # 0 = 不限速
        "overwrite": False,
    },
    "browser": {
        "headless": True,
        "channel": "chrome",    # chrome / msedge / ""（用 playwright 自带 chromium）
        "profile": "",          # 持久化登录态目录
        "cdp": "",              # 接管已开调试端口的 Chrome
    },
    "agent": {
        "json": True,           # stdout 只输出 JSON，日志走 stderr
        "timeout": 600,         # 单条总超时（秒）
    },
    "cache": {
        "enabled": True,
        "ttl": 3600,
    },
}

ENV_PREFIX = "DOUYIN_DL_"
CONFIG_NAME = "config.toml"
HOME_CONFIG = Path.home() / ".douyin_dl" / CONFIG_NAME


def _deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _read_toml(path: Path) -> Dict[str, Any]:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _from_env() -> Dict[str, Any]:
    """DOUYIN_DL_SECTION__KEY=value → {"section": {"key": value}}"""
    out: Dict[str, Any] = {}
    for key, raw in os.environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        rest = key[len(ENV_PREFIX) :]
        if "__" not in rest:
            continue
        section, _, name = rest.partition("__")
        val = _coerce(raw)
        out.setdefault(section.lower(), {})[name.lower()] = val
    return out


def _coerce(raw: str) -> Any:
    low = raw.strip().lower()
    if low in ("1", "true", "yes", "on"):
        return True
    if low in ("0", "false", "no", "off"):
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def find_config_file(explicit: Optional[str] = None) -> Optional[Path]:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    for p in (Path.cwd() / CONFIG_NAME, HOME_CONFIG):
        if p.is_file():
            return p
    return None


def load(explicit: Optional[str] = None) -> Dict[str, Any]:
    """返回合并后的配置。"""
    cfg = dict(DEFAULTS)
    path = find_config_file(explicit)
    if path:
        cfg = _deep_merge(cfg, _read_toml(path))
    cfg = _deep_merge(cfg, _from_env())
    return cfg


def get(cfg: Dict[str, Any], dotted: str, default: Any = None) -> Any:
    """cfg, 'download.out_dir'"""
    cur: Any = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


SAMPLE = """\
# douyin-dl 配置样例 —— 复制到项目根目录改名 config.toml 即可生效
# 优先级：命令行参数 > 环境变量(DOUYIN_DL_*) > 本文件 > 内置默认值

[download]
out_dir = "downloads"      # 输出目录，可写绝对路径
quality = ""               # 空=最高清；也可写 1080p / 720p / 540p
music = false              # 是否同时下背景音乐
zip_images = false         # 图集是否打包 zip
rate_limit_mb = 0          # 限速 MB/s，0 = 不限
overwrite = false          # 已存在文件是否覆盖

[browser]
headless = true            # false = 显示浏览器窗口（首次登录用）
channel = "chrome"         # chrome / msedge / ""（空=用 playwright 自带 chromium）
profile = ""               # 登录态目录，跑过 tools/login.py 后填这里
cdp = ""                   # 或接管已开的 Chrome：http://127.0.0.1:9222

[agent]
json = true                # agent 调用时 stdout 只输出 JSON
timeout = 600              # 单条超时秒数

[cache]
enabled = true             # 作品详情缓存
ttl = 3600                 # 缓存有效期秒数
"""
