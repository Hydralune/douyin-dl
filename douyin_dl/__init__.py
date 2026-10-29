"""douyin_dl —— 抖音无水印解析下载库

最简用法::

    from douyin_dl import DouyinParser, Downloader

    p = DouyinParser()
    aweme = p.parse("https://v.douyin.com/xxxxx/")
    print(aweme.video.url)

    Downloader("downloads").save(aweme)
"""
from .client import HttpClient, UA_DESKTOP, UA_MOBILE
from .downloader import Downloader, merge_av
from .models import Aweme, Media, safe_filename
from .parser import DouyinParser, dig, no_watermark, pick
from .resolver import expand_short_url, extract_aweme_id, normalize
from .session import BrowserSession, SessionError

__version__ = "1.0.0"

__all__ = [
    "DouyinParser",
    "Downloader",
    "HttpClient",
    "BrowserSession",
    "SessionError",
    "Aweme",
    "Media",
    "safe_filename",
    "normalize",
    "extract_aweme_id",
    "expand_short_url",
    "no_watermark",
    "merge_av",
    "dig",
    "pick",
    "UA_MOBILE",
    "UA_DESKTOP",
    "__version__",
]
