# 谛听 · 网页版设计文档

> **状态：阶段 0～6 已完成并于 2026-09-14 合并进 `main`**，网页版已是默认入口（`run_gui.bat` → `server.py`）。
> 剩阶段 7（删旧版 tkinter 代码），见 §9 进度表。
>
> 目标是把 tkinter 桌面窗口换成「本地 HTTP 服务 + 浏览器页面」，抓取/去重/通知等核心逻辑不变。视觉稿见 [web-mock.html](web-mock.html)（用浏览器直接打开即可，含示例数据，需联网加载 React/字体）。

## 1. 为什么改

- tkinter Treeview 只能整行设 tag，做不到"用户色染色条、类型色做标签"这种单元格级配色，之前所有美化都在跟控件限制打架。
- 右键弹窗、多屏坐标、滚动卡顿这些问题在网页里天然不存在。
- 日常使用场景是**竖着的副屏**，网页布局按宽度自适应比 tkinter 容易得多。

## 2. 总体架构

```
python server.py
  ├─ core.MonitorCore          后台抓取线程 + 数据模型（从 app.py 抽出，无任何 UI 依赖）
  │     ├─ _run_loop / _emit / 追加监视 / toast     原样搬
  │     ├─ self.items (list) + threading.Lock        主数据
  │     └─ subscribers: [queue.Queue]                事件广播给 SSE 连接
  ├─ http.server.ThreadingHTTPServer  绑 127.0.0.1:17777（被占则 +1 顺延；--host 0.0.0.0 开放局域网）
  │     ├─ GET  /  /assets/*        静态文件（web/ 目录）
  │     ├─ GET  /api/*              JSON
  │     ├─ GET  /api/events         SSE 长连接
  │     └─ POST /api/*              控制 / 保存设置
  └─ webbrowser.open("http://127.0.0.1:17777")
```

- 纯标准库，不引入 Flask/FastAPI。SSE 用标准库就能做，前端 `new EventSource()` 一行，断线自动重连。
- `monitor.py` 完全不动，命令行版 `python monitor.py` 照常独立运行。
- 旧的 `app.py` 先保留（`run_gui.bat` 改指向 `server.py`，另加 `run_gui_tk.bat` 指向旧版），网页版稳定后再删。

## 3. 文件布局

```
core.py            MonitorCore（新）
server.py          HTTP 服务 + 主入口（新）
mock_data.py       --mock 用的示例数据 + MockCore（新）
web/
  index.html       壳子：加载 vendor 脚本 + app.js
  app.css          全部样式（设计 token 见 §7）
  app.js           React 组件（htm 模板，见下）
  vendor/
    react.production.min.js        18.3.1 UMD
    react-dom.production.min.js    18.3.1 UMD
    htm.min.js                     3.1.1
    diting-serif.woff2             Noto Serif SC 700 只含「谛听」两字的子集（品牌字，1.3 KB）
    jetbrains-mono.woff2           JetBrains Mono 只含数字和 :·/- 的子集（时间/计数用，5 KB）
                                   两个子集都是 Google Fonts 的 css2?text= 接口直接给的，不用 fonttools
app.py             旧 tkinter 版，暂留
monitor.py         不动
docs/web-design.md 本文
docs/web-mock.html 视觉稿
```

**新增文件都要在 `.gitignore` 里逐个 `!` 放行**（白名单策略）。`web/` 目录整体放行写 `!/web/` 即可（`/*` 只匹配顶层）。

### 为什么是 htm 而不是 JSX

项目原则是"不加构建步骤、离线能跑"。JSX 要么装 Node+esbuild 编译，要么浏览器端跑 3 MB 的 Babel（每次打开页面都要编译一遍）。
[htm](https://github.com/developit/htm) 是 700 字节的标签模板函数，写法 `html\`<div class=${cls}>${x}</div>\``，
和 JSX 几乎一样，React 组件模型/Hooks 全都照用，且**不需要任何构建**。视觉稿 `web-mock.html` 用的是 Babel+JSX 方便快速出图，正式代码改写成 htm。

## 4. `core.py` — MonitorCore

从 `app.py` 的 `MonitorApp` 剥离，去掉一切 tkinter 和 queue→主线程的东西。

```python
class MonitorCore:
    items: list[dict]            # 展示字段（同 messages.db 表结构）
    item_keys: set
    lock: threading.Lock         # 保护 items / item_keys
    status_text: str
    running: bool
    _subs: list[queue.Queue]     # SSE 订阅者

    def start(self) -> str | None / stop(self)       # start 失败返回给用户看的错误文案（壳层决定弹窗还是忽略）
                                                     # 保留 join 旧线程 + 新 Event 的竞态修复（commit be583e0）
    def subscribe(self) -> queue.Queue / unsubscribe(q)
    def snapshot(self) -> dict                        # {items, status, running, users, kinds}
    def clear(self)
    def save_users(self, patch: dict)                 # 配色/静音/查追加 写回 config.json
    def test_toast(self)
```

事件广播（`_broadcast(type, payload)`）：

| type | payload | 触发点 |
|---|---|---|
| `history` | `[entry...]`（已处理好的展示字段，同 `messages.db`） | 某来源首轮基线（不弹通知） |
| `new` | `[entry...]` | 有新动态（已按 key 去重、已入列写库；通知在广播之后弹） |
| `status` | `{text, running, last_check}` | 每轮结束 / 抓取失败 / 启停 |
| `config` | `{users:[...]}` | 保存用户设置后 |
| `cleared` | `{}` | 清空列表 |

订阅者队列满（默认 200）就丢最旧的，避免某个挂死的浏览器 tab 拖住后台线程。

### 线程与 SQLite

现在的约束是"只在 tkinter 主线程读写 `messages.db`"。网页版没有主线程了，改成：

- **后台抓取线程独占写库**：`_add_item()` 里 `save_message()`，连接在 `_run_loop` 开头打开、结束时关。
- **启动时读历史**：`server.py` 主线程用一个独立的只读连接 `load_recent_messages()`，填完 `items` 就关掉。
- HTTP handler 线程**不碰 SQLite**，只读内存里的 `items`（加 `lock`）。唯一例外是 `/api/items` 翻历史，开临时只读连接、用完即关。

`state.json`（去重 key）仍只由后台线程读写，不变。

`_emit()` 的"按来源分别 seed"语义、`_handle_new()` 的静音/合并通知逻辑、`_append_watch` 的退避策略全部原样保留。

## 5. HTTP 接口（`server.py`）

默认只绑 `127.0.0.1`；`--host 0.0.0.0`（`run_gui_lan.bat`）开放局域网，让平板/手机当小副屏，启动时会打印私网段的访问地址（`lan_ips()`，故意不用 UDP connect 猜出口地址——开着 TUN 代理时那会猜到 198.18.x.x）。所有 `POST` 校验 `Origin` 头必须等于自己（`http://<Host 头>`，所以局域网地址访问也能过），否则 403——防止其它网页用 `fetch` 打本地端口。

平板当副屏时浏览器地址栏很占地方，两条路：① `index.html` 带 `manifest.webmanifest`（`display: standalone`）和 `apple-mobile-web-app-capable` meta，「添加到主屏幕」后独立窗口打开——iPad Safari 明文 http 也生效，Android Chrome 要求安全上下文（局域网 http 需在 flags 里豁免）；② 页面内「全屏」按钮走 Fullscreen API（iPad Safari 只有 webkit 前缀版），standalone 模式下没有这个 API、按钮自动隐藏。图标 `icon-192/512.png` 是 `favicon.svg` 用浏览器 canvas 栅格化出来的，`apple-touch-icon.png` 额外铺了不透明底色（iOS 会把透明角渲染成黑）。

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| GET | `/` | — | `web/index.html` |
| GET | `/assets/<path>` | — | `web/` 下静态文件（拒绝 `..`） |
| GET | `/api/snapshot?limit=300` | — | `{items（内存里最近 limit 条）, has_more, status:{text,running,last_check}, users:[{name,uid,source,color,mute,check_appends}], config:{poll_interval_seconds, append_check_interval_seconds}}` |
| GET | `/api/items?before=<time>&before_key=<key>&limit=200` | — | 从 `messages.db` 翻更早的历史（「加载更早」用，见 §8.3）；游标是 `(time, key)` 二元组，同一秒多条时不重不漏 |
| GET | `/api/events` | — | SSE，`event: <type>\ndata: <json>\n\n`；连上先发一条 `status`；每 25 s 发 `: ping` 保活 |
| POST | `/api/control` | `{"action":"start"\|"stop"\|"clear"\|"test_toast"\|"quit"}` | `{"ok":true}`；`quit` 回复后 0.5 s 调 `os._exit(0)` |
| POST | `/api/users` | `{"users":[{name,color,mute,check_appends}]}` | `{"ok":true}`；只写回请求里出现的用户，沿用现在"不遍历没渲染出来的用户"的规则（推特/微博下线期间不能被误清空） |
| GET | `/api/ai/settings` | — | `{active, profiles:[{id,name,base_url,model,key_hint,has_key}], prompt, default_prompt}`；`api_key` 明文永远不出后端，只给脱敏的 `key_hint` |
| POST | `/api/ai/settings` | `{active, prompt, profiles:[{id?,name,base_url,model,api_key?}]}` | 同 GET；profile 不带 `api_key` 表示"没改"，沿用旧配置里同 id 的 key |
| POST | `/api/ai/test` | `{profile}` | `{"ok":true,"reply"}`；发一条极短的请求验证接口能通，可以测还没保存的配置 |
| GET | `/api/ai/summary?name=&date=` | — | `{summary: 缓存的日报或 null, current_count: 当天现在的有效条数}` |
| POST | `/api/ai/summary` | `{name, date, force?}` | `{summary:{name,date,model,created_at,item_count,text}, cached}`；同步调模型，通常 20~60 秒；有缓存且不 `force` 就直接返回缓存 |

`item` 字段与 `messages.db` 一致：`key, name, kind, icon, time, bar, content, link`。打开原帖直接前端 `<a target="_blank">`，不经过后端。

## 6. 前端（`web/`）

React 18 + htm，无状态管理库，`useReducer` 一个 store 足够。

### 组件树

```
App
├─ TopBar          ☰ 侧栏开关 · 品牌「谛听」 · 状态胶囊 · 操作按钮（窄屏折叠进 ⋯ 菜单）
├─ SideBar         用户列表（色块 + 今日条数 + 🔕/追加 标记）· 类型筛选 chip；可收成 56px 窄轨
├─ Feed
│  └─ DateGroup    sticky 日期头（今天 / 日期 + 星期 + 条数），可折叠，默认只展开今天
│     └─ Card      色条=用户色 · 用户名 · 类型 pill · 股吧名 · 时间 · 上下文条 · 正文（2 行截断，点开展开）
├─ NewBadge        底部悬浮「↓ N 条新动态」，用户不在底部时出现
├─ SettingsDrawer  每用户一行：色板（8 个中国传统色 + 默认）· 静音 · 查追加
└─ Toast           页面内轻提示（保存成功 / 连接断开）
```

### 数据流

```
启动: GET /api/snapshot → store.replace()
      new EventSource('/api/events')
        history / new → store.append()  (按 key 去重，按 time 插入)
        status        → store.status
        config        → store.users
        cleared       → store.replace([])
      onerror        → 顶部显示「已断开，重连中…」横幅；EventSource 自动重连；重连成功后重新拉 snapshot 补漏
```

### 前端状态（持久化到 `localStorage`）

- `diting.rail` 侧栏是否收起（首次打开时窗口宽度 < 900 px 默认收起）
- `diting.theme` `system | light | dark`
- `diting.collapsed` 手动折叠过的日期（只记非今天的）
- `diting.filter` 当前用户/类型筛选
- `diting.view` `feed | summary`（顶栏「日报」按钮切换主区视图）

### 响应式断点

- `≥ 900 px`（横屏）：侧栏展开、按钮带文字、次要操作直接摆在顶栏。
- `< 900 px`（竖屏副屏）：按钮只留图标，测试通知/清空/主题/退出收进 ⋯ 菜单，状态胶囊只剩绿点和时间。
- `< 560 px`：设置抽屉铺满宽度，每行色板和开关竖排。

## 7. 视觉规范

沿用视觉稿的 token，正式代码直接照抄 `web-mock.html` 里的 `:root` 块。

**色**（浅色 / 深色）

| token | 浅色 | 深色 | 用途 |
|---|---|---|---|
| `--bg` | `#f4f5f7` | `#14171d` | 页面底，偏冷灰 |
| `--card` | `#ffffff` | `#1c2028` | 卡片、顶栏、侧栏 |
| `--ink` / `--ink-2` / `--ink-3` | `#1b1f27` / `#4a5160` / `#8a91a0` | `#e7eaf0` / `#b2b9c7` / `#737b8c` | 正文 / 次要 / 辅助 |
| `--accent` | `#1772b4` 群青 | `#5aa3e0` | 选中态、主按钮 |
| `--ok` / `--warn` / `--danger` | 竹绿 / 土黄 / 朱红 | — | 运行状态 / 「新」角标 / 退出 |

类型色沿用 `app.py` 的 `KIND_BG`（发帖绿 / 评论蓝 / 转发橙 / 追加棕），用户自定义色沿用 `PALETTE` 的 8 个中国传统色。

**两个正交维度不变**：用户色 → 左侧 4 px 色条 + 用户名；类型色 → 小 pill 标签。正文永远用 `--ink`，不再被用户色劫持。「追加」类型的色条是虚线。

**字**：品牌「谛听」Noto Serif SC 700（子集字体，离线可用，缺了退回 SimSun）；正文 `Microsoft YaHei UI`；时间/计数 JetBrains Mono → Consolas，`tabular-nums`。

**主题**：默认跟随系统 `prefers-color-scheme`，顶栏可手动切换并记住。

## 8. 相对 tkinter 版的功能变化

### 8.1 保留（行为不变）
自动开始监控、首轮基线不弹通知、按用户静音、通知合并阈值 `MERGE_LIMIT`、日期分组默认只展开今天、跟随滚动到底、查追加及退避、用户配色/静音/查追加设置。

### 8.2 去掉
右键"查看全文"弹窗（卡片点开即展开）、多屏坐标计算、`sv_ttk` 依赖、`_can_fast_append` 快速追加优化（React 自己做 diff）。

### 8.3 新增（第一版就做）
- 左栏按用户 / 类型筛选
- 侧栏可收缩、竖屏顶栏适配
- 深色模式
- 新动态「新」角标 + 淡入高亮；底部「↓ N 条新动态」按钮
- 评论/转发/追加的上下文从正文里拆出来单独显示（`content` 里现在是 `[评论《xx》] 正文` 这种拼好的字符串，前端用正则拆；DB 结构不改）
- 股吧「回复评论」条目多带 `quote_user`/`quote_text`（被回复的那条评论，`messages.db` 后加的两列）：折叠态 ctx 标签尾巴显示「· 回复 某某」，展开后正文上方渲染成 `<blockquote class="quote">`，把"别人说的"和"博主说的"分开
- 浏览器标签页标题显示未读数 `(3) 谛听`，切回页面后清零——放在副屏时一眼能看到
- 「加载更早」：首屏只取最近 300 条，往上滚到顶再从 `messages.db` 翻页，取代现在硬上限 `MAX_ROWS=1000` 一次全塞
- SSE 断线横幅 + 自动重连补漏
- 「退出程序」按钮（关掉标签页进程不会退出，必须有个出口）

### 8.4 第二期（本次不做，记下来）
- 页面内搜索（`messages.db` 全文 LIKE 查询）
- 「上次看到这里」分割线（记录最后浏览的 key）
- 在页面里增删监控用户（现在还是手改 `config.json`）
- 系统托盘图标 + 开机自启（要加 `pystray`）
- 紧凑单行模式
- 推特 / 微博来源恢复时的 UI（`ENABLE_TWITTER` / `ENABLE_WEIBO` 开关的语义要搬到 `core.py`，`/api/users` 保存逻辑同样只遍历已启用的来源）

## 9. 实施路线

整个改造**分 7 个阶段、预计 7～9 次独立会话**完成，不是一次做完。每个阶段的产物都能独立提交、独立运行，
做完一个阶段就 commit + push，下一次会话从「进度表」里没打勾的第一项接着做。
每个阶段都写明了**完成标准**——达不到就不算完，不要为了赶进度把验证跳过。

### 进度表

做完一个阶段就在这里打勾、填提交号。这是跨会话的唯一进度来源，别只记在脑子里。

| 阶段 | 内容 | 状态 | 提交 |
|---|---|---|---|
| 0 | 设计文档 + 视觉稿 | ✅ | `003002f` |
| 1 | `core.py`：抽离无 UI 的 `MonitorCore`，`app.py` 改为消费它 | ✅ | `4e44137` |
| 2 | `server.py` 最小版：静态文件 + `/api/snapshot` + `/api/events` + `--mock` | ✅ | `7e84399` |
| 3 | `web/` 骨架：vendor 落地、Feed 只读渲染、接 snapshot + SSE | ✅ | `547b494` |
| 4 | 交互：筛选、日期折叠、卡片展开、新动态角标、跟随滚动、标题未读数 | ✅ | `3efd270` |
| 5 | 设置抽屉 + `/api/users` + `/api/control`（启停/清空/测试通知/退出） | ✅ | `d104437` |
| 6 | 收尾：深色模式、竖屏适配、加载更早、断线横幅、启动脚本、文档 | ✅ | `f3162c3` `7266122` |
| 7 | 稳定运行 ≥ 1 周后删 `app.py`（网页版自 2026-09-14 起正式使用） | ⬜ | |

### 阶段 1 — `core.py`（预计 1 次会话，改动最大也最危险）

**目标**：`MonitorApp` 里所有不碰 tkinter 的东西搬进 `core.MonitorCore`，`app.py` 变成一层薄 UI 壳。这一步**不加任何新功能**，纯重构。

要搬的（按 `app.py` 现有方法名）：
- 状态：`items` / `item_keys` / `_seeded` / `_append_watch` / `_append_fail_streak` / `_stop_event` / `_thread` / 用户配置映射（`_refresh_config_maps` 产出的 name→color/mute/check_appends 字典）
- 线程：`start()` / `stop()`（**保留 join 旧线程 + 新建 `threading.Event` 的顺序**，见 commit be583e0）、`_run_loop()`、`_emit()`、`_register_append_watch()` / `_append_backoff_interval()` / `_check_append_watch()`
- 数据：`_add_item()`（含 `save_message()` 写库）、`_load_history_from_db()`、`_handle_new()` 里的静音判断 + `MERGE_LIMIT` 合并 + 弹 toast
- 常量：`KIND_BG` / `PALETTE` / `MERGE_LIMIT` / `ENABLE_TWITTER` / `ENABLE_WEIBO` 搬到 `core.py`，`app.py` 从 `core` import

不搬的（留在 `app.py`）：`_setup_style` / `_build_ui` / `_rebuild` / `_can_fast_append` / `_append_pending_rows` / `_show_full_content` / `_monitor_work_area` / `open_colors` 的窗口部分 / `_poll_queue`。

`queue.Queue` → 主线程的单播改成 `_broadcast(type, payload)` 多播（§4 的事件表）。`app.py` 的 `_poll_queue()` 改成 `subscribe()` 一个队列消费，行为和现在一样。

**SQLite 归属改动**（§4「线程与 SQLite」）：写库从主线程挪到后台线程，`_run_loop` 开头开连接、`finally` 里关。`app.py` 不再持有 `self.db`。

**完成标准**：
- [ ] `py_compile` 两个文件都过
- [ ] 用 CLAUDE.md 里的离线 monkeypatch 方法启动 `app.py`，灌假数据截图，和改动前一样
- [ ] 真实 `config.json` 跑 `python app.py` 10 分钟：首轮不弹通知、状态栏正常刷新、`messages.db` 有新写入、关窗口进程能退出（后台线程 join 成功）
- [ ] `core.py` 顶部 `import` 里**没有** `tkinter`
- [ ] `.gitignore` 加 `!/core.py`

提交：`refactor: 抽离 MonitorCore，app.py 只剩 tkinter 壳`

### 阶段 2 — `server.py` 最小版（预计 1 次会话）

**目标**：不写一行前端，纯用 `curl` 就能验证后端。

- `ThreadingHTTPServer` + `BaseHTTPRequestHandler`，默认绑 `127.0.0.1`（`--host` 可改），端口 17777 被占自动 +1（`OSError` 重试，最多 20 次）
- `GET /` 和 `GET /assets/<path>`：从 `web/` 读文件，`os.path.realpath` 校验不能逃出 `web/`；MIME 表手写几项（html/css/js/woff2/svg/png）即可
- `GET /api/snapshot`：按 §5 的结构返回，`items` 加 `lock` 读内存
- `GET /api/events`：SSE。`subscribe()` 拿队列，循环 `q.get(timeout=25)`，超时发 `: ping\n\n`，客户端断开（`BrokenPipeError` / `ConnectionResetError`）时 `unsubscribe()`。连上先发一条 `status`
- `--mock`：`MonitorCore` 加一个 `mock=True` 构造参数——不读 `config.json`、不起抓取线程，`items` 用内置示例数据（从 `web-mock.html` 里那份 JSON 抄出来放 `core.py` 底部或 `mock_data.py`），另起一个线程每 10 秒随机 `_broadcast("new", [...])`
- `--no-browser`：调试用，不自动 `webbrowser.open`
- 主入口：启动时 `load_recent_messages()` 填 `items` → `core.start()` → 起 HTTP → 开浏览器 → `serve_forever()`；`Ctrl-C` 时 `core.stop()` 再退出

**完成标准**：
- [ ] `python server.py --mock --no-browser` 起来后 `curl 127.0.0.1:17777/api/snapshot` 返回合法 JSON，`items` 非空
- [ ] `curl -N 127.0.0.1:17777/api/events` 能看到首条 `event: status`、之后每 10 秒一条 `event: new`、期间有 `: ping`
- [ ] 开两个 `curl -N`，`Ctrl-C` 掉一个，另一个仍在收事件，且 `core._subs` 长度回落（打日志确认）
- [ ] `curl 127.0.0.1:17777/assets/../core.py` 返回 404 而不是源码
- [ ] 手动占住 17777 端口再启动，能落到 17778
- [ ] `.gitignore` 加 `!/server.py`（`mock_data.py` 若单独建也要加）

提交：`feat: 本地 HTTP 服务 + SSE 事件流（--mock 离线模式）`

### 阶段 3 — `web/` 骨架（预计 1 次会话）

**目标**：浏览器打开能看到和视觉稿一致的只读列表，数据来自真实 snapshot + SSE。

- vendor 文件落地：`react.production.min.js` / `react-dom.production.min.js`（18.3.1 UMD）、`htm.min.js`（3.1.1）。子集字体 `diting-serif.woff2` 用 `pyftsubset`（`pip install fonttools`）从 Noto Serif SC 抠「谛听」两字；抠不出来就先不放，CSS 回退 SimSun，**不要阻塞这个阶段**
- `index.html`：三个 `<script>` 标签 + `<div id="root">` + `app.js`
- `app.css`：把 `web-mock.html` 的 `:root` token 和卡片样式整体搬过来，去掉 Babel 相关
- `app.js`：JSX → htm 改写。组件先只做 `App` / `TopBar`（静态） / `Feed` / `DateGroup` / `Card`；`useReducer` store 实现 `replace` / `append`（按 `key` 去重、按 `time` 插入保持有序）/ `status`
- 上下文拆分：`content` 里的 `[评论《xx》] 正文` 用正则拆成 `ctx` + `body`，放 `Card` 里
- 侧栏这一阶段可以先是静态用户列表（不带筛选）

**完成标准**：
- [ ] `python server.py --mock` 打开页面，列表和 `web-mock.html` 视觉一致（并排截图对比）
- [ ] 每 10 秒 mock 推送的新条目出现在正确日期组、正确时间位置
- [ ] 浏览器 DevTools Network 面板：除了 `127.0.0.1` 没有任何外部请求（离线可用的硬性要求）
- [ ] 刷新页面不重复、不丢条目
- [ ] `.gitignore` 加 `!/web/`

提交：`feat: 网页版前端骨架，只读渲染 + SSE 实时更新`

### 阶段 4 — 交互（预计 1 次会话）

- 侧栏：用户列表带色块 + 今日条数，点击筛选；类型 chip 筛选；侧栏收缩成 56px 窄轨（`localStorage: diting.rail`）
- 日期组折叠，默认只展开今天（`diting.collapsed`）
- 卡片正文 2 行截断，点击展开/收起
- 新动态：`new` 事件进来的条目带「新」角标 + 淡入高亮（3 秒后褪去）；用户不在底部时显示 `NewBadge`「↓ N 条新动态」，点击滚到底
- 跟随滚动：在底部时来新条目自动跟；不在底部不打扰
- `document.title` 未读数：页面 `visibilityState !== 'visible'` 时累加，切回清零

**完成标准**：
- [ ] `--mock` 下把页面滚到中间，等新推送：不跳动、右下角出现角标；点角标滚到底且角标消失
- [ ] 切到别的标签页等 30 秒再切回：标题曾显示 `(3) 谛听` 之类，切回后恢复
- [ ] 选一个用户 + 一个类型筛选后刷新页面，筛选状态还在
- [ ] 折叠昨天、刷新，昨天仍折叠；今天永远展开

提交：`feat: 筛选、折叠、新动态角标与跟随滚动`

### 阶段 5 — 设置与控制（预计 1 次会话）

- `POST /api/control`：`start` / `stop` / `clear` / `test_toast` / `quit`。`quit` 回 `{"ok":true}` 后 `threading.Timer(0.5, os._exit, [0])`
- `POST /api/users`：只更新请求里出现的用户；写 `config.json` 前先读现有内容合并，**不动没出现的用户和其它顶层键**（`weibo_cookie` 等）；成功后 `_broadcast("config", ...)`
- `Origin` 校验：所有 `POST` 没有 `Origin` 或不等于 `http://<Host 头>` 一律 403
- 前端 `SettingsDrawer`：每用户一行，8 色色板 + 默认、静音开关、查追加开关；保存后 `Toast` 提示
- 顶栏按钮接上：开始/停止、清空、测试通知、退出（退出弹 `confirm`）

**完成标准**：
- [ ] 改一个用户颜色保存，`config.json` 里只有该用户的 `color` 变了，`diff` 确认其它字段原样
- [ ] `curl -X POST -H 'Origin: http://evil.com' 127.0.0.1:17777/api/control -d '{"action":"quit"}'` 返回 403，进程还活着
- [ ] 页面点「退出」，进程在 1 秒内结束，端口释放
- [ ] 停止再开始，`_thread` 只有一个活着（`threading.enumerate()` 打日志）——不能退化出重复推送

提交：`feat: 设置抽屉与控制接口`

### 阶段 6 — 收尾（预计 1～2 次会话）

- 深色模式：`prefers-color-scheme` 默认 + 顶栏手动切换（`diting.theme`）
- 响应式三档断点（§6）；在竖屏副屏上实际摆一下看
- 「加载更早」：`GET /api/items?before=&limit=`，前端滚到顶触发，首屏改为只取 300 条
- SSE 断线：`EventSource.onerror` 显示横幅；`onopen` 时若不是首次连接则重拉 `/api/snapshot` 合并补漏
- `run_gui.bat` 改指向 `server.py`，新增 `run_gui_tk.bat` 指向 `app.py`
- README：截图换网页版、启动说明；CLAUDE.md：架构章节改写（两个入口变三个、线程模型、SQLite 归属、`--mock` 验证方法），删掉已不成立的 tkinter 限制说明
- `.gitignore`：确认 `core.py` / `server.py` / `web/` / 新 bat 都已放行

**完成标准**：
- [ ] 杀掉 `server.py` 再重启，页面横幅出现又消失，期间 mock 推的条目补回来了
- [ ] 系统切深色，页面跟着变；手动切浅色后系统再切，页面不变
- [ ] 浏览器窗口拖到 500px 宽，顶栏不溢出、设置抽屉铺满
- [ ] 造 1000+ 条历史（mock 数据循环塞库）验证「加载更早」翻页不重不漏
- [ ] 一个全新 clone 按 README 能跑起来

提交拆成多个：`feat: 深色模式与竖屏适配` / `feat: 历史翻页与断线补漏` / `docs: 网页版启动说明与架构文档更新`

### 阶段 7 — 删旧版

真实使用 ≥ 1 周没有回退到 `app.py` 的理由后：删 `app.py` / `run_gui_tk.bat`，`.gitignore` 同步去掉，CLAUDE.md 删掉 tkinter 相关全部段落，`requirements.txt` 去掉 `sv_ttk`。

提交：`chore: 移除 tkinter 旧版`

### 跨阶段的注意事项

- **每个阶段开始前**先 `git log` 确认上一阶段的提交在，再读一遍本节对应阶段的条目——不要凭记忆。
- **阶段 1 是唯一会碰现有行为的**，其余阶段都是纯新增，坏了也不影响 `app.py`。所以阶段 1 要格外保守：不顺手改任何"看着不顺眼"的逻辑。
- 阶段 2～6 都依赖 `--mock` 离线验证，真实 `config.json` 只在阶段 1 和阶段 6 末尾各跑一次。
- 中途发现设计文档写错了（接口字段、事件名等），**先改文档再改代码**，同一个 commit 提交。

### 验证手段

没有测试框架。`server.py` 提供 `--mock` 参数：不读 `config.json`、不联网，用 `web-mock.html` 里那套示例数据灌进 `MonitorCore`，每 10 秒随机推一条"新动态"事件——用来离线验证前端渲染和 SSE 流程。语法检查照旧 `py_compile`。

## 10. 已定的决策

| 问题 | 决定 |
|---|---|
| 窗口形态 | 浏览器标签页，页面里有「退出程序」；`pywebview` 独立窗口不做 |
| 端口 | 固定 `17777`，被占用自动 +1 |
| 前端 | React 18 + htm，vendor 本地文件，零构建 |
| 旧版 `app.py` | 先保留，网页版稳定后删 |
| 品牌副标题「东方财富股吧监控」 | 删掉，顶栏只留「谛听」 |
| 默认布局 | 窄屏（竖屏副屏）自动收起侧栏，用户手动切换后以用户选择为准 |
