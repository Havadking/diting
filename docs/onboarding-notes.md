# 谛听 · 项目认知笔记（上手 / 接手必读）

> 定位：这份文档**不是需求文档，也不是设计文档**，而是「一个新人（或新会话的 AI）在读完代码后，脑子里应该建立起来的那张地图」。
> 与其它文档的分工：
> - [`README.md`](../README.md) —— 用户视角：怎么配、怎么跑、注意事项。
> - [`CLAUDE.md`](../CLAUDE.md) —— 协作规约：常用命令、跨文件契约、改动禁区。
> - [`docs/web-design.md`](web-design.md) —— 网页版的历史设计与分阶段实施记录（已全部完成，仅阶段 7 未做）。
> - [`docs/v2-design.md`](v2-design.md) —— v2.0 功能规划（P0/P1 已落地，P2 待做）。
> - [`docs/gap-and-roadmap.md`](gap-and-roadmap.md) —— 不足盘点与后续开发方向（批判性评估 + 路线图 + 明确不建议做的事）。
> - **本文** —— 系统视角：架构全貌、关键不变量、实现里的「为什么」、以及已知债务。
>
> 阅读建议：先看 §1～§3 建立整体印象，再按需要跳到对应章节。§13 是给「准备动手改代码的人」的排错与禁区清单。

---

## 1. 一句话说清这个项目

**「谛听」是一个只在本机运行的 Windows 盯盘工具**：轮询抓取指定的东方财富股吧（以及代码保留但暂时下线的推特 / 微博）用户的新动态，攒进本地 SQLite，弹 Windows 系统通知，并通过一个本机 HTTP 服务 + 浏览器页面呈现。

四条硬性设计约束（违反其中任何一条都算改坏了）：

| 约束 | 含义 | 体现 |
|---|---|---|
| **全本地** | 无第三方推送服务、无云依赖、无额度限制 | `server.py` 只绑 `127.0.0.1`；浏览器 DevTools 里除了 `127.0.0.1` 不该有任何外部请求（页面内点开原帖/行情的外链除外） |
| **零构建** | 前端无打包步骤 | React 18 / htm / 字体全部 vendor 落地在 `web/vendor/`，`app.js` 是手写的 htm 标签模板 |
| **纯标准库（后端）** | 后端不做重依赖 | `server.py` 只用 `http.server`；唯二可选依赖是 `winotify`（系统通知）和 `sv_ttk`（仅旧 tkinter 版的皮肤） |
| **中文优先** | 代码注释、UI 文案、commit message 全中文 | 连 `kind` 都是中文字符串字面量并被当作 dict 键跨文件使用（见 §4.2） |

技术栈事实：Python 3.9+（实际环境 3.9.13）、React 18.3.1 UMD + htm 3.1.1（前端）、SQLite（`messages.db`）、SSE（`text/event-stream`）、tkinter（遗留旧版）。

---

## 2. 一分钟上手（开发视角）

```bash
python server.py --mock --no-browser     # 离线演示：不读 config.json、不联网、每 10 秒随机推一条
# 另开一个终端验证后端：
curl 127.0.0.1:17777/api/snapshot
curl -N 127.0.0.1:17777/api/events
```

生产跑法：`python server.py`（等价于双击 `run_gui.bat`，用 `pythonw` 无控制台启动，会自动开浏览器、自动开始监控）。

没有构建步骤、没有 lint 配置、**没有测试框架**。验证手段就是 `py_compile` / `pyflakes` / `node -e "new Function(...)"` 做语法检查 + `--mock` 模式下肉眼验证（见 §11）。

⚠ **本机当前 `config.json` 是存在的**（含真实 UID / Cookie / 推送 key），所以 `monitor.load_config()` 不会 `sys.exit(1)`；但把它提交上去是绝对禁止的（`.gitignore` 已排除）。在新 clone 的开发环境里通常**没有**这个文件，此时任何调用 `monitor.load_config()` 的路径都要能优雅降级——`core.py` 的很多方法为此写了 `try/except` 兜底返回空值。

---

## 3. 架构全貌

### 3.1 一个核心 + 三个入口，能力不对等

```
                       monitor.py
        ┌─────────── 抓取/解析核心（parse_* / collect_items / SQLite 读写 / 推送）
        │            也是独立的「命令行 + 微信推送」入口（main()，只处理股吧用户）
        │
   ┌────┴─────┐
   │  core.py │  MonitorCore：后台轮询线程、去重、首轮基线、追加监视、
   │          │  静音/合并/toast、写 messages.db、事件广播、用户设置读写
   │          │  ⚠ 无任何 UI 依赖，禁止 import tkinter
   └────┬─────┘
        │  subscribe() → queue.Queue，事件形状 ("history"|"new"|"status"|"config"|"cleared", payload)
        │
   ┌────┴──────────────┬─────────────────────┐
   │                   │                     │
server.py + web/     app.py             test_once.py
网页版（主力维护）   tkinter 旧版（冻结）  抓取自检脚本
```

| 文件 | 角色 | 维护状态 |
|---|---|---|
| `monitor.py` | 抓取/解析 + SQLite 层 + 命令行微信推送版 | 抓取逻辑会被 `core.py` 复用，改动影响两个入口 |
| `core.py` | 监控核心 | **主战场**，所有新功能（除纯展示）都应该落在这里 |
| `server.py` + `web/` | 网页版 | **主要维护对象**，功能已超出旧版（见 §10） |
| `app.py` | tkinter 旧版 | **冻结**，只允许修 bug，禁止加功能；等阶段 7 删除 |
| `mock_data.py` | `--mock` 的示例数据 + `MockCore` | 验证前端改动的主要手段 |
| `state.json` | 去重用的 key 集合（每来源最近 500 条） | 运行时产物，别手删（删了会把现状当基线） |
| `messages.db` | 消息持久化（SQLite，当前 4605 行，2026-07-02 ~ 2026-09-14） | 运行时产物 |

### 3.2 `monitor.py` 的双重身份（最容易踩的一个坑）

- **作为库**：`core.py` 依赖它做抓取（`collect_items` / `parse_tweets` / `parse_weibo` / `parse_post_appends`）、SQLite（`get_db` / `save_message` / `load_recent_messages` / `load_messages_before` / `search_messages` / `count_messages`）、配置状态（`load_config` / `save_config` / `load_state` / `save_state`）。
- **作为程序**：`python monitor.py` 是独立的「命令行 + 微信推送」版，`main()` **只遍历 `cfg["users"]`（股吧）**，推特/微博是 GUI 独有的。

推论：**改抓取/解析 → 两边都受影响；改轮询调度 → 通常只动 `core.py`。**

### 3.3 三个数据源与抓取端点

| 来源 | 端点 | 关键点 |
|---|---|---|
| 股吧发帖/转发 | `i.eastmoney.com/api/guba/userdynamiclistv2?...&type=1` | **必须用 `type=1`**，另一个 `fullarticlelist` 只返回财富号文章，会漏掉股吧短帖 |
| 股吧评论 | `i.eastmoney.com/api/guba/myreply` | 评论可能是「回复别人的评论」，作者名要按 `source_reply_user_nickname` → `source_post_user_nickname` 兜底 |
| 股吧帖子全文/追加 | `gbapi.eastmoney.com/content/api/Post/ArticleContent?postid=` | 列表接口正文超 200 字会截断，全文 `post_content`(HTML) 和追加 `post_add_list` 都从这取；按帖单独请求，比列表接口敏感，见 §7 |
| 推特 | 外部 CLI `twitter user-posts @handle -n 40 --json` | 需 `pipx install twitter-cli` + 环境变量 `TWITTER_AUTH_TOKEN` / `TWITTER_CT0`；子进程必须带 `_no_window_kwargs()` 隐藏 Windows 黑框 |
| 微博 | `weibo.com/ajax/statuses/mymblog` | Cookie 从 `config.json` 的 `weibo_cookie` 读（至少含 `SUB`），失效时报 `ok != 1` |

全是非官方接口，随时可能变。**单个来源的异常绝不能中断整个轮询循环**——`_run_loop` 里每个来源都单独 `try/except`，失败走 `set_status` 显示到状态栏。

---

## 4. 跨文件数据契约（改动前必读）

### 4.1 统一 item 字典

`parse_posts` / `parse_replies` / `parse_tweets` / `parse_weibo` / `parse_post_appends` 五个解析函数全部归一化成同一种 dict，`core` / `app.py` / `web` 才能混排渲染：

```
key, kind, icon, time, title, content, bar, ctx_user, ctx_text, link
```

两个**载荷性约定**（不是风格问题，是正确性问题）：

1. **`kind` 是中文字符串字面量**，且被当 dict 键跨文件使用：`core.KIND_BG` 查底色、`app.py._resolve_fg` 查文字色、`monitor.build_message` 决定推送文案、`web/app.js` 的 `KINDS` 查 CSS 变量。
   取值：`发帖` / `转发` / `评论`（股吧、微博）、`推文` / `转推`（推特）、`追加`（帖子追加）。
   → **新增来源必须复用这些字符串**，否则配色与通知文案会**静默失配**（不报错，只是不对）。
2. **`time` 必须是 `YYYY-MM-DD HH:MM:SS`**。`app.py` 直接对字符串切片（`[:10]` 取日期分组、`[11:16]` 取显示时间）并按字符串排序；前端同样用 `time.slice(0,10)` 分组、`localeCompare` 排序。
   → 推特走 `_norm_time()`、微博走 `_parse_weibo_time()` 做归一化。**任何新来源都必须归一化时间格式**，否则分组和排序错乱。

`key` 用来源前缀去重，保证跨来源不撞车：`P`(帖) / `R`(回复) / `T`(推文) / `W`(微博) / `A`(帖子追加) / `M`(mock 数据)，追加监视的 skey 是 `ap:<post_id>`。

### 4.2 展示条目（`core.items` / `messages.db` 行）

`_add_item()` 把原始 item 加工成**成品条目**后再入列和写库：

```python
{"key", "name", "kind", "icon", "time", "bar", "content", "link"}
```

加工规则（都发生在 `_add_item`）：

- 正文里**拼好上下文前缀**：`[评论《xx》] 正文` / `[转发自 某人《xx》] 正文` / `[转推自 某人] 正文`；
- 换行统一替换成空格（保证一行渲染）；
- 空正文填 `(无正文)`；`bar` 空则填 `—`。

**DB 里存的就是这种成品**，所以 `load_recent_messages()` 取出来可以直接塞回列表，不需要重新解析。前端用正则 `CTX_RE` 把前缀再拆出来单独渲染成一条 ctx 小条（**DB 结构因此不用改**）——这是一个刻意的「后端拼、前端拆」设计。

### 4.3 配置与状态

- `config.json`：`poll_interval_seconds` / `monitor_posts` / `monitor_replies` / `append_check_interval_seconds` / `push{type,key}` / `groups{名:色}` / 三个来源的用户数组（`users` / `twitter_users` / `weibo_users`）/ `weibo_cookie` / 各来源轮询间隔。用户级可选字段：`color` / `group` / `mute` / `check_appends`。
- `state.json`：`{skey: [key, ...]}`，每来源保留最近 500 条。skey：股吧用**裸 uid**、推特 `tw:<handle>`、微博 `wb:<uid>`、追加 `ap:<post_id>`。
- **`config.json` 不在版本库里**（含 UID/Cookie/推送 key）。`save_config()` 先写 `.tmp` 再 `os.replace`，避免写一半崩了把配置清空。

---

## 5. 核心语义：首轮基线与去重（`_emit`）

这是整个项目最需要理解的 20 行代码：

```python
def _emit(self, state, skey, name, items, db):
    seen = set(state.get(skey, []))
    new_items = [it for it in items if it["key"] not in seen]
    state[skey] = list(dict.fromkeys([it["key"] for it in items] + list(seen)))[:500]
    if skey not in self._seeded:
        self._seeded.add(skey)                       # 该来源第一次抓成功
        entries = [self._add_item(...) for it in sorted(items, key=time)[-10:]]
        self._broadcast("history", entries)          # 入列表，但不弹通知
    elif new_items:
        self._handle_new(name, new_items, db)        # 之后的才算「新动态」
```

要点：

- **首轮基线不弹通知**：避免启动/新增用户时被历史刷屏。只取最近 10 条入列表。
- **按来源分别 seed**（`self._seeded` 是 set 而不是 bool）是**有意为之**：历史上曾用单一全局标志，导致某个来源开机时抓取失败就**永远不显示**（commit `7a3d470`）。每次 `start()` 都会 `self._seeded = set()` 重置。
- 股吧用户的 skey 是用户 uid，所以**每个股吧用户各自成一路基线**。
- `monitor.py` 的 `check_user()` 有一套**独立实现**的相同语义（用 `uid not in state` 判首次），两者共享同一个 `state.json`。改语义时两边都要看。
- `state.json` 在每轮循环末尾 `monitor.save_state(state)` 统一落盘。

---

## 6. 线程模型与 SQLite 归属

### 6.1 线程

| 线程 | 职责 | 约束 |
|---|---|---|
| 后台轮询线程（单条，`_run_loop`） | 抓取所有来源、去重、入列、写库、弹通知、广播、写 `state.json` | 独占**写**连接 |
| HTTP handler 线程（每请求一条，`ThreadingHTTPServer`） | 提供 JSON / 静态文件 | **不碰 SQLite**（例外：`load_older` / `search` / `db_count` 各开临时短连接，用完即关） |
| SSE handler 线程（每连接一条，长驻） | `subscribe()` 一个队列 → 写 `event:` 帧 | 断开时必须 `unsubscribe()` + `close_connection = True` |
| tkinter 主线程 | 消费队列 + 渲染 | **所有 tkinter 调用必须在主线程** |

- `core.items` 会被后台线程追加/裁剪（`MAX_ROWS = 1000`），**任何线程读它都要拿 `core.lock` 拷一份**（`snapshot()` / `app.py._rebuild()` 都这么做）。
- 后台线程处理完每个来源后有 `random.uniform(2, 4)` 秒的随机等待，用来降低被限流风险；轮询周期间隔 `poll_interval_seconds`。
- 推特/微博有**各自独立的慢节奏**（`twitter_poll_interval_seconds` / `weibo_poll_interval_seconds`，刻意更慢以降低风控封号风险），用 `first_cycle or time.time() - self._last_tw >= tw_interval` 判断；追加检查是第三个独立节奏。

### 6.2 `start()` 的启停竞态修复（别退化）

```python
if self.worker and self.worker.is_alive():
    self.stop_event.set(); self.worker.join(timeout=3)
self.stop_event = threading.Event()      # 给新线程一个全新的 Event
my_stop = self.stop_event                # 新线程只认这个局部引用
```

先 join 旧线程、再换一个**全新**的 `threading.Event`，是为了修历史上的**重复推送竞态**（commit `be583e0`）。改动启停逻辑时不要"顺手简化"。

### 6.3 SQLite 的三条纪律

1. **后台线程独占写连接**：`_run_loop` 开头 `get_db()`、`finally` 里 `close()`；`_add_item()` 用它写。
2. **没开 `check_same_thread=False`** → **禁止跨线程传连接对象**。启动时读历史（`_load_history_from_db`）、翻页（`load_older`）、搜索（`search`）、计数（`db_count`）全部各自开临时连接、用完即关。
3. **表结构就是成品展示字段**（见 §4.2），`key` 是 PRIMARY KEY，`INSERT OR IGNORE` 天然幂等；`time` 上有索引。

内存只保留最近 `MAX_ROWS = 1000` 条（超出按时间丢最旧，并同步从 `item_keys` 里移除）；首屏 `/api/snapshot` 只给最近 300 条，更早的靠 `/api/items?before=<time>&before_key=<key>` 按 **(time, key) 二元游标**从库里翻——游标带 key 是为了**同一秒多条时不重不漏**。

---

## 7. 帖子「追加」检查（本项目最复杂的一块）

### 7.1 为什么需要单独做

东方财富允许作者在原帖发布后继续「追加」内容（前端显示成"作者更新以下内容"），但**这部分文字不在列表接口的 `post_content` 里**，只有 `ArticleContent` 接口返回的 `post_add_list` 数组才有。所以要**为每条关心的帖子多发一次请求**。同一个接口也是「全文补全」的数据源：列表接口把超过 200 字的正文截成摘要，`monitor.complete_truncated()` 在新帖入列前用它换成全文（`core._complete_truncated()` 调，首轮基线只补最近 10 条）。以前走帖子详情页抠内嵌 JSON，第一次请求就可能被拦成验证页，已换掉。

### 7.2 实现链路

1. `monitor.parse_news_link(link)` 从统一 item 的 `link` 反解出 `(code, post_id)`——**刻意不给 item 字典加新字段**。
2. `monitor.fetch_article(post_id)` 请求 `ArticleContent`，被拦成验证页 / `rc != 1` / 没有 `post` 都抛异常。
3. `monitor.parse_post_appends(code, post_id)` 取 `post_add_list`、用 `html_to_text()` 转成带段落换行的纯文本，产出 `kind="追加"` 的 item（key 前缀 `A`，`code` 只用来拼链接）。
4. `core._register_append_watch()` 把**发布在 24 小时内**的 `发帖`/`转发` 登记进 `self._append_watch = {post_id: {uid, code, name, expires_at}}`。**只对 `check_appends: true` 的股吧用户生效**（推特/微博没有这个概念）。
5. `core._check_append_watch()` 按 `append_check_interval_seconds`（默认 300 秒）挨个请求 `ArticleContent`；查到追加就 `_emit(state, "ap:" + post_id, ...)` 走**和其它来源完全一样**的去重/首轮基线/通知流程，并把 `expires_at` 顺延 24 小时；查不到就让它自然过期、下一轮被清掉。

### 7.3 两个刻意的设计决定

- **`_append_watch` 故意不落盘**：只有后台线程碰它，不需加锁；重启清空**不是 bug**——下次轮询重新拉到该用户的帖子时，只要还在"发布 24 小时内"就会被重新登记。真正查到的追加内容会像其它动态一样存进 `messages.db`，不会因重启丢失。
- **失败退避**（`_append_fail_streak` + `_append_backoff_interval`）：按帖单独请求的接口比列表接口**更容易触发东财反爬验证**（详情页时代实测踩过：连续调几次后，同 IP 请求任何帖子详情页都会被拦成验证页；`ArticleContent` 温和得多，但退避保留）。`fetch_article()` 识别出验证页特征（`fd_guba_validate` / `em_capt.js`）就**主动抛异常**（避免把验证页误当成"没有追加"），`core` 据此把下次检查间隔按 `base * 2^streak` 翻倍拉长（封顶 2 小时），一旦有一轮成功就清零回正常间隔。**退避只作用于"查追加"这一个节奏，不影响正常轮询。**

---

## 8. 网页版后端（`server.py`）

纯标准库 `ThreadingHTTPServer`，只绑 `127.0.0.1`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/`、`/favicon.ico`、`/assets/<path>` | 静态文件；`os.path.realpath` 校验真实路径必须在 `web/` 之下（防目录穿越） |
| GET | `/api/snapshot?limit=300` | `{items, has_more, status{text,running,last_check}, users[], config{}}` |
| GET | `/api/items?before=&before_key=&limit=200` | 「加载更早」翻页，二元游标 |
| GET | `/api/search?q=&limit=100` | 全库 LIKE 检索 `content` / `bar` / `name` |
| GET | `/api/probe_user?uid=` | 探测 UID 是否真实存在并回显东财昵称（新增用户前用） |
| GET | `/api/events` | SSE：连上先发一条 `status`，之后转发广播事件，`q.get(timeout=25)` 超时发 `: ping` 保活 |
| POST | `/api/control` | `start` / `stop` / `clear` / `test_toast` / `quit` |
| POST | `/api/users` | 旧版配色/静音/查追加保存（按 name 匹配） |
| POST | `/api/users/manage` | 新版统一管理：`add` / `delete` / `update_all`（含轮询参数），前端设置抽屉走这个 |

几个**别去掉**的实现细节：

- **所有 POST 校验 `Origin == "http://" + Host`**，否则 403。这是防止**别的网页用 `fetch` 打本地端口**（比如远程让程序退出）的唯一防线。
- `quit` 先 `_send_json({"ok": True})` 再 `threading.Timer(0.5, os._exit, [0])`——先把响应发出去，浏览器才能显示"已退出"。
- SSE 断开时 `close_connection = True`：否则 handler 会回到 keep-alive 循环，在对端已关的 socket 上读下一个请求，打一屏 traceback。
- `Server.allow_reuse_address = False`：标准库默认开 `SO_REUSEADDR`，在 Windows 上会让"端口已被另一个进程监听"时 bind 照样成功（两个进程抢连接），端口顺延逻辑就形同虚设。
- 端口被占自动 +1（最多试 20 个）。`run_gui.bat` 用 `pythonw` 启动没有控制台，所以顶层 `__main__` 兜底把 traceback 写进 `server_error.log`。
- `mock_data.MockCore` **故意不调父类 `__init__`**（父类会读 `config.json` / `messages.db`），只搭骨架后覆写数据源；订阅/广播/接口和真 core 一模一样。

---

## 9. 网页版前端（`web/`）

### 9.1 形态

React 18 + htm，单文件 `web/app.js`（约 1060 行），所有状态在一个 `useReducer` 里。htm 是标签模板函数（`` html`<div class=${x}>` ``），**不需要 JSX 编译**——这是"零构建"约束的直接产物。

### 9.2 事件 → reducer 映射与状态

| SSE 事件 | reducer | 效果 |
|---|---|---|
| （启动 / 重连）`GET /api/snapshot` | `snapshot` | 首次整体替换；**已 loaded 时改为 merge**，以保留已翻出来的更早历史 |
| `history` / `new` | `append` | 按 `key` 去重、按 `(time, key)` 升序插入 |
| `status` | `status` | 顶栏状态胶囊 |
| `config` | `config` | 用户列表（侧栏 / 设置抽屉） |
| `cleared` | `cleared` | 清空列表 |

本地 UI 状态：`drawer` / `menu` / `theme` / `rail`（侧栏收起）/ `dense`（紧凑模式）/ `sound` / `query` / `searchDb` / `filter{user, kindOff}` / `toggled`（日期折叠）/ `newKeys` / `pendingBelow` / `unread` / `firstUnreadKey`。

`localStorage` 键：`diting.rail` / `diting.theme`（`system|light|dark`）/ `diting.filter` / `diting.collapsed`（**只记非今天**）/ `diting.dense` / `diting.sound`。读写都经 `store` 小工具包了 `try/catch`（隐私窗口下 accessor 会抛）。

### 9.3 几个"看起来可以优化、其实不能动"的点

- **跟随滚动用 `useLayoutEffect` + `followRef`，不用 `requestAnimationFrame`**——后台标签页不跑 rAF，切回来才追，体验像卡住。
- **「加载更早」用 `scroll` 事件而不是 `IntersectionObserver`**——同理，后台标签页里 IO 不触发。
- 新动态到达时**走 `filterRef` / `soundRef` / `usersRef` 拿最新值**，避免把 `filter` 放进 SSE effect 的依赖里导致反复重连。
- `onNew` 里的声音只在「页面不可见或没焦点」且该用户**没被静音**时响（Web Audio API 现场合成正弦波 880→1760Hz，**绝不引入音频文件**，保证离线与零体积）。
- 首屏加载完滚到底（最新在最下面，和 tkinter 版习惯一致），并且 `document.fonts.ready` 后**再补滚一次**（字体晚到会撑高内容）。

### 9.4 视觉规范（两个正交维度）

- **用户色**（`config.json` 的 `color` 或 `groups` 映射，8 个中国传统色见 `PALETTE`）：网页版是卡片左侧 4px 色条 + 用户名；旧 tkinter 版染整行文字（Treeview tag 只能整行设，是控件限制不是 bug）。
- **类型色**（`KIND_BG` / CSS 的 `--k-*` token）：网页版是卡片上的小 pill；旧版是整行底色。
- 正文永远用 `--ink`，**不被用户色劫持**；`追加` 类型的色条是虚线。
- 设计 token 以 [`docs/web-design.md`](web-design.md) §7 和 `web/app.css` 顶部 `:root` 为准；品牌字「谛听」是只含这两个字的 woff2 子集（1.3 KB），时间/计数用等宽字子集。

### 9.5 SSR/展示上的小硬编码

`web/app.js` 里维护了一份 `POPULAR_STOCKS`（约 60 个热门股票名 → 代码），配合一组正则从正文里识别 `$名称(代码)$`、`名称(代码)`、`SH600186`、`600186.SH`、纯 6 位代码等 8 种写法，渲染成跳转东财行情的链接；同时动态从 `item.link` 的 `news,(\d{6})` 和 `bar` 名反推补充映射。`getMarket()` 负责按代码前缀判 `sh`/`sz`/`bj`。

---

## 10. 网页版比 tkinter 版多出来的东西

这些是**只在 `web/` 里存在的功能**，排查"某功能为什么旧版没有"时看这里：

1. 紧凑单行模式（Dense Mode，localStorage `diting.dense`）
2. 全局搜索：本地过滤 + `Enter` 触发全库检索（`/api/search`）+ 关键词 `<mark>` 高亮
3. 在线增删用户与参数管理（`/api/probe_user` + `/api/users/manage`，含添加前探测昵称、二次确认删除、就地改备注名、改轮询/查追加间隔）
4. 盯盘声音提示（后台标签页才响）
5. 「上次看到这里」未读断点线（`visibilitychange` 记录离开时的最新 key）
6. 「加载更早」历史翻页（首屏 300 条）
7. SSE 断线横幅 + 自动重连补漏
8. 「退出程序」按钮（关标签页进程不会退出，必须有出口）
9. 深色模式（默认跟随系统）、侧栏可收缩成窄轨、竖屏三档响应式断点、标签页标题未读数

---

## 11. 怎么验证改动（没有测试框架，只能这样验）

```bash
# 1) 语法检查
python -c "import py_compile; [py_compile.compile(f, doraise=True) for f in ('server.py','core.py','app.py','monitor.py','mock_data.py')]"
python -m pyflakes server.py core.py app.py monitor.py mock_data.py     # 需 pip install pyflakes
node -e "new Function(require('fs').readFileSync('web/app.js','utf8'))"  # 前端 JS 语法

# 2) 后端离线验证
python server.py --mock --no-browser --mock-interval 2
curl 127.0.0.1:17777/api/snapshot
curl -N 127.0.0.1:17777/api/events      # 应看到首条 status、之后 new、期间 : ping
# 目录穿越防护：curl 127.0.0.1:17777/assets/../core.py  应为 404 而不是源码
# Origin 防护：curl -X POST -H 'Origin: http://evil.com' .../api/control -d '{"action":"quit"}'  应 403
```

- **离线可用是硬性要求**：浏览器 DevTools Network 面板里除 `127.0.0.1` 不该有任何外部请求。
- ⚠ **后台标签页里 `scroll` 事件、`IntersectionObserver`、`requestAnimationFrame` 都不跑**。用浏览器面板验证滚动相关行为时，页面常常处于 hidden 状态，**要先截一张图让页面变可见**，否则会误判成"没生效"。
- 验证 `core.py` 的行为（首轮基线 / 去重 / 静音 / 停止）：把 `monitor.collect_items` 换成假函数、`core.random.uniform` 换成常量跳过用户间等待、`subscribe()` 一个队列断言事件序列。
- 验证旧 tkinter 渲染：monkeypatch 跳过配置和联网，手工灌假数据后截图（`monitor.DB_PATH` / `STATE_PATH` 指向临时文件，**别碰真实 `messages.db`**）。详见 `CLAUDE.md`。
- `test_once.py` 只覆盖股吧抓取，且需要真实的 `config.json`（含真实 UID）才能跑。

---

## 12. Mock 模式（`mock_data.py`）

`MockCore` 继承 `MonitorCore` **只换掉数据源**，订阅/广播/接口与真的一模一样。这是本项目"可测试性"的全部依托：

- 10 条固定示例（`_RAW`，日期相对"今天"偏移，保证永远有今天/昨天/前天三个分组）
- 每 `--mock-interval` 秒随机推一条 `new` 事件（文案池 `_NEW_POOL`，含 `$名称(代码)$` 语法用来试行情链接）
- `_older()` 造假历史（往前 30 天，用固定种子 `Random(42)`），让「加载更早」和全库搜索可以离线验证
- 覆写 `probe_user` / `manage_config` / `list_users` / `start`，让设置抽屉在演示模式下也能完整走通

---

## 13. 已知债务与风险清单

| 级别 | 项 | 说明 |
|---|---|---|
| 高 | **推特/微博暂时下线** | `core.py` 顶部 `ENABLE_TWITTER = False` / `ENABLE_WEIBO = False`：不轮询、UI 也不提，但抓取逻辑与代码完整保留，改回 `True` 即可恢复。**改任何"用户列表"相关代码都要留意这两个开关**：`_run_loop` / `describe_config` / `list_users` / `save_users` / `manage_config` / `app.py` `open_colors` 都用同一份"启用来源"列表，就是为了**别让隐藏来源泄漏到 UI、也别让保存逻辑遍历到没渲染出来的用户而误清空配置**。注意 `refresh_config_maps()` 仍会读取全部三个来源（颜色/静音映射），这是有意的。 |
| 高 | **抓取依赖非官方接口** | 东财随时改结构或收紧反爬；`monitor.log` 里已能看到 `urlopen error [Errno 2] No such file or directory`（通常是代理/网络环境导致）和 `read operation timed out` 的真实失败记录。详情页（查追加）尤其容易被拦。 |
| 中 | **`app.py` 是冻结的双份代码** | 阶段 7（稳定运行 ≥1 周后删除）一直未做，`requirements.txt` 里的 `sv_ttk` 也因此还挂着。 |
| 中 | **`/api/users` 已成为遗留接口** | 前端设置抽屉改走 `/api/users/manage` 后，`/api/users` + `core.save_users()` 没有调用方了，但仍保留着（`mock_data.save_users` 也还在）。清理前要确认没有别的调用方。 |
| 中 | **搜索是 `LIKE '%kw%'` 全表扫描** | 当前 4605 行完全够用，数据量再大（几万行）需要 FTS5 或加索引策略。 |
| 中 | **未提交的工作区改动** | `web/app.css` + `web/app.js` 有未提交改动：侧栏头像色块方案（窄轨 32px 圆角方块内显示用户名首字，未设色用户走 `default-bg` 灰底；展开态仍是 10px 小色块）——替代了原先"纯色小方块"的做法。 |
| 中 | **`state.json` 目前同时存着推特/微博/追加的 key** | 下线期间这些 key 不会被更新也不会被清理；重新启用时它们是有效基线（不会重新刷屏），但 `ap:*` 条目意义不大。 |
| 低 | **`messages.db` 无清理/轮转机制** | 只会增长。4605 行 ≈ 1.6 MB，暂无问题。 |
| 低 | **未做的 v2 P2** | 多渠道手机推送（Bark/飞书/企业微信，统一收进 `core._handle_new`）、Stage 7 清理。详见 [`docs/v2-design.md`](v2-design.md)。 |
| 低 | **`web/app.js` 单文件 1000+ 行** | 零构建约束下的取舍，短期不建议拆（拆了要么上构建，要么自己写模块加载）。 |

---

## 14. 改动前最后检查一遍（速查）

1. 我改的东西会不会破坏 §4.1 的 item 契约（中文字面量 `kind`、`YYYY-MM-DD HH:MM:SS` 时间）？
2. 我有没有在 `core.py` 里 import tkinter？有没有跨线程传 SQLite 连接？读 `core.items` 有没有拿锁？
3. 我碰没碰"用户列表"相关逻辑？那就要同时检查 `ENABLE_TWITTER` / `ENABLE_WEIBO` 两个开关。
4. 我改的异常处理会不会让**单个来源的失败中断整个 `_run_loop`**？
5. 前端我是不是用了 rAF / IntersectionObserver 做滚动相关的事？（后台标签页不跑，应该用 `useLayoutEffect` / `scroll` 事件）
6. 我的改动会不会引入任何**外部网络请求**（字体、CDN、音频文件）？
7. **新增任何顶层文件都要在 `.gitignore` 里加一行 `!/文件名`**（白名单策略：先 `/*` 全忽略再逐个放行）。`web/` 与 `docs/` 目录已整体放行，往里加文件不用改。
8. 每完成一个可独立描述的改动，立刻 `git add -A && git commit`，commit message 用 conventional commits 且说明**为什么**；不在 `main`/`master` 上时提交后 `git push`。

---

## 15. 项目时间线速览（从 git 历史读出来的演进脉络）

```
工作区初始化
  └─ 股吧监控 MVP（monitor.py 命令行 + 微信推送）
      └─ tkinter 桌面版 + Windows 通知（app.py）
          └─ 微博监控（后下线）→ 自动开始监控 → 图标/底色/转发来源展示
              └─ 弹窗全文、滚动性能、多屏坐标修复 → 改名「谛听」→ Fluent 主题
                  └─ SQLite 持久化历史 → 帖子「追加」检查 → 反爬识别 + 失败退避
                      └─ core.py 抽离 → server.py + web/ 网页版（阶段 0~6，7 次会话）
                          └─ v2 P0/P1：紧凑模式、全局搜索、在线用户管理、声音提示、未读断点线
                              └─ 股票行情联动、顶栏折行修复、看盘红文案 + 专属图标
                                  └─ （当前）侧栏头像色块方案 [未提交]
                                      └─ 待办：P2 多渠道推送、Stage 7 删除 app.py
```

共 51 个提交，主线一直在 `main`，另有 4 个 `.claude/worktrees/` 下的历史工作树分支和 `feature/v2-p0` / `feature/web-ui` 两个特性分支。
