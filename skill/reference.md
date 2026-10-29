# douyin-download 参考手册

给需要深入改动或排错的人看。日常调用看 [SKILL.md](SKILL.md) 就够。

---

## 为什么必须用浏览器

抖音 2024 年后把三个纯 HTTP 入口全封了。实测结果：

| 入口 | 结果 |
|---|---|
| `iesdouyin.com/share/video/{id}/` SSR | 200，但 `_ROUTER_DATA` 只剩空壳，数据被剥离 |
| `/aweme/v1/web/aweme/detail/` | `403 Blocked by ArgusSecurityPlugin Uifid Not Found` |
| `/web/api/v2/aweme/iteminfo/` | 200，返回空 body，接口已废弃 |

硬闯 detail 接口需要同时凑齐：

| 参数 | 说明 |
|---|---|
| `a_bogus` | 184 字符签名，RC4 + 自定义 base64 + 浏览器指纹 |
| `x-secsdk-web-signature` | secsdk 二次签名 |
| `uifid` | 作为 **URL 查询参数**（不是 cookie）传递 |
| `fp` / `verifyFp` / `timestamp` | 设备指纹 |

把浏览器导出的 29 个 cookie 全灌进 requests，**仍然 403**。

所以本项目不移植签名算法，改为：**起真浏览器，在页面上下文里 `fetch` detail 接口**。
页面自己的 SDK 会算出合法签名，我们只截取 JSON。

好处是抖音改算法、加字段都不受影响。代价是首次要起浏览器（约 8 秒），
用会话复用 + 结果缓存摊薄。

---

## 后端降级链

`DouyinParser.BACKENDS`，按顺序尝试：

1. **browser** —— 主力。Playwright 起浏览器，页面内 fetch
2. **share_page** —— 老分享页 SSR，现已失效，保留作历史兜底
3. **iteminfo** —— 已废弃，最后兜底

全失败时抛 `RuntimeError`，消息里带每个后端的具体原因。

`http_cookie` 后端代码保留但默认关闭（`_be_http_cookie`），因为缺 `a_bogus` 永远 403。

---

## 数据结构

```python
Aweme
├── aweme_id: str
├── desc: str                  # 标题
├── author_name / author_id / author_avatar
├── cover: str
├── duration_ms: int
├── medias: list[Media]        # 视频多档 / 图集多张
├── music: Media | None
├── statistics: dict           # digg / comment / share / collect / play
├── backend: str               # 命中的后端
└── raw: dict                  # 原始 JSON，字段全在里面

Media
├── kind: str                  # video / image / audio
├── url: str
├── quality: str               # 1080p / 720p / 540p / default
├── ext: str                   # mp4 / jpg / mp3
├── index: int
└── extra: dict                # 含 bitrate（bps）
```

**清晰度去重逻辑**：抖音 `video.bit_rate` 有 10–16 条记录，同清晰度会有多个 CDN 线路。
`_all_videos()` 按码率降序排序后按 `quality` 去重，只留码率最高那条，
所以 `medias` 里通常只有 3 档（1080p / 720p / 540p），`medias[0]` 就是最高清。

`aweme.video` 返回第一档 video；`aweme.is_image_post` 判断图文。

---

## 去水印原理

不是「去画面水印」，是拿平台提供的无水印地址：

- 老接口给的是 `/aweme/v1/playwm/`（带水印），把 `playwm` 换成 `play`
- 新接口直接给 CDN 直链（`v26-web.douyinvod.com/...`），本身无水印
- 还有一路兜底：`play_addr.uri` 拼 `https://www.iesdouyin.com/aweme/v1/play/?video_id={uri}&ratio=1080p&line=0`，会 302 到真实 CDN

`no_watermark()` 负责前两种，`build_play_url()` 负责第三种。

作者自己压进画面的水印去不掉，那得 ffmpeg delogo 或 AI 修补，是另一件事。

---

## 缓存

| 缓存 | 位置 | TTL | 作用 |
|---|---|---|---|
| 详情 | `~/.douyin_dl/detail_cache/{aweme_id}.json` | 1 小时 | 同作品重复解析秒回 |
| 历史 | `~/.douyin_dl/history.json` | 永久（上限 2000 条） | GUI 历史窗口 |
| 会话 cookie | `~/.douyin_dl/cookies.json` | 30 分钟 | 供调试用，解析不依赖它 |

全部可安全删除。

`use_cache=False` 或 `--no-cache` 关闭详情缓存。

---

## 配置

优先级：**命令行参数 > 环境变量 > `config.toml` > 内置默认值**

配置文件查找顺序：
1. `--config` 指定
2. `./config.toml`
3. `~/.douyin_dl/config.toml`

查看当前生效配置：

```bash
python -m douyin_dl config          # 打印合并结果
python -m douyin_dl config --sample # 打印样例
```

环境变量用双下划线表示层级：

```bash
DOUYIN_DL_DOWNLOAD__OUT_DIR=D:\videos
DOUYIN_DL_BROWSER__HEADLESS=0
DOUYIN_DL_DOWNLOAD__QUALITY=720p
```

完整样例见项目根 `config.example.toml`。

---

## 内部 API（Python 直调）

```python
from douyin_dl import DouyinParser, Downloader
from douyin_dl.agent import run

# 单条
with DouyinParser() as p:
    aweme = p.parse("https://v.douyin.com/xxxxx/")
    print(aweme.video.url, aweme.video.quality)

# 批量：浏览器只起一次
with DouyinParser(on_step=lambda lv, msg: print(msg)) as p:
    for url in urls:
        aweme = p.parse(url)          # 之后每条 1–2 秒
        Downloader("downloads").save(aweme)

# 一步到位拿结构化结果（和 CLI 的 agent 子命令同一套）
result = run(["URL1", "URL2"], out_dir="downloads", quality="720p")
print(result["summary"])
```

`DouyinParser` 关键参数：

| 参数 | 说明 |
|---|---|
| `headless` | 无头模式，默认 True |
| `user_data_dir` | 持久化登录态目录 |
| `cdp_endpoint` | 接管已开调试端口的 Chrome |
| `use_cache` / `cache_ttl` | 详情缓存 |
| `on_step(level, msg)` | 每步进度回调，`level` ∈ info/ok/warn/err |
| `prefer` | 指定优先后端 |

`Downloader` 关键参数：

| 参数 | 说明 |
|---|---|
| `on_progress(name, recv, total)` | 下载进度回调 |
| `rate_limit` | 字节/秒，0 = 不限 |
| `save(aweme, media_index, control)` | `control` 是 `DownloadControl`，支持暂停/取消 |
| `resume=True` | 断点续传（默认开） |

---

## 暂停 / 取消的实现

`DownloadControl` 包了两个 `threading.Event`：

```python
ctl = DownloadControl()
dl.save(aweme, control=ctl)   # 后台线程跑
ctl.pause()                   # 暂停：chunk 循环里阻塞
ctl.resume()                  # 继续
ctl.cancel()                  # 取消：抛 DownloadCancelled
```

`_fetch_to` 每读一个 64KB chunk 调一次 `control.checkpoint()`：
暂停时在那里 `wait()`，取消时抛异常。已写入的 `.part` 保留，
下次带 `Range` 头续传。

> 踩过的坑：tqdm 在 `disable=True` 时 `update()` 直接 return，`bar.n` 永远是 0。
> 早期用 `bar.n` 当进度源，导致进度恒定 0（GUI 看起来像假进度条）。
> 现在自己维护 `written` 计数器。

---

## 扩展新平台

`douyin_dl/platforms.py` 是唯一注册表，GUI 下拉框、CLI、平台识别都从这里读。

三步：

**1. 写 Parser**

```python
# douyin_dl/xhs.py
from .models import Aweme, Media

class XhsParser:
    def parse(self, text: str) -> Aweme:
        return Aweme(aweme_id=..., desc=..., author_name=...,
                     medias=[Media(kind="video", url=..., quality="1080p", ext="mp4")])
    def close(self): pass
```

只要返回 `Aweme`，`Downloader`（断点续传 / 并发 / 图集打包 / 暂停取消）全部现成可用。

**2. 登记**

```python
# platforms.py
def _xhs_factory(**kw):
    from .xhs import XhsParser
    return XhsParser(**kw)

"xiaohongshu": Platform(
    key="xiaohongshu", name="小红书", accent="#FF2442",
    ready=True, link_patterns=["xhslink.com", "xiaohongshu.com"],
    factory=_xhs_factory,
),
```

**3. 完成** —— GUI、CLI、`agent` 子命令同时生效。

各平台难度：

| 平台 | 难度 | 要点 |
|---|---|---|
| B站 | ★☆☆ | `api.bilibili.com/x/web-interface/view` + `playurl`，带 Referer 即可 |
| 微博 | ★☆☆ | `m.weibo.cn/api/container/getIndex` |
| 快手 | ★★☆ | 页面 `__APOLLO_STATE__` |
| 小红书 | ★★★ | `x-s` / `x-t` 签名，要补浏览器环境 |
| YouTube | ★★★☆ | 包装 yt-dlp 最省事 |
| 微信视频号 | ★★★★★ | 只能抓包，建议最后做 |

纯 requests 能搞定的平台**不需要浏览器**，`factory` 指到一个轻量 Parser 即可。

---

## 已知限制

- **只支持抖音**。其它平台返回 `UNSUPPORTED`（架构已留位）
- **不下载直播**。抖音直播流是独立协议，需要另写
- **不下载私密/仅作者可见**。这类即使登录也受作者设置限制
- **画面内水印去不掉**。见上文「去水印原理」
- **浏览器必须能跑**。headless 被拦时用 `--headed`
- **改签名算法不受影响**，但改页面结构 / 换接口路径需要更新 `session.py` 的 `DETAIL_JS`

---

## 目录结构

```
douyin-dl/
├── douyin_dl/
│   ├── platforms.py      平台注册表
│   ├── config.py         配置加载
│   ├── agent.py          Agent 入口（结构化 JSON）
│   ├── envcheck.py       环境自检
│   ├── session.py        浏览器会话（免签名核心）
│   ├── parser.py         抖音解析 + 后端降级 + 缓存
│   ├── resolver.py       链接处理
│   ├── models.py         数据结构
│   ├── downloader.py     下载器（续传/暂停/取消/限速）
│   ├── history.py        下载历史
│   └── cli.py            命令行
├── skill/
│   ├── SKILL.md          ← agent 看这个
│   ├── reference.md      ← 你正在看的
│   └── scripts/
│       ├── agent_dl.py   agent 调用入口
│       └── check_env.py  环境自检入口
├── gui.py                Tkinter 图形界面
├── tools/login.py        一次性扫码登录
├── examples/batch.py     批量示例
└── tests/                自测与探测脚本
```
