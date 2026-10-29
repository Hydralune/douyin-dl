"""命令行入口。"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .client import HttpClient
from .downloader import Downloader
from .models import Aweme
from .parser import DouyinParser

console = Console()


def _setup_log(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )


def _make_parser(args: argparse.Namespace) -> DouyinParser:
    return DouyinParser(
        cookie_file=getattr(args, "cookie_file", None),
        cookie=getattr(args, "cookie", None),
        proxy=getattr(args, "proxy", None),
        prefer=getattr(args, "backend", None),
        timeout=getattr(args, "timeout", 15.0),
        headless=not getattr(args, "headed", False),
        user_data_dir=getattr(args, "profile", None),
        cdp_endpoint=getattr(args, "cdp", None),
        use_cache=not getattr(args, "no_cache", False),
    )


def _show(aweme: Aweme) -> None:
    t = Table(show_header=False, box=None, padding=(0, 1))
    t.add_column(style="cyan", no_wrap=True)
    t.add_column()
    t.add_row("ID", aweme.aweme_id)
    t.add_row("类型", "图集" if aweme.is_image_post else "视频")
    t.add_row("作者", f"{aweme.author_name} ({aweme.author_id})")
    t.add_row("标题", aweme.desc[:120] or "-")
    if aweme.duration_ms:
        t.add_row("时长", aweme.duration_text)
    t.add_row("后端", aweme.backend)
    st = aweme.statistics
    if any(st.values()):
        t.add_row(
            "数据",
            f"赞 {st.get('digg', 0)} / 评 {st.get('comment', 0)} / 转 {st.get('share', 0)}",
        )
    console.print(Panel(t, title="解析结果", border_style="green"))

    mt = Table(title="可下载资源", box=None)
    mt.add_column("#", justify="right")
    mt.add_column("类型")
    mt.add_column("清晰度")
    mt.add_column("地址", overflow="fold")
    for i, m in enumerate(aweme.medias, 1):
        mt.add_row(str(i), m.kind, m.quality or "-", m.url[:110])
    if aweme.music:
        mt.add_row("♪", "audio", "-", aweme.music.url[:110])
    console.print(mt)


# ----------------------------------------------------------------------
def cmd_parse(args: argparse.Namespace) -> int:
    p = _make_parser(args)
    try:
        aweme = p.parse(args.target)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]解析失败:[/red] {e}")
        return 1
    finally:
        p.close()

    if args.json:
        print(json.dumps(aweme.to_dict(), ensure_ascii=False, indent=2))
    else:
        _show(aweme)
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    p = _make_parser(args)
    dl = Downloader(
        out_dir=args.out,
        client=p.client,
        workers=args.workers,
        overwrite=args.overwrite,
    )

    targets = list(args.targets)
    if args.file:
        lines = Path(args.file).read_text(encoding="utf-8").splitlines()
        targets += [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]

    total = len(targets)
    ok = fail = 0
    try:
        for idx, target in enumerate(targets, 1):
            console.rule(f"[{idx}/{total}] {target[:70]}")
            try:
                aweme = p.parse(target)
                _show(aweme)
                files = dl.save(aweme)
                if args.zip and aweme.is_image_post:
                    z = dl.save_zip(aweme, delete_dir=True)
                    if z:
                        files.append(z)
                if args.music:
                    m = dl.save_music(aweme)
                    if m:
                        files.append(m)
                for f in files:
                    console.print(f"  [green]✓[/green] {f}")
                ok += 1
            except Exception as e:  # noqa: BLE001
                console.print(f"  [red]✗ {e}[/red]")
                fail += 1
    finally:
        p.close()

    console.rule()
    console.print(f"成功 [green]{ok}[/green] / 失败 [red]{fail}[/red] / 共 {total}")
    console.print(f"输出目录: [bold]{Path(args.out).resolve()}[/bold]")
    return 0 if fail == 0 else 1


def cmd_from_json(args: argparse.Namespace) -> int:
    """从 parse --json 的输出里下载（方便和别的程序串起来）。"""
    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else [data]
    dl = Downloader(out_dir=args.out, overwrite=args.overwrite)
    for item in items:
        for m in item.get("medias", []):
            name = f"{item.get('author', {}).get('name', 'x')}_{item['aweme_id']}_{m.get('quality', '')}.{m.get('ext', 'bin')}"
            dest = Path(args.out) / "".join(c for c in name if c not in '\\/:*?"<>|')
            dl._fetch_to(m["url"], dest)
            console.print(f"  [green]✓[/green] {dest}")
    return 0


def cmd_agent(args: argparse.Namespace) -> int:
    """面向 AI Agent 的结构化入口：stdout 只有 JSON。"""
    from .agent import emit, run_from_config

    ov: dict = {}
    if args.out is not None:
        ov["out_dir"] = args.out
    if args.quality is not None:
        ov["quality"] = args.quality
    if args.music is not None:
        ov["music"] = args.music
    if args.zip is not None:
        ov["zip_images"] = args.zip
    if args.no_download is not None:
        ov["download"] = not args.no_download
    if args.headed is not None:
        ov["headless"] = not args.headed
    if args.profile is not None:
        ov["profile"] = args.profile
    if args.cdp is not None:
        ov["cdp"] = args.cdp
    if args.rate is not None:
        ov["rate_limit_mb"] = args.rate
    if args.no_cache is not None:
        ov["use_cache"] = not args.no_cache

    result = run_from_config(list(args.targets), ov)
    return emit(result, json_mode=not args.text)


def cmd_envcheck(args: argparse.Namespace) -> int:
    from .envcheck import main as env_main

    argv = []
    if args.json:
        argv.append("--json")
    if args.no_network:
        argv.append("--no-network")
    if args.profile:
        argv += ["--profile", args.profile]
    return env_main(argv)


def cmd_config(args: argparse.Namespace) -> int:
    """打印生效配置，或输出配置样例。"""
    from . import config as cfgmod

    if args.sample:
        print(cfgmod.SAMPLE)
        return 0
    cfg = cfgmod.load(args.file)
    src = cfgmod.find_config_file(args.file)
    if not args.json:
        console.print(f"[cyan]配置文件:[/cyan] {src or '（未找到，使用内置默认值）'}")
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    return 0


# ----------------------------------------------------------------------
def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="douyin",
        description="抖音无水印解析下载工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  douyin parse "https://v.douyin.com/xxxxx/"
  douyin parse 7300000000000000000 --json
  douyin dl "https://v.douyin.com/xxxxx/" -o D:\\videos
  douyin dl --file urls.txt -o D:\\videos --zip
  douyin dl 7300000000000000000 --cookie-file cookies.txt
""",
    )
    ap.add_argument("-v", "--verbose", action="store_true", help="打开调试日志")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--cookie", help="直接传 cookie 字符串")
    common.add_argument("--cookie-file", help="cookie 文件路径（一行 a=1; b=2 或 JSON）")
    common.add_argument("--proxy", help="代理，如 http://127.0.0.1:7890")
    common.add_argument("--backend", choices=["browser", "share_page", "iteminfo"],
                        help="指定解析后端，默认自动降级")
    common.add_argument("--timeout", type=float, default=15.0)
    common.add_argument("--headed", action="store_true",
                        help="显示浏览器窗口（首次扫码登录时用）")
    common.add_argument("--profile", metavar="DIR",
                        help="持久化浏览器 profile 目录，登录态会保存下来")
    common.add_argument("--cdp", metavar="URL",
                        help="接管已运行的 Chrome，如 http://127.0.0.1:9222（复用其登录态）")
    common.add_argument("--no-cache", action="store_true", help="禁用 detail 结果缓存")

    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("parse", aliases=["p"], parents=[common], help="只解析不下载")
    p1.add_argument("target", help="分享文案 / URL / 作品 ID")
    p1.add_argument("--json", action="store_true", help="输出 JSON")
    p1.set_defaults(func=cmd_parse)

    p2 = sub.add_parser("dl", aliases=["download"], parents=[common], help="解析并下载")
    p2.add_argument("targets", nargs="*", help="一个或多个链接/ID")
    p2.add_argument("-o", "--out", default="downloads", help="输出目录")
    p2.add_argument("--file", help="从文本文件读取链接（一行一个）")
    p2.add_argument("--zip", action="store_true", help="图集额外打包 zip")
    p2.add_argument("--music", action="store_true", help="同时下载背景音乐")
    p2.add_argument("--workers", type=int, default=4, help="图集并发数")
    p2.add_argument("--overwrite", action="store_true", help="覆盖已存在文件")
    p2.set_defaults(func=cmd_download)

    p3 = sub.add_parser("from-json", help="根据 parse --json 的结果下载")
    p3.add_argument("file")
    p3.add_argument("-o", "--out", default="downloads")
    p3.add_argument("--overwrite", action="store_true")
    p3.set_defaults(func=cmd_from_json)

    # ---- agent：结构化输出，给 AI 调用 ----
    p4 = sub.add_parser(
        "agent",
        help="AI Agent 入口：stdout 只输出 JSON",
        description="解析并下载，结果以 JSON 输出到 stdout，日志走 stderr。",
    )
    p4.add_argument("targets", nargs="+", help="一个或多个链接/文案/作品ID")
    p4.add_argument("-o", "--out", default=None, help="输出目录")
    p4.add_argument("--quality", default=None, help="1080p / 720p / 540p，默认最高清")
    p4.add_argument("--music", action="store_true", default=None, help="同时下背景音乐")
    p4.add_argument("--zip", action="store_true", default=None, help="图集打包 zip")
    p4.add_argument("--no-download", action="store_true", default=None, help="只解析不下载")
    p4.add_argument("--headed", action="store_true", default=None, help="显示浏览器窗口")
    p4.add_argument("--profile", default=None, help="登录态 profile 目录")
    p4.add_argument("--cdp", default=None, help="接管已开调试端口的 Chrome")
    p4.add_argument("--rate", type=float, default=None, help="限速 MB/s")
    p4.add_argument("--no-cache", action="store_true", default=None, help="禁用解析缓存")
    p4.add_argument("--json", action="store_true", default=None, help="显式指定 JSON 输出（默认已开启）")
    p4.add_argument("--text", action="store_true", help="输出人类可读文本而非 JSON")
    p4.set_defaults(func=cmd_agent)

    # ---- envcheck ----
    p5 = sub.add_parser("envcheck", aliases=["env"], help="环境自检（依赖/浏览器/登录态/网络）")
    p5.add_argument("--json", action="store_true", help="输出 JSON")
    p5.add_argument("--no-network", action="store_true", help="跳过网络探测")
    p5.add_argument("--profile", default="", help="检查指定登录态目录")
    p5.set_defaults(func=cmd_envcheck)

    # ---- config ----
    p6 = sub.add_parser("config", help="查看生效配置 / 输出配置样例")
    p6.add_argument("--sample", action="store_true", help="打印 config.toml 样例")
    p6.add_argument("--file", default=None, help="指定配置文件路径")
    p6.add_argument("--json", action="store_true", help="只输出 JSON")
    p6.set_defaults(func=cmd_config)

    return ap


def main(argv: List[str] | None = None) -> int:
    ap = build_argparser()
    args = ap.parse_args(argv)
    _setup_log(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
