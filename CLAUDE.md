# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

「谛听」：本地运行的 Windows 小工具，轮询监控指定用户在**东方财富股吧 / 推特(X) / 微博**上的新动态，弹 Windows 系统通知并显示在列表里。界面是本机 HTTP 服务 + 浏览器页面（`server.py` + `web/`），旧的 tkinter 窗口版（`app.py`）暂时保留。纯 Python 标准库 + 可选 `winotify`，前端 React + htm 全部本地 vendor、零构建，无第三方推送服务依赖。

代码、注释、UI 文案、提交信息**全部用中文**。

## 常用命令

```bash
python server.py                 # 网页版（主入口，等价于双击 run_gui.bat）；自动开浏览器
python server.py --mock          # 离线演示：不读 config.json、不联网，示例数据 + 每 10 秒随机推一条
python server.py --no-browser    # 不自动开浏览器；--port N 指定端口（默认 17777，被占自动 +1）
python app.py                    # 旧版 tkinter 窗口（等价于双击 run_gui_tk.bat）
python monitor.py                # 命令行 + 微信推送版（等价于双击 run.bat）
python test_once.py              # 抓取自检：打印 config.json 里第一个股吧用户的最新 8 条
```

没有构建步骤、没有 lint 配置、**没有测试框架**。验证改动的手段：

```bash
python -c "import py_compile; [py_compile.compile(f, doraise=True) for f in ('server.py','core.py','app.py','monitor.py','mock_data.py')]"   # 语法检查
python -m pyflakes server.py core.py app.py monitor.py mock_data.py    # 未用导入/未定义名字（pip install pyflakes）
node -e "new Function(require('fs').readFileSync('web/app.js','utf8'))"  # 前端 JS 语法检查（有 node 的话）
```

`test_once.py` 只覆盖股吧抓取，且需要真实的 `config.json`（含真实 UID）才能跑。

### 离线验证网页版

`python server.py --mock --no-browser` 起来后，后端用 `curl 127.0.0.1:17777/api/snapshot` / `curl -N 127.0.0.1:17777/api/events` 看；前端直接开浏览器。
`--mock-interval 2` 可以把随机推送加快到 2 秒一条。mock 数据在 `mock_data.py`，`MockCore` 继承 `MonitorCore` 只换掉数据源，订阅/广播/接口和真的一模一样。

浏览器 DevTools Network 面板里除了 `127.0.0.1` 不该有任何外部请求——离线可用是硬性要求。

注意：**后台标签页里 scroll 事件、IntersectionObserver、requestAnimationFrame 都不跑**，用 Claude 的浏览器面板（页面常常处于 hidden 状态）验证滚动相关行为时要先截一张图让页面变可见，否则会误判成没生效。

### 离线验证旧版 tkinter 渲染

改动 `app.py` 的展示逻辑时，不要靠真实联网监控来验证（慢且要等新动态）。用 monkeypatch 跳过配置和联网，手工灌假数据后截图：

```python
monitor.DB_PATH = <临时文件>; monitor.STATE_PATH = <临时文件>   # 别碰真实 messages.db
monitor.load_config = lambda: {"users": [...]}              # 跳过 config.json（要在 import app 之前）
appmod.MonitorApp.start = lambda self, silent=False: None   # 跳过联网监控
# 然后 a.core._add_item(name, fake_item, None) 若干次 → a._rebuild() → PIL ImageGrab 截图
# 要测快速追加路径：e = a.core._add_item(...); a.core._broadcast("new", [e]); root.update()
```

验证 `core.py` 的行为（首轮基线 / 去重 / 静音 / 停止）同理：把 `monitor.collect_items` 换成假函数、
`core.random.uniform` 换成常量跳过用户间等待，`subscribe()` 一个队列断言收到的事件序列。

`config.json` 不在版本库里（含真实 UID/Cookie），所以开发环境通常**没有**这个文件，任何调用 `monitor.load_config()` 的代码路径都会 `sys.exit(1)`。

## 架构

### 一个核心、三个入口，能力不对等

- **`core.py`** — `MonitorCore`：后台轮询线程、首轮基线/去重、追加监视、静音/合并/toast、写 `messages.db`、事件广播、读写用户设置。**无 UI 依赖，不许 import tkinter**。三个数据源全支持。
- **`server.py` + `web/`** — 网页版，主要维护对象。`server.py` 是纯标准库 `ThreadingHTTPServer`，只绑 `127.0.0.1`，接口见 `docs/web-design.md` §5（该表**没有**收录 2026-09 v2 新增的 `/api/search`、`/api/probe_user`、`/api/users/manage`，见下方「v2 功能」）；`web/app.js` 是 React 18 + htm 写的单文件前端（htm 是标签模板函数，写法 `` html`<div class=${x}>` ``，不需要 JSX 编译）。所有 `POST` 校验 `Origin` 必须等于自己，别去掉——这是防止别的网页 fetch 本地端口让程序退出的唯一防线。
- **`app.py`** — 旧版 tkinter 窗口，只是 `MonitorCore` 的另一层壳：`subscribe()` 一个队列，消费事件画 Treeview。稳定后会删，**不要再往里加功能**。
- **`monitor.py`** — 双重身份：① 被 `core.py` import 的抓取/解析核心；② 独立的命令行推送版（`main()`）。注意 **`monitor.py` 的命令行 `main()` 只处理股吧用户**，推特/微博是 GUI 独有的。改抓取逻辑时两边都受影响，改轮询逻辑时通常只动 `core.py`。

`core.py` 顶部的 `ENABLE_TWITTER` / `ENABLE_WEIBO` 目前是 `False`——推特/微博功能暂时下线（不轮询、UI 也不提），但代码和 `monitor.py` 里的抓取逻辑都完整保留，改成 `True` 即可恢复。改任何"用户列表"相关的地方（`core.py` 的 `_run_loop`/`describe_config`/`list_users`/`save_users`、`app.py` `open_colors` 的 `rows`/`editable_users`）时留意这两个开关，别让隐藏的来源重新泄漏到 UI，也别让保存逻辑遍历到没渲染出来的用户而误清空他们的配置。`list_users()` 和 `save_users()` 用同一份"启用来源"列表就是为了这个。

### 统一 item 字典是跨文件契约

`monitor.py` 的三个解析函数 `parse_posts()` / `parse_replies()`（股吧）、`parse_tweets()`（推特）、`parse_weibo()`（微博）都归一化成同一种 dict，`app.py` 才能混排渲染：

```
key, kind, icon, time, title, content, bar, ctx_user, ctx_text, link
```

两个**载荷性约定**，改动时容易踩：

1. **`kind` 是中文字符串字面量**，且被当作 dict 键在两个文件里跨文件使用（`core.py` 的 `KIND_BG`、`app.py` 的 `_resolve_fg`，`monitor.py` 的 `build_message`）。取值：`发帖` / `转发` / `评论`（股吧、微博）、`推文` / `转推`（推特）、`追加`（`monitor.parse_post_appends()`，见下方「帖子追加检查」）。新增来源必须复用这些字符串，否则配色和通知文案会静默失配。
2. **`time` 必须是 `YYYY-MM-DD HH:MM:SS` 格式**。`app.py` 直接对字符串做切片（`it["time"][:10]` 取日期分组、`it["time"][11:16]` 取显示时间）并按字符串排序，格式不对会导致分组和排序错乱。推特走 `_norm_time()`、微博走 `_parse_weibo_time()` 做归一化。

`key` 带来源前缀去重：`P`(帖) / `R`(回复) / `T`(推文) / `W`(微博) / `A`(帖子追加)。

### 去重与「首轮基线」

`state.json` 记录每个来源已见过的 `key`（每来源保留最近 500 条）。skey 命名：股吧用裸 uid，推特 `tw:<handle>`，微博 `wb:<uid>`。

`core.py` 的 `_emit()` 实现关键语义：**每个来源第一次抓取成功**时，把结果当基线塞进列表但**不弹通知**（避免启动刷屏），之后才提示新增。按来源分别 seed（`self._seeded`）是有意为之——历史上曾因全局单一 seed 标志，导致某个来源开机时抓取失败就永远不显示（见 commit 7a3d470）。

注意 `monitor.py` 的 `check_user()` 有一套**独立实现**的相同语义（用 `uid not in state` 判首次），两者共享同一个 `state.json`。

### 消息持久化（SQLite）

`state.json` 只存去重用的 `key` 列表，不存消息内容——真正的消息内容存在 `monitor.py` 的 `messages.db`（`get_db()`/`save_message()`/`load_recent_messages()`），表结构就是 `core.items` 那种已经处理好的展示字段（`content` 已经拼好「评论于/转发自」前缀），不是 `monitor.py` 解析函数的原始字段，所以直接读出来就能塞回列表，不用重新处理。

连接归属：**后台抓取线程独占写连接**（`_run_loop` 开头 `get_db()`、`finally` 里关，`_add_item()` 用它写）；启动时 `MonitorCore._load_history_from_db()` 用一个独立短连接读完即关；HTTP 线程的「加载更早」翻页（`core.load_older()`）和总数查询（`core.db_count()`）也各自开临时短连接、用完即关。没开 `check_same_thread=False`，所以别跨线程传连接对象。壳层不直接碰 SQLite，读内存里的 `core.items` 要拿 `core.lock`。

内存里的 `core.items` 上限 `MAX_ROWS`（1000），`snapshot(limit=300)` 首屏只给最近 300 条，更早的靠 `/api/items?before=<time>&before_key=<key>` 按 `(time, key)` 游标从库里翻——游标带 key 是为了同一秒多条时不重不漏。`monitor.py` 的独立命令行版目前不写这个库。

### 帖子追加检查

东方财富股吧允许作者在原帖发布后继续「追加」内容（前端显示成"作者更新以下内容"），但这部分文字**不在** `userdynamiclistv2` 列表接口的 `post_content` 字段里，只存在于帖子详情页 `guba.eastmoney.com/news,{code},{post_id}.html` 内嵌的 `var post_article={...};` JSON 里的 `post_add_list` 数组。`monitor.parse_post_appends(code, post_id)` 专门请求这个详情页，用 `_extract_js_object()` 手动配平大括号把这段 JSON 抠出来（正则的非贪婪匹配处理不了嵌套 JSON，见函数内注释）。`monitor.parse_news_link(link)` 从统一 item 的 `link` 字段反解出 `(code, post_id)`，避免给统一 item 字典再加新字段。

只对用户在「用户设置」里勾了 `check_appends: true` 的股吧用户生效（推特/微博没有这个概念）。监视逻辑全在 `core.py` 后台线程 `_run_loop` 里，**故意不落盘**：

- `self._append_watch`：`{post_id: {"uid","code","name","expires_at"}}`，只有后台线程会碰它，不用加锁。`_register_append_watch()` 把发布在 24 小时内的帖子登记进去；`_check_append_watch()` 定期（`append_check_interval_seconds`，默认 300 秒）挨个请求详情页，查到追加就用 `_emit()` 走跟其它来源一样的去重/首轮基线/通知流程（skey 是 `"ap:" + post_id`），并把 `expires_at` 顺延 24 小时；查不到就让它自然过期、下一轮被清掉。
- 重启会清空这张表——不是 bug，是有意简化：下次轮询重新拉到该用户的帖子时，只要还在"发布 24 小时内"就会被重新登记，不需要额外持久化这份运行时调度状态。真正的追加内容一旦查到，会像其它动态一样存进 `messages.db`，不会因为重启丢失。

**详情页接口比列表接口(`userdynamiclistv2`)更容易触发东财反爬验证**（实测踩过：连续调过几次详情页后，同一个 IP 请求任何帖子详情页都会被拦成验证页而不是真实内容）。`parse_post_appends()` 识别出验证页特征（`fd_guba_validate`/`em_capt.js`）就主动抛异常，不会把验证页误当成"没有追加"。`core.py` 的 `_check_append_watch()` 配了失败退避：`self._append_fail_streak` 记连续失败次数，`_append_backoff_interval()` 让下次检查间隔按 `base * 2^streak` 翻倍拉长（封顶 2 小时），一旦有一轮成功就清零回到 `append_check_interval_seconds` 配的正常间隔。退避只作用于"查追加"这一个独立节奏，不影响股吧/推特/微博的正常轮询。

### 线程模型

`core.py` 单后台线程 `_run_loop()` 轮询，处理完的条目（已入 `core.items`、已写库、已弹通知）通过 `_broadcast()` 推给所有订阅者队列（`subscribe()` 拿，满 200 条丢最旧的，别让挂死的浏览器 tab 拖住后台线程）。事件形状：`("history"|"new", [entry...])`、`("status", {text, running, last_check})`、`("config", {users})`、`("cleared", {})`。

- 网页版：每个 SSE 连接（`/api/events`）在自己的 handler 线程里 `subscribe()` 一个队列，`q.get(timeout=25)` 超时就发 `: ping` 保活；断开时 `unsubscribe()` 并把 `close_connection` 置 `True`，否则 handler 会回到 keep-alive 循环在已关的 socket 上读下一个请求、打一屏 traceback。前端 `EventSource` 自己重连，`onopen` 时若不是首次连接就重拉 `/api/snapshot` 合并补漏。
- tkinter 版：主线程 `_poll_queue()` 每 400ms 消费一次并渲染，**所有 tkinter 调用必须在主线程**。

`core.items` 在后台线程被追加/裁剪（`MAX_ROWS`），任何线程读它都要拿 `core.lock` 拷一份。

### 前端状态

全在 `web/app.js` 一个 `useReducer` 里，事件→reducer 的对应关系见 `docs/web-design.md` §6。几个容易踩的点：

- 条目按 `(time, key)` 升序，**今天在最下面、最新在最底**，和旧窗口版习惯一致；日期组默认只展开今天。
- `content` 里的 `[评论《xx》] 正文` / `[转发自 某人《xx》] 正文` 前缀由 `splitCtx()` 正则拆出来单独渲染，DB 结构没改。
- 跟随滚动用 `useLayoutEffect` + `followRef`，不用 `requestAnimationFrame`（后台标签页不跑）。
- `localStorage` 键：`diting.rail`（侧栏收起）、`diting.theme`（`system|light|dark`）、`diting.filter`、`diting.collapsed`（只记非今天的日期）、`diting.dense`（紧凑单行模式）、`diting.sound`（新动态提示音开关）。读写都经 `store` 小工具包了 try/catch。

### v2 功能（2026-09，P0/P1 已实现）

规划文档是 `docs/v2-design.md`，P2（手机推送、删旧版 tkinter）还没做。已落地的几项跨文件契约：

- **全库搜索**：`GET /api/search?q=&limit=` → `core.search()` → `monitor.search_messages()`，对 `messages.db` 的 `content/bar/name` 做 `LIKE` 匹配，HTTP 线程开独立短连接。前端顶栏搜索框先过滤内存里的条目，回车再打库；快捷键 `/` 聚焦搜索框。
- **在线用户与参数管理**：`POST /api/users/manage` → `core.manage_config()`，`action` 取 `add` / `delete` / `update_all`。`update_all` 除用户设置外还写 `poll_interval_seconds`（钳到 10~3600）和 `append_check_interval_seconds`（钳到 30~7200），保存后广播 `("config", {users, config})`；**前端 reducer 的 `config` 分支只取 `users`**，轮询参数靠设置抽屉保存后重拉 `/api/snapshot` 刷新（`snapshot()` 里带 `config: poll_config()`）。`add` 前可先 `GET /api/probe_user?uid=` 探测昵称（`monitor.probe_guba_user()`，只支持股吧）。旧的 `POST /api/users`（`save_users()`）仍在。
- **紧凑单行模式 / 提示音 / 未读断点线** 全是前端本地状态，后端无感知。提示音用 Web Audio 合成（`playDing()`），不带音频文件；未读断点线靠 `visibilitychange`：切走时记最新条目 `key`，切回时若有新条目就在其后画一条「上次看到这里」分界线。这些都依赖页面可见性，用 Claude 浏览器面板验证时同样要先截图让页面变可见。
- **股票行情联动**：`web/app.js` 里硬编码 `POPULAR_STOCKS`（名称→代码）+ 正文里的 6 位代码识别，`renderRichContent()` 把命中文本渲染成指向 `quote.eastmoney.com` 的链接；卡片上的吧名也链到行情页。**只生成链接、不发请求**，不违反离线可用要求。

`start()` 里先 join 旧线程、再给新线程一个全新的 `threading.Event`，是为修历史上的重复推送竞态（commit be583e0）——改动启停逻辑时别退化。

三个来源各有独立轮询节奏（`poll_interval_seconds` / `twitter_poll_interval_seconds` / `weibo_poll_interval_seconds`），推特微博刻意更慢以降低风控封号风险。

### 列表配色

两个正交维度，网页版和旧窗口版一致：

- **类型**（`KIND_BG` / CSS 的 `--k-*` token）：网页版是卡片上的小 pill，旧窗口版是整行底色
- **用户色**（`config.json` 的 `color` 或 `groups`，8 个中国传统色见 `PALETTE`）：网页版是卡片左侧 4px 色条 + 用户名，旧窗口版染整行文字（tkinter Treeview 的 tag 只能按整行设，控件限制不是 bug）

设计 token（颜色、字体、断点）以 `docs/web-design.md` §7 和 `web/app.css` 顶部的 `:root` 为准。

## .gitignore 采用白名单策略

本目录与其它无关项目共用，`.gitignore` 先 `/*` 忽略一切，再用 `!` 逐个放行本项目文件。

**新增任何顶层文件都必须在 `.gitignore` 里加一行 `!/文件名`，否则 git 完全看不到它。** `web/` 目录整体已放行（`!/web/`），往里加文件不用再改。

始终排除（即使被放行）：`config.json`（含 UID/Cookie/推送 key）、`state.json`、`*.log`。

## Git 工作流

每完成一个可独立描述的改动，立刻 `git add -A && git commit`。

commit message 用 conventional commits 格式，说明"为什么"而非"改了什么"。

提交后 `git push`，除非当前在 main/master 分支。

## 数据源

- 股吧发帖/转发：`i.eastmoney.com/api/guba/userdynamiclistv2`（`type=1`）。**必须用这个而非 `fullarticlelist`**——后者只返回财富号文章，会漏掉股吧短帖。
- 股吧评论：`i.eastmoney.com/api/guba/myreply`
- 股吧帖子追加：`guba.eastmoney.com/news,{code},{post_id}.html`（详情页，抠内嵌 `post_article` JSON，见上方「帖子追加检查」）
- 推特：外部 CLI `twitter user-posts @handle -n 40 --json`（`pipx install twitter-cli`），靠环境变量 `TWITTER_AUTH_TOKEN` / `TWITTER_CT0` 认证。子进程必须带 `_no_window_kwargs()` 隐藏控制台黑框。
- 微博：`weibo.com/ajax/statuses/mymblog`，Cookie 从 `config.json` 的 `weibo_cookie` 读（至少含 `SUB`）。

全是非官方接口，随时可能变。抓取失败走 `q.put(("status", ...))` 显示到状态栏，**不要让单个来源的异常中断整个轮询循环**。
