# 谛听 · 东方财富股吧动态监控

![License](https://img.shields.io/badge/License-MIT-green.svg)
![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)

> 谛听是地藏菩萨的坐骑，传说能听辨世间万物之声。本项目借这个名字：盯着几位股吧博主，他们一发声就提醒你。

一个跑在本机的 Windows 小工具：轮询指定用户在东方财富股吧的**发帖、转发、评论**和**发帖后 24 小时内的追加内容**，有新动态就弹 Windows 通知、（可选）响一声，并显示在浏览器页面里。

纯 Python 标准库 + 可选的 `winotify`，前端 React 18 + htm 全部本地 vendor、零构建；除了东财接口之外不访问任何外部服务，也没有账号、推送额度之类的依赖。

![界面截图](docs/screenshot.png)

---

## 功能

**监控**
- 抓取股吧用户的发帖 / 转发（`userdynamiclistv2`）和评论（`myreply`）；评论会标出「评论了谁的哪个帖子」，如果是回复别人的评论，展开卡片还能看到被回复的那条原话。
- 长帖补全：列表接口把超过 200 字的正文截成摘要，新抓到的长帖会再请求一次全文接口换成完整内容（取不到就保留摘要）。
- 「查追加」：勾选的用户发帖后 24 小时内，定期查帖子有没有「作者更新」的追加内容，查到就当新动态提醒。按帖单独请求的接口比列表接口更容易触发东财的验证页，所以这条路径带了失败退避（间隔按 2 倍拉长、封顶 2 小时，成功后复原）。
- 每个来源第一次抓取成功时把已有内容当基线载入、不弹通知，避免启动刷屏。
- 每个用户可以单独设静音（只入列表不弹通知）。
- 所有抓到的动态写进本地 SQLite（`messages.db`），重启不丢；页面顶部「加载更早」按 `(时间, key)` 游标往前翻。

**页面**
- 卡片模式 / 紧凑单行模式切换；卡片点开显示全文、原帖链接和行情链接。
- 正文里的 `$股票名(代码)$` 和所属股吧名会变成东方财富行情页链接。
- 搜索：`/` 聚焦搜索框，边输入边过滤当前列表并高亮；回车再搜整个数据库。`Esc` 清空。
- 「上次看到这里」：切走标签页再回来，会在切走的位置插一条分界线，标出之后来了哪些新动态。
- 声音提示（可关）：用 Web Audio 合成一个短音，只在页面处于后台或窗口没焦点时响，正盯着页面时不响。
- 浅色 / 深色 / 跟随系统；侧栏可收成一列首字徽章（窗口窄于 900px 时首次打开默认收起，之后记住你的选择）。
- 用户和轮询参数都能在页面「设置」抽屉里改，直接写回 `config.json`，不用重启。
- 局域网访问：`--host 0.0.0.0` 后可以用平板/手机开同一个页面当小副屏（见下文）。

**AI 日报**
- 顶栏「日报」进入：选一个博主、选一天，点「生成日报」，让模型把这个人当天所有发帖/评论/追加整理成「一句话总览 → 大盘板块观点 → 个股操作台账（表格）→ 回答粉丝的个股判断 → 明日计划 → 其它」。评论会连同被回复者的原话一起喂给模型，不然「不能」「短线」这种回答没法理解。
- 结果存进 `messages.db`，同一个人同一天只花一次钱；生成后又抓到新动态会提示可「重新生成」。
- 日报页右上角齿轮打开 AI 设置：接口地址 / 模型 / API Key 自由添加多套、切换，「测试连接」可以先验一下；总结要求（提示词）也能改。任何 OpenAI 兼容接口都行（DeepSeek、通义千问、Kimi、OpenAI…），Key 只存在本机 `config.json`。这是全程序**唯一**会主动联外网（除东财外）的功能，只在你点「生成」时发请求。

**我的评论**
- 顶栏「我的」（窄屏在「⋯」菜单里）进入：填一次自己的东财 UID（个人主页 `i.eastmoney.com/` 后面那串数字，粘贴整个地址也行），点「开始同步」，程序在后台一页页往回翻你的全部历史评论，存进本机 `messages.db`。
- 每条都标出**回复给谁**：回复别人的评论时显示对方昵称和被回复的原话，直接评论帖子时显示帖主；底下是帖子标题和原帖链接。
- 按时间倒序（最新在上）、按日期分组；可以搜关键词（评论、帖子标题、对方原话都搜），也可以点「回复对象」里的名字只看回复某个人的。
- 之后点「同步最新」只补新增的部分，一般翻一页就够；中途停止或网络出错也不要紧，下次会从停下的页附近接着翻。「全量重翻」从第 1 页重新走一遍，用来补漏。
- 翻页间隔 1.5~3 秒，和平时轮询用的是同一个公开列表接口；评论几千条的话第一次要翻几分钟。

**代码里还有但默认关闭的**
- 推特（X）和微博的抓取、解析都在 `monitor.py` 里，`core.py` 顶部的 `ENABLE_TWITTER` / `ENABLE_WEIBO` 改成 `True` 即可启用。推特依赖外部 CLI `twitter-cli` 和登录 cookie，微博需要 `config.json` 里的 `weibo_cookie`。这两个来源风控更严，默认关着。
- `monitor.py` 单独运行是一个命令行版，用 Server酱 / PushPlus 推到微信，**只处理股吧用户**，也不写 `messages.db`。
- `app.py` 是旧的 tkinter 窗口版，功能落后于网页版，不再加新功能。

---

## 快速上手

需要 Windows 10/11、Python 3.9+，浏览器随意。

```bash
git clone https://github.com/Havadking/diting.git
cd diting
pip install -r requirements.txt     # 只有可选的 winotify（系统通知）和 sv_ttk（旧窗口版皮肤），不装也能跑
```

把 `config.example.json` 复制为 `config.json`，填上要监控的用户（见下节），然后双击 `run_gui.bat` 或：

```bash
python server.py
```

服务监听 `http://127.0.0.1:17777`（被占自动 +1），会自动打开浏览器。

其它启动方式：

| 命令 | 说明 |
| :-- | :-- |
| `python server.py --mock` | 离线演示：不读配置、不联网，用内置示例数据，每 10 秒随机推一条（`--mock-interval N` 改间隔） |
| `python server.py --no-browser` | 不自动开浏览器 |
| `python server.py --port 18888` | 指定端口 |
| `python server.py --host 0.0.0.0` / `run_gui_lan.bat` | 开放局域网访问 |
| `python monitor.py` / `run.bat` | 命令行 + 微信推送版 |
| `python app.py` / `run_gui_tk.bat` | 旧版 tkinter 窗口 |
| `python test_once.py` | 抓取自检：打印 `config.json` 里第一个用户的最新几条 |

### 用平板当副屏

双击 `run_gui_lan.bat`，窗口里会打印类似 `http://192.168.1.23:17777` 的地址（有多个时选和路由器同网段的那个），服务在后台跑，看完地址窗口可以关。平板连同一个 Wi-Fi 打开这个地址即可。

- 首次会弹 Windows 防火墙提示，勾选允许；当前网络被标成「公用」时可能被拦，改成「专用」。
- 去掉浏览器地址栏：iPad 用 Safari「添加到主屏幕」会以独立窗口打开；Android Chrome 对局域网 http 地址不给安装 PWA，可以在 `chrome://flags/#unsafely-treat-insecure-origin-as-secure` 里填入该地址后再安装，或者直接用页面右上角（窄屏在 ⋯ 菜单里）的「全屏」按钮。
- 平板会自动熄屏，网页在 http 下拿不到 Wake Lock，要在系统设置里把息屏时间调长。

---

## 配置

### 在页面里改（推荐）

顶栏「设置」打开抽屉：
- 输入 UID → 「获取昵称」会调东财接口确认 UID 存在并带出昵称 → 可改备注名、颜色、静音、查追加 → 添加。
- 已有用户：点名字改备注，点色块换颜色，下拉选所属分组，切换静音 / 查追加，删除（二次确认）。
- 「分组管理」：新建 / 改名 / 排序 / 删除分组，给分组设组色（组内没单独选色的用户默认用组色）。删组不删用户，成员回到未分组。
- 底部改股吧轮询间隔和追加检查间隔。

建了分组后，左侧栏会按组收纳用户：点组名「只看本组」，点左边的箭头折叠 / 展开；没分组的用户收在「未分组」里。

改动会写回 `config.json` 并立即生效。

「我的评论」页填的 UID 存在 `config.json` 的 `my_uid` 字段，也可以直接写进去。

AI 接口在「日报」页右上角的齿轮里配，存到 `config.json` 的 `ai` 字段：

```json
"ai": {
  "active": "p1",
  "profiles": [{"id": "p1", "name": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-chat", "api_key": "sk-..."}],
  "prompt": ""
}
```

### 直接改 `config.json`

```json
{
  "poll_interval_seconds": 60,
  "append_check_interval_seconds": 300,
  "monitor_posts": true,
  "monitor_replies": true,
  "groups": { "核心": "#ED5126" },
  "users": [
    { "uid": "5591057086910116", "name": "张三", "group": "核心", "check_appends": true },
    { "uid": "1234567890123456", "name": "李四", "mute": true, "color": "#1772B4" }
  ],
  "push": { "type": "serverchan", "key": "只有命令行推送版用得到" }
}
```

- `groups` 是「组名 → 组色」，写的顺序就是侧栏里的顺序，组色留空串表示不设色；用户的 `group` 写组名。`color` 直接给用户指定颜色，优先于组色。页面里的 8 个色块是几种中国传统色，手填别的十六进制也行。
- 推特 / 微博相关字段见 `config.example.json`，默认开关关着时不会读。
- `config.json`、`state.json`、`messages.db`、`*.log` 都在 `.gitignore` 里，不会被提交。

**UID 怎么找**：打开博主的股吧个人主页，地址栏形如 `https://i.eastmoney.com/5591057086910116`，那串数字就是。

---

## 工作方式

```
东方财富股吧接口 ──轮询──> core.py 后台线程 ──> 去重 / 首轮基线 / 通知 / 写 messages.db
                                   │
                                   └─ 广播事件 ──> server.py（SSE /api/events）──> web/app.js
```

- `core.py`：一个后台线程轮询所有用户，处理好的条目写库、弹通知，再通过订阅队列广播给各个 SSE 连接。
- `server.py`：纯标准库 `ThreadingHTTPServer`，默认只绑 `127.0.0.1`。所有 `POST` 校验 `Origin` 头必须等于自己，防止别的网页用 `fetch` 打本地端口。
- `web/app.js`：单文件 React + htm，状态在一个 `useReducer` 里；断线重连后重拉快照合并补漏。
- `state.json` 只存每个来源见过的 key（各保留最近 500 条）用于去重；消息内容在 `messages.db`。

接口：

| 接口 | 方法 | 说明 |
| :-- | :-- | :-- |
| `/api/snapshot?limit=300` | GET | 首屏：最近条目、运行状态、用户列表、轮询参数 |
| `/api/events` | GET | SSE：`history` / `new` / `status` / `config` / `cleared` 事件 |
| `/api/items?before=&before_key=&limit=` | GET | 往前翻更早的记录 |
| `/api/search?q=&limit=` | GET | 搜整个 `messages.db`（正文、股吧、作者、被回复的评论） |
| `/api/probe_user?uid=` | GET | 确认 UID 存在并返回昵称 |
| `/api/users` | POST | 批量更新用户的颜色 / 静音 / 查追加 |
| `/api/users/manage` | POST | 增删用户、改备注、改轮询参数 |
| `/api/control` | POST | `start` / `stop` / `clear` / `test_toast` / `quit` |

更细的说明在 `docs/web-design.md`（网页版设计）、`docs/v2-design.md`、`docs/onboarding-notes.md`（架构与已知债务）。

---

## 项目结构

```
├── server.py            网页版入口：HTTP 服务 + API + SSE
├── core.py              监控核心：轮询线程、去重、追加监视、通知、SQLite
├── monitor.py           抓取与解析（股吧 / 推特 / 微博）、SQLite 封装；单独运行是命令行推送版
├── summary.py           AI 日报：预处理当天动态、拼提示词、调 OpenAI 兼容接口
├── mock_data.py         --mock 用的示例数据和 MockCore
├── app.py               旧版 tkinter 窗口
├── test_once.py         抓取自检
├── run_gui.bat / run_gui_lan.bat / run_gui_tk.bat / run.bat
├── config.example.json
├── docs/
└── web/                 前端：index.html、app.js、app.css、图标、manifest、vendor/（React、htm、marked、两个字体）
```

---

## 常见问题

**Windows 通知没弹出来？** 先看 `pip install winotify` 装了没；再看 Windows「设置 → 系统 → 通知」和专注助手 / 勿扰是否拦了；顶栏「测试通知」能弹出来就是正常的。

**声音没响？** 浏览器要求页面上有过一次点击/按键之后才允许出声，打开页面后随便点一下；另外它只在页面处于后台或窗口没焦点时才响。

**会不会被限流？** 列表接口不需要登录，默认 60 秒轮询、用户之间有随机间隔，正常用没遇到问题。按帖单独请求的接口（查追加、补全长帖全文用）更敏感，连续请求太多可能被拦成验证页，程序会识别并退避，但别把追加检查间隔调得太短。轮询间隔下限 10 秒。

**接口全是非官方的**，东财随时可能改，抓取失败会显示在顶栏状态里，不会让程序退出。

---

## 免责声明

仅供个人学习和自用。数据来自第三方网站的公开接口，可能随时变动或失效；使用本工具造成的任何后果由使用者自行承担。请合理设置抓取频率。

## 许可证

[MIT](LICENSE)
