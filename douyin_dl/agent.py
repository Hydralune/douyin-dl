"""Agent 调用层 —— 面向 AI Agent 的结构化入口。

设计约束
--------
1. **stdout 只能有 JSON**，任何日志/进度必须走 stderr，否则 agent 解析会炸
2. 每个结果都是自描述的：成功带文件绝对路径和字节数，失败带错误码
3. 不依赖交互：没有 input()，不会弹窗
4. 单个失败不影响批量里其它条目

调用::

    python -m douyin_dl agent "https://v.douyin.com/xxx/" --json
    python -m douyin_dl agent URL1 URL2 --json --out D:/videos --quality 720p
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import get as cfg_get
from .downloader import Downloader
from .history import History
from .models import Aweme
from .parser import DouyinParser
from .platforms import detect

log = logging.getLogger("douyin_dl.agent")

# 超过这个大小就提醒 agent 换发送方式（直传容易被聊天软件拒绝）
LARGE_FILE = 100 * 1024 * 1024

ERROR_CODES = {
    "NO_ID": "输入里没找到作品 ID",
    "UNSUPPORTED": "该平台尚未实现",
    "PARSE_FAILED": "解析失败（作品不可访问 / 风控 / 网络）",
    "DOWNLOAD_FAILED": "下载失败",
    "NO_MEDIA": "解析成功但没有可下载资源",
}


def _err(input_text: str, code: str, message: str) -> Dict[str, Any]:
    return {
        "ok": False,
        "input": input_text,
        "code": code,
        "error": message,
        "hint": ERROR_CODES.get(code, ""),
    }


def pick_media(aweme: Aweme, quality: str = "") -> Optional[int]:
    """按清晰度标签挑一档，返回 medias 索引；不指定则用最高档。"""
    if not quality:
        for i, m in enumerate(aweme.medias):
            if m.kind == "video":
                return i
        return 0 if aweme.medias else None
    q = quality.lower().strip()
    for i, m in enumerate(aweme.medias):
        label = (m.quality or "").lower()
        if q and (q in label or label in q):
            return i
    # 没命中就用最高档，并在结果里说明
    for i, m in enumerate(aweme.medias):
        if m.kind == "video":
            return i
    return 0 if aweme.medias else None


def file_list(paths: List[Path]) -> List[Dict[str, Any]]:
    out = []
    for p in paths:
        try:
            size = p.stat().st_size
        except Exception:  # noqa: BLE001
            size = 0
        out.append(
            {
                "path": str(p.resolve()),
                "name": p.name,
                "size": size,
                "kind": p.suffix.lstrip(".").lower() or "bin",
            }
        )
    return out


def run(
    inputs: List[str],
    out_dir: str | Path = "downloads",
    quality: str = "",
    music: bool = False,
    zip_images: bool = False,
    rate_limit_mb: float = 0,
    overwrite: bool = False,
    download: bool = True,
    headless: bool = True,
    channel: str = "chrome",
    profile: str = "",
    cdp: str = "",
    use_cache: bool = True,
    cache_ttl: int = 3600,
) -> Dict[str, Any]:
    """执行一批解析+下载，返回可直接 json.dumps 的结果。"""
    out_path = Path(out_dir).expanduser()
    out_path.mkdir(parents=True, exist_ok=True)

    history = History()
    started = time.time()
    results: List[Dict[str, Any]] = []

    parser = DouyinParser(
        headless=headless,
        browser_channel=channel or "chrome",
        user_data_dir=profile or None,
        cdp_endpoint=cdp or None,
        use_cache=use_cache,
        cache_ttl=cache_ttl,
        on_step=lambda level, msg: log.info("%s", msg),
    )
    dl = Downloader(
        out_dir=out_path,
        show_progress=False,
        overwrite=overwrite,
        rate_limit=int(rate_limit_mb * 1024 * 1024),
    )

    try:
        for raw in inputs:
            text = (raw or "").strip()
            if not text:
                continue
            t0 = time.time()

            # 平台预判：非抖音直接说明，不让它走完整个失败流程
            plat = detect(text)
            if plat is not None and not plat.ready:
                results.append(_err(text, "UNSUPPORTED", f"{plat.name}：{plat.note}"))
                continue

            try:
                aweme = parser.parse(text)
            except ValueError as e:
                results.append(_err(text, "NO_ID", str(e)))
                continue
            except Exception as e:  # noqa: BLE001
                results.append(_err(text, "PARSE_FAILED", str(e)))
                continue

            if not aweme.medias:
                results.append(_err(text, "NO_MEDIA", f"作品 {aweme.aweme_id} 无资源"))
                continue

            idx = pick_media(aweme, quality)
            chosen = aweme.medias[idx] if idx is not None else None
            entry: Dict[str, Any] = {
                "ok": True,
                "input": text,
                "aweme_id": aweme.aweme_id,
                "title": aweme.desc,
                "author": aweme.author_name,
                "author_id": aweme.author_id,
                "kind": "image" if aweme.is_image_post else "video",
                "duration": aweme.duration_text,
                "duration_ms": aweme.duration_ms,
                "cover": aweme.cover,
                "backend": aweme.backend,
                "quality": (chosen.quality if chosen else ""),
                "quality_requested": quality,
                "stats": aweme.statistics,
                "files": [],
                "total_bytes": 0,
                "large": False,
                "elapsed_s": 0.0,
            }
            if chosen and quality and quality.lower() not in (chosen.quality or "").lower():
                entry["quality_note"] = f"未找到 {quality}，已回退到 {chosen.quality}"

            if download:
                try:
                    files = dl.save(aweme, idx)
                    if zip_images and aweme.is_image_post:
                        z = dl.save_zip(aweme, delete_dir=True)
                        if z:
                            files.append(z)
                    if music:
                        m = dl.save_music(aweme)
                        if m:
                            files.append(m)

                    entry["files"] = file_list(files)
                    entry["total_bytes"] = sum(f["size"] for f in entry["files"])
                    entry["large"] = entry["total_bytes"] >= LARGE_FILE
                    history.add_from_aweme(
                        aweme, files, quality=entry["quality"], url=text
                    )
                except Exception as e:  # noqa: BLE001
                    results.append(_err(text, "DOWNLOAD_FAILED", str(e)))
                    continue

            entry["elapsed_s"] = round(time.time() - t0, 2)
            results.append(entry)
    finally:
        parser.close()

    ok_count = sum(1 for r in results if r.get("ok"))
    summary = {
        "total": len(results),
        "succeeded": ok_count,
        "failed": len(results) - ok_count,
        "bytes": sum(r.get("total_bytes", 0) for r in results),
        "elapsed_s": round(time.time() - started, 2),
        "out_dir": str(out_path.resolve()),
    }
    return {
        "ok": ok_count == len(results) and ok_count > 0,
        "summary": summary,
        "results": results,
    }


def run_from_config(inputs: List[str], overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """按配置文件 + 覆盖项执行。CLI 和 skill 都走这里，行为一致。"""
    from .config import load

    cfg = load()
    ov = overrides or {}
    kw = {
        "out_dir": ov.get("out_dir") or cfg_get(cfg, "download.out_dir", "downloads"),
        "quality": ov.get("quality") or cfg_get(cfg, "download.quality", ""),
        "music": ov.get("music", cfg_get(cfg, "download.music", False)),
        "zip_images": ov.get("zip_images", cfg_get(cfg, "download.zip_images", False)),
        "rate_limit_mb": ov.get("rate_limit_mb", cfg_get(cfg, "download.rate_limit_mb", 0)),
        "overwrite": ov.get("overwrite", cfg_get(cfg, "download.overwrite", False)),
        "download": ov.get("download", True),
        "headless": ov.get("headless", cfg_get(cfg, "browser.headless", True)),
        "channel": ov.get("channel", cfg_get(cfg, "browser.channel", "chrome")),
        "profile": ov.get("profile", cfg_get(cfg, "browser.profile", "")),
        "cdp": ov.get("cdp", cfg_get(cfg, "browser.cdp", "")),
        "use_cache": ov.get("use_cache", cfg_get(cfg, "cache.enabled", True)),
        "cache_ttl": ov.get("cache_ttl", cfg_get(cfg, "cache.ttl", 3600)),
    }
    return run(inputs, **kw)


def emit(result: Dict[str, Any], json_mode: bool, out=sys.stdout) -> int:
    """输出结果。JSON 模式下 stdout 只有这一个 JSON 对象。"""
    if json_mode:
        out.write(json.dumps(result, ensure_ascii=False, indent=None) + "\n")
        out.flush()
    else:
        s = result.get("summary", {})
        for r in result.get("results", []):
            if r.get("ok"):
                print(f"✓ {r['author']} · {r['title'][:40]}")
                for f in r.get("files", []):
                    print(f"   {f['path']}  ({f['size'] / 1024 / 1024:.1f}MB)")
            else:
                print(f"✗ {r.get('input', '')[:50]} → {r.get('error')}")
        print(
            f"\n成功 {s.get('succeeded', 0)} / 失败 {s.get('failed', 0)} "
            f"/ 共 {s.get('total', 0)}，耗时 {s.get('elapsed_s', 0)}s"
        )
        print(f"输出目录 {s.get('out_dir', '')}")
    return 0 if result.get("ok") else 1
