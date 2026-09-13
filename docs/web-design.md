# 谛听 · 网页版设计文档

> 分支：`feature/web-ui`。目标是把 tkinter 桌面窗口换成「本地 HTTP 服务 + 浏览器页面」，
> 抓取/去重/通知等核心逻辑不变。视觉稿见 [web-mock.html](web-mock.html)（用浏览器直接打开即可，含示例数据，需联网加载 React/字体）。

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
  ├─ http.server.ThreadingHTTPServer  绑 127.0.0.1:17777（被占则 +1 顺延）
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
web/
  index.html       壳子：加载 vendor 脚本 + app.js
  app.css          全部样式（设计 token 见 §7）
  app.js           React 组件（htm 模板，见下）
  vendor/
    react.production.min.js        18.3.1 UMD
    react-dom.production.min.js    18.3.1 UMD
    htm.min.js                     3.1.1
    diting-serif.woff2             Noto Serif SC 只含「谛听」两字的子集（品牌字）
    jetbrains-mono.woff2           时间/数字用的等宽字体（可选，缺了退回 Consolas）
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

    def start(self, silent=False) / stop(self)       # 保留 join 旧线程 + 新 Event 的竞态修复（commit be583e0）
    def subscribe(self) -> queue.Queue / unsubscribe(q)
    def snapshot(self) -> dict                        # {items, status, running, users, kinds}
    def clear(self)
    def save_users(self, patch: dict)                 # 配色/静音/查追加 写回 config.json
    def test_toast(self)
```

事件广播（`_broadcast(type, payload)`）：

| type | payload | 触发点 |
|---|---|---|
| `history` | `[item...]` | 某来源首轮基线（不弹通知） |
| `new` | `[item...]` | 有新动态（已按 key 去重，已弹通知） |
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

只绑 `127.0.0.1`。所有 `POST` 校验 `Origin` 头必须等于自己（`http://127.0.0.1:<port>`），否则 403——防止其它网页用 `fetch` 打本地端口。

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| GET | `/` | — | `web/index.html` |
| GET | `/assets/<path>` | — | `web/` 下静态文件（拒绝 `..`） |
| GET | `/api/snapshot` | — | `{items, status:{text,running,last_check}, users:[{name,uid,color,mute,check_appends}], config:{poll_interval_seconds, append_check_interval_seconds}, db_count}` |
| GET | `/api/items?before=<time>&limit=200` | — | 从 `messages.db` 翻更早的历史（「加载更早」用，见 §8.3） |
| GET | `/api/events` | — | SSE，`event: <type>\ndata: <json>\n\n`；连上先发一条 `status`；每 25 s 发 `: ping` 保活 |
| POST | `/api/control` | `{"action":"start"\|"stop"\|"clear"\|"test_toast"\|"quit"}` | `{"ok":true}`；`quit` 回复后 0.5 s 调 `os._exit(0)` |
| POST | `/api/users` | `{"users":[{name,color,mute,check_appends}]}` | `{"ok":true}`；只写回请求里出现的用户，沿用现在"不遍历没渲染出来的用户"的规则（推特/微博下线期间不能被误清空） |

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

## 9. 实施顺序

每步独立可提交、可运行，走 conventional commits。

1. **`core.py`**：把 `MonitorApp` 的非 UI 部分搬过去，`app.py` 改成 import `core` 跑（保证旧版还能用，作为回归验证）。
2. **`server.py` 最小版**：`/api/snapshot` + `/api/events` + 静态文件，用 `curl` 验证。
3. **`web/` 骨架**：拉 vendor 文件，index + Feed 只读渲染，接 snapshot + SSE。
4. **交互**：筛选、折叠、展开、新动态角标、跟随滚动。
5. **设置抽屉** + `/api/users` + `/api/control`。
6. **收尾**：深色模式、竖屏适配、加载更早、断线横幅、退出、`run_gui.bat`、README、CLAUDE.md 更新。
7. 稳定运行一段时间后删 `app.py`。

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
