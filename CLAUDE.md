# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

「谛听」：本地运行的 Windows 桌面小工具，轮询监控指定用户在**东方财富股吧 / 推特(X) / 微博**上的新动态，弹 Windows 系统通知并显示在窗口列表里。纯 Python 标准库 + 可选 `winotify`，无第三方推送服务依赖。

代码、注释、UI 文案、提交信息**全部用中文**。

## 常用命令

```bash
python app.py            # 桌面 GUI（主入口，等价于双击 run_gui.bat）
python monitor.py        # 命令行 + 微信推送版（等价于双击 run.bat）
python test_once.py      # 抓取自检：打印 config.json 里第一个股吧用户的最新 8 条
```

没有构建步骤、没有 lint 配置、**没有测试框架**。验证改动的手段：

```bash
python -c "import py_compile; py_compile.compile('app.py', doraise=True)"   # 语法检查
```

`test_once.py` 只覆盖股吧抓取，且需要真实的 `config.json`（含真实 UID）才能跑。

### 离线验证 GUI 渲染

改动 `app.py` 的展示逻辑时，不要靠真实联网监控来验证（慢且要等新动态）。用 monkeypatch 跳过配置和联网，手工灌假数据后截图：

```python
appmod.MonitorApp.start = lambda self, silent=False: None   # 跳过联网监控
appmod.monitor.load_config = lambda: {"users": [...]}       # 跳过 config.json
# 然后 a._add_item(name, fake_item) 若干次 → a._rebuild() → PIL ImageGrab 截图
```

`config.json` 不在版本库里（含真实 UID/Cookie），所以开发环境通常**没有**这个文件，任何调用 `monitor.load_config()` 的代码路径都会 `sys.exit(1)`。

## 架构

### 两个入口，能力不对等

- **`app.py`** — 桌面 GUI，主要维护对象。三个数据源全支持。
- **`monitor.py`** — 双重身份：① 被 `app.py` import 的抓取/解析核心；② 独立的命令行推送版（`main()`）。注意 **`monitor.py` 的命令行 `main()` 只处理股吧用户**，推特/微博是 GUI 独有的。改抓取逻辑时两边都受影响，改轮询逻辑时通常只动 `app.py`。

### 统一 item 字典是跨文件契约

`monitor.py` 的三个解析函数 `parse_posts()` / `parse_replies()`（股吧）、`parse_tweets()`（推特）、`parse_weibo()`（微博）都归一化成同一种 dict，`app.py` 才能混排渲染：

```
key, kind, icon, time, title, content, bar, ctx_user, ctx_text, link
```

两个**载荷性约定**，改动时容易踩：

1. **`kind` 是中文字符串字面量**，且被当作 dict 键在两个文件里跨文件使用（`app.py` 的 `KIND_BG`、`_resolve_fg`，`monitor.py` 的 `build_message`）。取值：`发帖` / `转发` / `评论`（股吧、微博）、`推文` / `转推`（推特）。新增来源必须复用这些字符串，否则配色和通知文案会静默失配。
2. **`time` 必须是 `YYYY-MM-DD HH:MM:SS` 格式**。`app.py` 直接对字符串做切片（`it["time"][:10]` 取日期分组、`it["time"][11:16]` 取显示时间）并按字符串排序，格式不对会导致分组和排序错乱。推特走 `_norm_time()`、微博走 `_parse_weibo_time()` 做归一化。

`key` 带来源前缀去重：`P`(帖) / `R`(回复) / `T`(推文) / `W`(微博)。

### 去重与「首轮基线」

`state.json` 记录每个来源已见过的 `key`（每来源保留最近 500 条）。skey 命名：股吧用裸 uid，推特 `tw:<handle>`，微博 `wb:<uid>`。

`app.py` 的 `_emit()` 实现关键语义：**每个来源第一次抓取成功**时，把结果当基线塞进列表但**不弹通知**（避免启动刷屏），之后才提示新增。按来源分别 seed（`self._seeded`）是有意为之——历史上曾因全局单一 seed 标志，导致某个来源开机时抓取失败就永远不显示（见 commit 7a3d470）。

注意 `monitor.py` 的 `check_user()` 有一套**独立实现**的相同语义（用 `uid not in state` 判首次），两者共享同一个 `state.json`。

### 线程模型

`app.py` 单后台线程 `_run_loop()` 轮询，通过 `queue.Queue` 把 `("status"|"history"|"new", ...)` 事件传给主线程，主线程 `_poll_queue()` 每 400ms 消费一次并重建列表。**所有 tkinter 调用必须在主线程**。

`start()` 里先 join 旧线程、再给新线程一个全新的 `threading.Event`，是为修历史上的重复推送竞态（commit be583e0）——改动启停逻辑时别退化。

三个来源各有独立轮询节奏（`poll_interval_seconds` / `twitter_poll_interval_seconds` / `weibo_poll_interval_seconds`），推特微博刻意更慢以降低风控封号风险。

### 列表渲染

`_rebuild()` 全量重建 Treeview（扁平结构 + 手工插入日期表头行，不用 tkinter 的树形嵌套）。配色是两个正交维度：

- **背景色** = 动态类型（`KIND_BG`）
- **文字色** = 用户自定义配色（`config.json` 的 `color` 或 `groups`），没设则回退到按类型的文字色

tkinter Treeview 的 tag **只能按整行设置**，做不到单元格级配色——所以"用户色"实际染整行文字，这是控件限制不是 bug。

## .gitignore 采用白名单策略

本目录与其它无关项目共用，`.gitignore` 先 `/*` 忽略一切，再用 `!` 逐个放行本项目文件。

**新增任何文件都必须在 `.gitignore` 里加一行 `!/文件名`，否则 git 完全看不到它。**

始终排除（即使被放行）：`config.json`（含 UID/Cookie/推送 key）、`state.json`、`*.log`。

## 数据源

- 股吧发帖/转发：`i.eastmoney.com/api/guba/userdynamiclistv2`（`type=1`）。**必须用这个而非 `fullarticlelist`**——后者只返回财富号文章，会漏掉股吧短帖。
- 股吧评论：`i.eastmoney.com/api/guba/myreply`
- 推特：外部 CLI `twitter user-posts @handle -n 40 --json`（`pipx install twitter-cli`），靠环境变量 `TWITTER_AUTH_TOKEN` / `TWITTER_CT0` 认证。子进程必须带 `_no_window_kwargs()` 隐藏控制台黑框。
- 微博：`weibo.com/ajax/statuses/mymblog`，Cookie 从 `config.json` 的 `weibo_cookie` 读（至少含 `SUB`）。

全是非官方接口，随时可能变。抓取失败走 `q.put(("status", ...))` 显示到状态栏，**不要让单个来源的异常中断整个轮询循环**。
