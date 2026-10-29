---
name: douyin-download
description: Download Douyin (抖音) videos and image posts without watermark and return local file paths. Use when the user sends a Douyin share link (v.douyin.com), a douyin.com URL, an aweme ID, or asks to 解析 / 下载 / 去水印 a Douyin video, or wants to fetch media from a Douyin share text and send the file back.
---

# 抖音下载（douyin-download）

把抖音分享链接变成**本地文件**，并返回结构化结果，供上层 agent 决定怎么发给用户。

不依赖交互、不弹窗、stdout 只输出 JSON —— 可以直接放进自动化流程。

---

## 何时用

命中以下任一情况就用这个 skill：

- 用户发了 `https://v.douyin.com/xxxxx/` 这类短链
- 用户发了抖音分享文案（含「复制此链接，打开抖音搜索」那一大段）
- 用户发了 `https://www.douyin.com/video/7300...` 或带 `modal_id=` 的链接
- 用户直接给了纯数字作品 ID
- 用户说「下载这个视频」「去水印」「解析一下」

不适用：小红书 / B站 / 快手 / YouTube / 微博（架构已预留，但尚未实现，会返回 `UNSUPPORTED`）。

---

## 前置：三秒自检

```bash
python scripts/check_env.py --json
```

关键看 `"ready": true`。为 `false` 时 `issues` 里会写缺什么：

| issue | 处理 |
|---|---|
| 缺少 playwright | `pip install playwright` |
| 没有可用浏览器 | `python -m playwright install chromium` |
| 访问 douyin 失败 | 检查网络 / 代理，或设置 `DOUYIN_DL_BROWSER__HEADLESS=0` 看浏览器是否被拦 |

`login.logged_in = false` **不影响下载**，公开视频不登录也能拿 1080p。
只有要更高画质或受限内容时才需要登录（见下文）。

---

## 主命令

```bash
python scripts/agent_dl.py "<用户发来的链接或整段分享文案>" --json
```

一次多条也可以：

```bash
python scripts/agent_dl.py "URL1" "URL2" "URL3" --json
```

### 输出契约

**stdout 只有一个 JSON 对象**，日志全部走 stderr。成功时：

```json
{
  "ok": true,
  "summary": {
    "total": 1, "succeeded": 1, "failed": 0,
    "bytes": 1405018523, "elapsed_s": 42.1,
    "out_dir": "C:\\...\\downloads"
  },
  "results": [
    {
      "ok": true,
      "input": "https://v.douyin.com/IYjoYbsISeA/",
      "aweme_id": "7679426956026187035",
      "title": "一个人，18个月2万元手搓94分钟刘慈欣AI仿真人科幻电影",
      "author": "野生锅",
      "kind": "video",
      "duration": "45:30",
      "duration_ms": 2730688,
      "quality": "1080p",
      "quality_requested": "",
      "cover": "https://...",
      "backend": "browser",
      "files": [
        {
          "path": "C:\\...\\野生锅_..._1080p.mp4",
          "name": "野生锅_..._1080p.mp4",
          "size": 1405018523,
          "kind": "mp4"
        }
      ],
      "total_bytes": 1405018523,
      "large": true,
      "elapsed_s": 40.5
    }
  ]
}
```

失败时对应条目：

```json
{"ok": false, "input": "...", "code": "PARSE_FAILED", "error": "...", "hint": "..."}
```

> 注意 `ok` 有两层：顶层表示整批是否全成功，`results[i].ok` 表示单条。
> 批量里部分失败时顶层 `ok = false`，但成功的条目照常有 `files`。

### 关键字段怎么用

| 字段 | 用途 |
|---|---|
| `results[i].files[].path` | **绝对路径，直接拿去发送/上传** |
| `results[i].total_bytes` | 发给用户前报大小，或判断能否直传 |
| `results[i].large` | `true` = ≥100MB，见下方「大文件」 |
| `results[i].duration` | 告诉用户视频多长 |
| `summary.out_dir` | 用户想自己找文件时给这个目录 |

---

## 拿到结果之后：怎么发回给用户

这是这个 skill 的核心用法 —— 用户远程发链接，你下载完把文件送回去。

```
1. 跑 agent_dl.py --json
2. 解析 results[0].files[0].path
3. 用宿主自己的发送能力把文件发出去
   （能发文件就发；沙箱里发不了就把绝对路径 + 大小告诉用户）
```

判断用哪种方式：

| 文件大小 | 建议 |
|---|---|
| < 20MB | 直接发文件 |
| 20–100MB | 直接发，但先告诉用户多大 |
| ≥ 100MB（`large: true`） | 先问要不要降清晰度，或只发路径 |

**降清晰度的办法**：加 `--quality 720p` 或 `--quality 540p` 重跑。
同一作品 1080p 与 720p 的体积比大约 1.8:1。

抖音单条最长 30 分钟以内通常几十 MB，超过 30 分钟的长视频能到 1GB+，
这类务必先报大小。

---

## 常用参数

```bash
--json              # 必须：结构化输出（默认已是 JSON 模式）
-o, --out DIR       # 输出目录
--quality 720p      # 1080p / 720p / 540p，不写 = 最高清
--music             # 顺带下背景音乐 mp3
--zip               # 图集打包成 zip
--no-download       # 只解析不下载（先给用户看标题/大小，确认后再下）
--rate 2            # 限速 MB/s
--profile DIR       # 用已登录的 profile
--headed            # 显示浏览器窗口（排错时用）
--text              # 输出人类可读文本，不是 JSON
```

**建议的两段式流程**（适合长视频，避免白下 1GB）：

```bash
# 第一步：只解析，拿到标题和清晰度
python scripts/agent_dl.py "URL" --json --no-download

# 确认后第二步：真正下载
python scripts/agent_dl.py "URL" --json --quality 720p
```

---

## 错误码处理

| code | 含义 | 怎么跟用户说 |
|---|---|---|
| `NO_ID` | 输入里没找到作品 ID | 让用户重发完整分享文案 |
| `PARSE_FAILED` | 作品不可访问 / 风控 / 网络 | 常见是视频已删、私密，或需要登录 |
| `NO_MEDIA` | 解析到了但没有可下载资源 | 少见，通常是平台临时异常 |
| `DOWNLOAD_FAILED` | 解析成功但下载中断 | 重试一次；持续失败看 `--headed` |
| `UNSUPPORTED` | 非抖音链接 | 告诉用户目前只支持抖音 |

`PARSE_FAILED` 的具体原因在 `error` 里，例如：

- `作品不可访问（已删除 / 私密 / 作者关闭详情） [core_dep]` → 让用户换个链接
- `全部后端失败 ... Blocked by ArgusSecurityPlugin` → 会话建不起来，用 `--headed` 看浏览器是否被拦截页挡了

---

## 登录（可选）

公开视频**不需要**登录。以下情况才需要：

- 想要 1080p 以上
- 视频是「仅登录可见」
- 频繁请求后开始被风控

一次性登录，之后复用：

```bash
python tools/login.py          # 弹窗口，扫码，登录态存进 ./chrome-profile
```

然后在命令里带 `--profile ./chrome-profile`，或者写进 `config.toml`：

```toml
[browser]
profile = "chrome-profile"
```

之后就不用再传了。

---

## 性能特征

| 场景 | 耗时 |
|---|---|
| 首次（要起浏览器） | 8–12 秒 |
| 同进程后续 | 1–2 秒/条 |
| 命中详情缓存 | < 1 秒 |
| 1.3GB / 45 分钟视频 | 约 40 秒（本地带宽 12MB/s 时） |

**批量时不要每条起一个进程** —— 浏览器会话会被反复重建。
要么用 `python -m douyin_dl dl --file urls.txt`，要么在一个进程里多次调用 `agent.run()`。

缓存位置 `~/.douyin_dl/detail_cache/`，默认 1 小时。删掉无副作用。

---

## 失败排查顺序

1. `python scripts/check_env.py --json` → 看 `issues`
2. 加 `--headed` 重跑，看浏览器里发生了什么
3. 加 `-v` 看 stderr 的详细日志（agent 模式下不影响 stdout 的 JSON）
4. 确认链接形态：`https://v.douyin.com/` 开头最稳，纯 ID 也行

更多字段说明、平台扩展方法、内部原理见 [reference.md](reference.md)。
