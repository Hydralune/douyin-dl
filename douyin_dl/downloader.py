"""下载器 —— 断点续传、进度条、图集打包、并发。与平台解耦。"""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from tqdm import tqdm

from .client import HttpClient
from .models import Aweme, Media, safe_filename

log = logging.getLogger("douyin_dl.downloader")

CHUNK = 1 << 16  # 64 KiB


class DownloadCancelled(Exception):
    """用户主动取消了下载。"""


class DownloadControl:
    """下载的暂停 / 继续 / 取消开关，线程安全。

    用法::

        ctl = DownloadControl()
        dl.save(aweme, control=ctl)   # 后台线程里跑
        ctl.pause()                   # 暂停
        ctl.resume()                  # 继续
        ctl.cancel()                  # 取消，抛 DownloadCancelled
    """

    def __init__(self) -> None:
        self._resume = threading.Event()
        self._resume.set()           # set = 允许继续
        self._cancel = threading.Event()

    def pause(self) -> None:
        self._resume.clear()

    def resume(self) -> None:
        self._resume.set()

    def cancel(self) -> None:
        self._cancel.set()
        self._resume.set()           # 解除暂停阻塞，好让它走到取消检查

    @property
    def paused(self) -> bool:
        return not self._resume.is_set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def checkpoint(self) -> None:
        """每读完一块调用：暂停时阻塞，取消时抛异常。"""
        while not self._resume.wait(0.2):
            if self._cancel.is_set():
                break
        if self._cancel.is_set():
            raise DownloadCancelled("用户取消")


class Downloader:
    """通用下载器。

    用法::

        dl = Downloader(out_dir="downloads")
        files = dl.save(aweme)          # 视频 -> 单个 mp4；图集 -> 目录
        dl.save_zip(aweme)              # 图集额外打包 zip
    """

    def __init__(
        self,
        out_dir: str | Path = "downloads",
        client: Optional[HttpClient] = None,
        workers: int = 4,
        overwrite: bool = False,
        resume: bool = True,
        referer: str = "https://www.douyin.com/",
        show_progress: bool = True,
        on_progress: Optional[Callable[[str, int, int], None]] = None,
        rate_limit: int = 0,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.client = client or HttpClient()
        self.workers = max(1, workers)
        self.overwrite = overwrite
        self.resume = resume
        self.referer = referer
        self.show_progress = show_progress
        self.on_progress = on_progress
        self.rate_limit = max(0, int(rate_limit))   # 字节/秒，0 = 不限速

    # ------------------------------------------------------------------
    def _fetch_to(
        self, url: str, dest: Path, control: Optional[DownloadControl] = None
    ) -> Path:
        """单个文件下载，支持 Range 续传 + 暂停/取消。"""
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")

        if dest.exists() and not self.overwrite:
            log.debug("已存在，跳过: %s", dest.name)
            return dest

        if control:
            control.checkpoint()

        headers = {"Referer": self.referer, "User-Agent": "Mozilla/5.0"}
        existing = part.stat().st_size if part.exists() else 0
        if existing and self.resume:
            headers["Range"] = f"bytes={existing}-"

        r = self.client.session.get(
            url, headers=headers, stream=True, timeout=60, allow_redirects=True
        )

        # 服务器不认 Range，从零来
        if existing and r.status_code == 200:
            existing = 0
            part.unlink(missing_ok=True)
        r.raise_for_status()

        total = int(r.headers.get("Content-Length", 0) or 0) + existing
        mode = "ab" if existing else "wb"

        bar = tqdm(
            total=total or None,
            initial=existing,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc=dest.name[:28],
            disable=not self.show_progress,
            leave=False,
        )
        # 自己维护已写入字节数。
        # 不能用 bar.n：tqdm 在 disable=True 时 update() 直接 return，n 永远是 0，
        # 会导致上层拿到的进度恒为 0（GUI 进度条会变成假进度）。
        written = existing
        t_start = time.monotonic()
        try:
            with part.open(mode) as fh:
                for chunk in r.iter_content(CHUNK):
                    if control:
                        control.checkpoint()
                    if not chunk:
                        continue
                    fh.write(chunk)
                    written += len(chunk)
                    bar.update(len(chunk))
                    if self.on_progress:
                        try:
                            self.on_progress(dest.name, written, total)
                        except Exception:  # noqa: BLE001
                            pass
                    # 限速：按已下载量算"应该花多久"，超前的部分睡掉
                    if self.rate_limit > 0:
                        expected = (written - existing) / self.rate_limit
                        actual = time.monotonic() - t_start
                        if expected > actual:
                            time.sleep(min(expected - actual, 1.0))
        except DownloadCancelled:
            bar.close()
            r.close()
            # 保留 .part 供下次续传；是否清理由上层决定
            log.info("已取消: %s（已下 %s 字节保留在 .part）", dest.name, part.stat().st_size if part.exists() else 0)
            raise
        finally:
            bar.close()
            try:
                r.close()
            except Exception:  # noqa: BLE001
                pass

        part.replace(dest)
        return dest

    # ------------------------------------------------------------------
    def _base_name(self, aweme: Aweme) -> str:
        author = safe_filename(aweme.author_name, 20, "unknown")
        title = aweme.title(50)
        return f"{author}_{title}_{aweme.aweme_id}"

    def save(
        self,
        aweme: Aweme,
        media_index: Optional[int] = None,
        control: Optional[DownloadControl] = None,
    ) -> List[Path]:
        """保存一个作品。视频返回 [mp4]，图集返回一堆 jpg。

        media_index 指定用 aweme.medias 里哪一档（GUI 的清晰度选择用）；
        control 传入后可随时暂停 / 继续 / 取消。
        """
        if aweme.is_image_post:
            return self._save_images(aweme, control=control)
        return self._save_video(aweme, media_index, control=control)

    def _save_video(
        self,
        aweme: Aweme,
        media_index: Optional[int] = None,
        control: Optional[DownloadControl] = None,
    ) -> List[Path]:
        media: Optional[Media] = None
        if media_index is not None and 0 <= media_index < len(aweme.medias):
            cand = aweme.medias[media_index]
            if cand.kind == "video":
                media = cand
        if media is None:
            media = aweme.video
        if not media:
            return []
        name = self._base_name(aweme)
        q = f"_{media.quality}" if media.quality and media.quality != "default" else ""
        dest = self.out_dir / f"{name}{q}.{media.ext or 'mp4'}"
        return [self._fetch_to(media.url, dest, control=control)]

    def _save_images(
        self, aweme: Aweme, control: Optional[DownloadControl] = None
    ) -> List[Path]:
        name = self._base_name(aweme)
        folder = self.out_dir / name
        folder.mkdir(parents=True, exist_ok=True)

        images: List[Media] = aweme.images
        results: List[Path] = []

        def job(m: Media) -> Optional[Path]:
            ext = m.ext or "jpg"
            dest = folder / f"{m.index:02d}.{ext}"
            try:
                return self._fetch_to(m.url, dest, control=control)
            except DownloadCancelled:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("第 %d 张失败: %s", m.index, e)
                return None

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(job, m): m for m in images}
            for fut in as_completed(futures):
                got = fut.result()
                if got:
                    results.append(got)

        results.sort()
        return results

    # ------------------------------------------------------------------
    def save_zip(self, aweme: Aweme, delete_dir: bool = False) -> Optional[Path]:
        """把图集目录打成 zip。"""
        if not aweme.is_image_post:
            return None
        name = self._base_name(aweme)
        folder = self.out_dir / name
        if not folder.is_dir():
            return None
        zip_path = self.out_dir / f"{name}.zip"
        if zip_path.exists() and not self.overwrite:
            return zip_path
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
            for f in sorted(folder.iterdir()):
                if f.is_file():
                    zf.write(f, f.name)
        if delete_dir:
            shutil.rmtree(folder, ignore_errors=True)
        return zip_path

    def save_music(self, aweme: Aweme) -> Optional[Path]:
        if not aweme.music:
            return None
        name = self._base_name(aweme)
        dest = self.out_dir / f"{name}_music.{aweme.music.ext or 'mp3'}"
        try:
            return self._fetch_to(aweme.music.url, dest)
        except Exception as e:  # noqa: BLE001
            log.warning("音乐下载失败: %s", e)
            return None

    # ------------------------------------------------------------------
    def save_all(
        self, awemes: Iterable[Aweme], zip_images: bool = True
    ) -> List[Path]:
        """批量保存，返回全部落盘文件。"""
        out: List[Path] = []
        for a in awemes:
            try:
                files = self.save(a)
                out.extend(files)
                if zip_images and a.is_image_post:
                    z = self.save_zip(a, delete_dir=True)
                    if z:
                        out.append(z)
                log.info("完成 %s -> %d 个文件", a.aweme_id, len(files))
            except Exception as e:  # noqa: BLE001
                log.error("保存 %s 失败: %s", a.aweme_id, e)
        return out


def merge_av(video: Path, audio: Path, out: Optional[Path] = None) -> Optional[Path]:
    """音视频分离时用 ffmpeg 合并（B站/YouTube 会用到，抖音用不上）。"""
    import subprocess

    if not shutil.which("ffmpeg"):
        log.error("未找到 ffmpeg")
        return None
    out = out or video.with_name(video.stem + "_merged.mp4")
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(video), "-i", str(audio),
        "-c", "copy", "-map", "0:v:0", "-map", "1:a:0", str(out),
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        log.error("ffmpeg 合并失败: %s", r.stderr.decode("utf-8", "ignore")[:400])
        return None
    return out
