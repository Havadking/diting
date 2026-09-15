# -*- coding: utf-8 -*-
"""
东方财富股吧 + 推特(X) - 指定用户动态实时监控

数据源:
  股吧发帖/转发: https://i.eastmoney.com/api/guba/userdynamiclistv2 (type=1)
  股吧评论/回复: https://i.eastmoney.com/api/guba/myreply
  股吧帖子全文/追加: https://gbapi.eastmoney.com/content/api/Post/ArticleContent
  推特发帖:      twitter-cli (twitter user-posts @handle --json)，需 X 账号 cookie
推送渠道: Server酱(serverchan) 或 PushPlus(pushplus)；桌面版用 Windows 通知
"""
import html as html_mod
import json
import os
import re
import sqlite3
import sys
import time
import random
import shutil
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "state.json")
LOG_PATH = os.path.join(BASE_DIR, "monitor.log")
DB_PATH = os.path.join(BASE_DIR, "messages.db")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 发帖/文章/转发：用「全部动态」接口(type=1)，能拿到股吧短帖（fullarticlelist 只有财富号文章，会漏帖）
POST_API = "https://i.eastmoney.com/api/guba/userdynamiclistv2?uid=%s&pagenum=1&pagesize=20&type=1"
REPLY_API = "https://i.eastmoney.com/api/guba/myreply?uid=%s&pageindex=1"
# 帖子全文 + 作者追加(post_add_list)。列表接口的 post_content 超过 200 字就截成摘要补"..."，全文和追加
# 都只有这里给。以前用帖子详情页 news,{code},{post_id}.html 抠内嵌 JSON，那个页面第一次请求就可能被拦成
# 反爬验证页；这个 JSON 接口实测温和得多。
ARTICLE_API = ("https://gbapi.eastmoney.com/content/api/Post/ArticleContent"
               "?postid=%s&plat=web&version=200&product=guba&deviceid=web")


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------- 配置 / 状态 ----------
def load_config():
    """缺文件抛 FileNotFoundError 而不是直接 sys.exit——这个函数会在 GUI/HTTP 的后台线程里被调，
    SystemExit 躲得过 `except Exception`，会把线程无声杀掉；命令行版在 main() 里自己兜底退出。"""
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError("找不到 config.json，请先按 README 填写配置。")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg):
    """整体写回 config.json。先写临时文件再替换，避免写一半崩了把配置弄成空文件。"""
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------- 消息持久化(SQLite) ----------
# 只存 app.py 渲染用的「成品」字段(标题/来源等已经拼进 content 里了)，方便原样取出来重新显示。
# 只在主线程用(app.py 只从 _add_item 里写、从启动流程里读)，故意不开 check_same_thread=False。
# 读出来直接塞进列表的展示字段。quote_user/quote_text 是「被回复的那条评论」（只有股吧"回复评论"才有），后加的列，
# 老库靠 get_db() 里的 ALTER TABLE 补上，读出来是 None 时统一转成空串。
MESSAGE_COLS = ["key", "name", "kind", "icon", "time", "bar", "content", "link", "quote_user", "quote_text"]
_SELECT_COLS = ", ".join(MESSAGE_COLS)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS messages (
        key TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        icon TEXT,
        time TEXT NOT NULL,
        bar TEXT,
        content TEXT,
        link TEXT,
        saved_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(time)")
    have = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    for col in ("quote_user", "quote_text"):
        if col not in have:
            conn.execute("ALTER TABLE messages ADD COLUMN %s TEXT" % col)
    # AI 日报缓存：同一个人同一天只花一次钱；item_count 记生成时喂了多少条，前端据此提示"又有新动态，要不要重新生成"
    conn.execute("""CREATE TABLE IF NOT EXISTS summaries (
        name TEXT NOT NULL,
        date TEXT NOT NULL,
        model TEXT,
        created_at TEXT NOT NULL,
        item_count INTEGER,
        text TEXT,
        PRIMARY KEY (name, date)
    )""")
    conn.commit()
    return conn


def _rows_to_dicts(cur):
    return [{k: (v if v is not None else "") for k, v in zip(MESSAGE_COLS, r)} for r in cur.fetchall()]


def save_message(conn, entry):
    """entry 是 app.py self.items 里那种已经处理好的 dict(key/name/kind/icon/time/bar/content/link)。"""
    conn.execute(
        "INSERT OR IGNORE INTO messages (key, name, kind, icon, time, bar, content, link, quote_user, quote_text, saved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (entry["key"], entry["name"], entry["kind"], entry.get("icon", ""), entry["time"],
         entry.get("bar", ""), entry.get("content", ""), entry.get("link", ""),
         entry.get("quote_user", ""), entry.get("quote_text", ""),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()


def load_recent_messages(conn, limit=1000):
    """按时间取最近 limit 条，返回时按时间升序(旧的在前)，方便直接塞进列表。"""
    cur = conn.execute(
        "SELECT " + _SELECT_COLS + " FROM messages ORDER BY time DESC LIMIT ?", (limit,))
    rows = _rows_to_dicts(cur)
    rows.reverse()
    return rows


def load_messages_before(conn, before_time, before_key, limit=200):
    """「加载更早」翻页：取严格早于游标 (before_time, before_key) 的 limit 条，返回按时间升序。
    游标用 (time, key) 二元组而不只是 time，避免同一秒有多条时漏掉或重复。"""
    cur = conn.execute(
        "SELECT " + _SELECT_COLS + " FROM messages "
        "WHERE time < ? OR (time = ? AND key < ?) "
        "ORDER BY time DESC, key DESC LIMIT ?", (before_time, before_time, before_key, limit))
    rows = _rows_to_dicts(cur)
    rows.reverse()
    return rows


def load_day_messages(conn, name, date):
    """某个人某一天（本地日期，YYYY-MM-DD）的全部动态，按时间升序。给 AI 日报用。"""
    cur = conn.execute(
        "SELECT " + _SELECT_COLS + " FROM messages WHERE name = ? AND time >= ? AND time < ? ORDER BY time, key",
        (name, date + " 00:00:00", date + " 23:59:60"))
    return _rows_to_dicts(cur)


SUMMARY_COLS = ["name", "date", "model", "created_at", "item_count", "text"]


def load_summary(conn, name, date):
    row = conn.execute("SELECT " + ", ".join(SUMMARY_COLS) + " FROM summaries WHERE name = ? AND date = ?",
                       (name, date)).fetchone()
    rec = dict(zip(SUMMARY_COLS, row)) if row else None
    # 早期版本会把模型返回的空串也存进来；这种记录当没有，让前端显示「生成日报」而不是一块空白
    return rec if rec and (rec.get("text") or "").strip() else None


def save_summary(conn, rec):
    conn.execute("INSERT OR REPLACE INTO summaries (" + ", ".join(SUMMARY_COLS) + ") VALUES (?, ?, ?, ?, ?, ?)",
                 tuple(rec.get(c) for c in SUMMARY_COLS))
    conn.commit()


def count_messages(conn):
    return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]


def search_messages(conn, keyword, limit=100):
    """根据关键词在 messages.db 中检索正文、吧名、作者名，返回按时间升序（旧的在前）以便列表统一追加。"""
    kw = (keyword or "").strip()
    if not kw:
        return []
    pat = "%" + kw + "%"
    cur = conn.execute(
        "SELECT " + _SELECT_COLS + " FROM messages "
        "WHERE content LIKE ? OR bar LIKE ? OR name LIKE ? OR quote_text LIKE ? "
        "ORDER BY time DESC, key DESC LIMIT ?", (pat, pat, pat, pat, limit))
    rows = _rows_to_dicts(cur)
    rows.reverse()
    return rows


# ---------- 抓取 ----------
def fetch_json(url, uid):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://i.eastmoney.com/%s" % uid,
        "Accept": "application/json, text/plain, */*",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", "ignore")
    return json.loads(body)


def get_list(data):
    """兼容返回结构，取出列表。"""
    if not isinstance(data, dict):
        return []
    res = data.get("result")
    if isinstance(res, dict):
        return res.get("list") or []
    if isinstance(res, list):
        return res
    return []


def make_link(code, post_id):
    if code and post_id:
        return "https://guba.eastmoney.com/news,%s,%s.html" % (code, post_id)
    if post_id:
        return "https://mguba.eastmoney.com/mguba/article/0/%s" % post_id
    return "https://guba.eastmoney.com/"


# ---------- 解析为统一格式 ----------
def parse_posts(uid):
    data = fetch_json(POST_API % uid, uid)
    items = []
    for p in get_list(data):
        pid = str(p.get("post_id") or "")
        if not pid:
            continue
        guba = p.get("post_guba") or {}
        code = guba.get("stockbar_code") or ""
        is_repost = bool(p.get("source_post_id"))
        items.append({
            "key": "P" + pid,
            "kind": "转发" if is_repost else "发帖",
            "icon": "🔁" if is_repost else "📝",
            "time": p.get("post_publish_time") or "",
            "title": (p.get("post_title") or "").strip(),
            "content": (p.get("post_content") or "").strip(),
            "bar": guba.get("stockbar_name") or "",
            "ctx_user": p.get("source_post_user_nickname") or "",
            "ctx_text": (p.get("source_post_title") or p.get("source_post_content") or "").strip(),
            "link": make_link(code, pid),
        })
    return items


def parse_replies(uid):
    data = fetch_json(REPLY_API % uid, uid)
    items = []
    for r in get_list(data):
        rid = str(r.get("reply_id") or "")
        if not rid:
            continue
        guba = r.get("reply_guba") or {}
        code = guba.get("stockbar_code") or ""
        src_post = str(r.get("source_post_id") or "")
        # 评论可能是回复别人的评论：此时 source_reply_* 是被回复的那条评论（列表接口直接给，不用再请求详情页），
        # source_post_* 仍是所在的帖子。直接评论帖子时 source_reply_id 为 0、source_reply_text 为空。
        to_user = r.get("source_reply_user_nickname") or r.get("source_post_user_nickname") or ""
        quote_text = (r.get("source_reply_text") or "").strip() if r.get("source_reply_id") else ""
        items.append({
            "key": "R" + rid,
            "kind": "评论",
            "icon": "💬",
            "time": r.get("reply_publish_time") or "",
            "title": "",
            "content": (r.get("reply_text") or "").strip(),
            "bar": guba.get("stockbar_name") or "",
            "ctx_user": to_user,
            "ctx_text": (r.get("source_post_title") or "").strip(),
            "link": make_link(code, src_post),
            "quote_user": (r.get("source_reply_user_nickname") or "").strip() if quote_text else "",
            "quote_text": quote_text,
        })
    return items


def probe_guba_user(uid):
    """探测指定 UID 是否是合法的股吧用户，并返回其东财昵称。"""
    uid = str(uid).strip()
    if not uid or not uid.isdigit():
        raise ValueError("UID 必须是纯数字")
    url = POST_API % uid
    data = fetch_json(url, uid)
    p_list = get_list(data)
    name = ""
    if p_list:
        p0 = p_list[0]
        name = p0.get("user_nickname") or p0.get("user_name") or ""
    if not name:
        try:
            rdata = fetch_json(REPLY_API % uid, uid)
            r_list = get_list(rdata)
            if r_list:
                r0 = r_list[0]
                name = r0.get("reply_user_nickname") or r0.get("reply_user_name") or ""
        except Exception:
            pass
    return {"uid": uid, "name": name or ("股友" + uid[-4:])}


# ---------- 推特(X) ----------
def _twitter_exe():
    exe = shutil.which("twitter")
    if exe:
        return exe
    cand = os.path.expanduser(r"~\.local\bin\twitter.exe")
    return cand if os.path.exists(cand) else "twitter"


def _no_window_kwargs():
    """Windows 下隐藏子进程的控制台窗口，避免每次调用闪黑框。"""
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {"startupinfo": si,
            "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}


def _norm_time(s):
    """把 '2026-06-13 10:29' 补成 '2026-06-13 10:29:00'，便于和股吧时间一起排序。"""
    s = (s or "").strip().replace("T", " ")
    if len(s) == 16 and s[4] == "-":
        return s + ":00"
    return s[:19] if len(s) >= 19 else s


def parse_tweets(handle):
    """调用 twitter-cli 抓某用户最近推文，归一化成统一格式。"""
    handle = (handle or "").lstrip("@")
    if not handle:
        return []
    proc = subprocess.run(
        [_twitter_exe(), "user-posts", "@" + handle, "-n", "40", "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=90,
        **_no_window_kwargs())
    if not proc.stdout:
        raise RuntimeError((proc.stderr or "twitter-cli 无输出")[:200])
    data = json.loads(proc.stdout)
    if not data.get("ok", True):
        raise RuntimeError(str(data.get("error") or data)[:200])
    items = []
    for t in data.get("data", []):
        tid = str(t.get("id") or "")
        if not tid:
            continue
        author = t.get("author") or {}
        sn = author.get("screenName") or handle
        is_rt = bool(t.get("isRetweet"))
        quoted = t.get("quotedTweet") or {}
        q_author = (quoted.get("author") or {}) if isinstance(quoted, dict) else {}
        rt_by = t.get("retweetedBy")
        if isinstance(rt_by, dict):
            rt_by = rt_by.get("screenName") or rt_by.get("name") or ""
        if is_rt:
            ctx_user, ctx_text = (rt_by or ""), ""
        else:
            ctx_user = q_author.get("screenName") or ""
            ctx_text = (quoted.get("text") or "").strip() if isinstance(quoted, dict) else ""
        items.append({
            "key": "T" + tid,
            "kind": "转推" if is_rt else "推文",
            "icon": "🔁" if is_rt else "🐦",
            "time": _norm_time(t.get("createdAtLocal") or t.get("createdAtISO")),
            "title": "",
            "content": (t.get("text") or "").strip(),
            "bar": "推特",
            "ctx_user": ctx_user,
            "ctx_text": ctx_text,
            "link": "https://x.com/%s/status/%s" % (sn, tid),
        })
    return items


# ---------- 微博 ----------
_WB_MON = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
           "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}


def _parse_weibo_time(s):
    """'Sun Jul 12 21:41:08 +0800 2026' -> '2026-07-12 21:41:08'"""
    try:
        p = (s or "").split()
        return "%s-%02d-%02d %s" % (p[5], _WB_MON[p[1]], int(p[2]), p[3])
    except Exception:
        return ""


def parse_weibo(uid, cookie):
    """抓某微博用户的主页动态(发帖/转发/回复)。用桌面版 ajax 接口，需登录 cookie(SUB)。"""
    uid = str(uid)
    url = "https://weibo.com/ajax/statuses/mymblog?uid=%s&page=1&feature=0" % uid
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Cookie": cookie,
        "Referer": "https://weibo.com/u/%s" % uid,
        "Accept": "application/json, text/plain, */*",
        "x-requested-with": "XMLHttpRequest",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8", "ignore"))
    if data.get("ok") != 1:
        raise RuntimeError("微博接口 ok=%s（cookie 可能失效，需重新导 SUB）" % data.get("ok"))
    items = []
    for mb in (data.get("data") or {}).get("list", []):
        mid = str(mb.get("idstr") or mb.get("id") or "")
        if not mid:
            continue
        rt = mb.get("retweeted_status")
        text = (mb.get("text_raw") or "").strip()
        if mb.get("isLongText"):
            text += " …[长文]"
        if rt:
            kind, icon = "转发", "🔁"
            ctx_user = (rt.get("user") or {}).get("screen_name") or ""
            ctx_text = (rt.get("text_raw") or "").strip()
        elif text.startswith("回复@"):
            kind, icon = "评论", "💬"
            ctx_user, ctx_text = "", ""
        else:
            kind, icon = "发帖", "📝"
            ctx_user, ctx_text = "", ""
        items.append({
            "key": "W" + mid,
            "kind": kind,
            "icon": icon,
            "time": _parse_weibo_time(mb.get("created_at")),
            "title": "",
            "content": text,
            "bar": "微博",
            "ctx_user": ctx_user,
            "ctx_text": ctx_text,
            "link": "https://weibo.com/%s/%s" % (uid, mb.get("mblogid") or mid),
        })
    return items


# ---------- 帖子追加内容 ----------
NEWS_LINK_RE = re.compile(r"eastmoney\.com/news,([^,]+),(\d+)\.html")


def parse_news_link(link):
    """从帖子链接反解出 (股吧代码, post_id)，查追加/取全文要按 post_id 单独请求，链接拼回去也要 code。"""
    m = NEWS_LINK_RE.search(link or "")
    if not m:
        return None, None
    return m.group(1), m.group(2)


def fetch_article(post_id):
    """取一条帖子的完整数据(post 字典)。被拦成验证页、rc != 1、没有 post 都抛异常——
    调用方别把这些情况误当成"帖子没内容/没追加"。"""
    req = urllib.request.Request(ARTICLE_API % post_id, headers={
        "User-Agent": UA,
        "Referer": "https://guba.eastmoney.com/",
        "Accept": "application/json, text/plain, */*",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", "ignore")
    if "fd_guba_validate" in body or "em_capt.js" in body:
        raise RuntimeError("触发东财反爬验证，本次跳过")
    try:
        data = json.loads(body)
    except Exception:
        raise RuntimeError("接口返回的不是 JSON：%s" % body[:60])
    post = data.get("post") if isinstance(data, dict) else None
    if not isinstance(post, dict) or data.get("rc") != 1:
        raise RuntimeError("接口返回异常：%s" % str(data.get("me") if isinstance(data, dict) else "")[:60])
    return post


def html_to_text(s):
    """帖子全文/追加是富文本 HTML：段落和换行转成 \n，其余标签剥掉，实体反转义。"""
    s = re.sub(r"(?i)<br\s*/?>", "\n", s or "")
    s = re.sub(r"(?i)</(p|div|li|h[1-6])>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_mod.unescape(s).replace("\xa0", " ")
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def is_truncated(content):
    """列表接口把超过 200 字的正文截成前 200 字 + "..."。作者自己写的省略号一般不会正好卡在这个长度上。"""
    c = (content or "").rstrip()
    return len(c) >= 195 and c.endswith(("...", "…"))


def fetch_full_content(post_id):
    return html_to_text(fetch_article(post_id).get("post_content") or "")


def complete_truncated(items, seen_keys, on_error=None):
    """把列表接口截成摘要的帖子换成全文，就地改 item["content"]。
    只处理没见过的帖子（避免每轮轮询都去请求全文），取不到就保留摘要，不影响这条动态本身的入列/通知。"""
    for it in items:
        if it["kind"] not in ("发帖", "转发") or it["key"] in seen_keys or not is_truncated(it["content"]):
            continue
        try:
            full = fetch_full_content(it["key"][1:])
        except Exception as e:
            if on_error:
                on_error(it, e)
            continue
        if full:
            it["content"] = full
        time.sleep(random.uniform(1, 2))


def parse_post_appends(code, post_id):
    """取作者「追加」的内容(post_add_list)。这部分内容不在 userdynamiclistv2 的列表接口里，
    只有 ArticleContent 接口（以前是帖子详情页的内嵌 JSON）才有。code 只用来拼链接。"""
    link = make_link(code, post_id)
    items = []
    for a in (fetch_article(post_id).get("post_add_list") or []):
        add_id = str(a.get("add_id") or "")
        if not add_id:
            continue
        items.append({
            "key": "A" + add_id,
            "kind": "追加",
            "icon": "📌",
            "time": (a.get("add_time") or "")[:19],
            "title": "",
            "content": html_to_text(a.get("add_text")),
            "bar": "",
            "ctx_user": "",
            "ctx_text": "",
            "link": link,
        })
    return items


# ---------- 推送 ----------
def push_serverchan(key, title, desp):
    url = "https://sctapi.ftqq.com/%s.send" % key
    data = urllib.parse.urlencode({"title": title, "desp": desp}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", "ignore")


def push_pushplus(token, title, content):
    url = "https://www.pushplus.plus/send"
    body = json.dumps({"token": token, "title": title,
                       "content": content, "template": "txt"}).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", "ignore")


def send_push(cfg, title, content):
    push = cfg.get("push") or {}
    ptype = push.get("type", "serverchan")
    key = push.get("key", "")
    if not key or key.startswith("在这里填"):
        log("⚠ 未配置推送 key，仅打印不推送：\n%s\n%s" % (title, content))
        return
    try:
        if ptype == "pushplus":
            push_pushplus(key, title, content)
        else:
            push_serverchan(key, title, content)
        log("已推送: %s" % title)
    except Exception as e:
        log("推送失败: %s" % e)


def build_message(user_name, it):
    title = "%s %s [%s] %s" % (it["icon"], it["kind"], user_name,
                               (it["title"] or it["content"])[:22])
    lines = [
        "用户：%s" % user_name,
        "类型：%s" % it["kind"],
        "时间：%s" % it["time"],
        "来源：%s" % (it["bar"] or "—"),
    ]
    if it["kind"] == "评论" and (it["ctx_user"] or it["ctx_text"]):
        lines.append("评论于：%s 的帖子《%s》" % (it["ctx_user"] or "?", it["ctx_text"] or ""))
    if it.get("quote_text"):
        lines.append("回复 %s 的评论：%s" % (it.get("quote_user") or "?", it["quote_text"]))
    if it["kind"] == "转发" and (it["ctx_user"] or it["ctx_text"]):
        lines.append("转发自：%s 《%s》" % (it["ctx_user"] or "?", it["ctx_text"] or ""))
    if it["kind"] == "转推" and it["ctx_user"]:
        lines.append("转推自：@%s" % it["ctx_user"])
    if it["kind"] == "推文" and (it["ctx_user"] or it["ctx_text"]):
        lines.append("引用 @%s：%s" % (it["ctx_user"] or "?", it["ctx_text"] or ""))
    if it["title"]:
        lines.append("标题：%s" % it["title"])
    lines.append("")
    lines.append(it["content"] or "(无正文)")
    lines.append("")
    lines.append("原文链接：%s" % it["link"])
    return title, "\n\n".join(lines)


# ---------- 主循环 ----------
def collect_items(cfg, uid):
    """按配置抓取发帖和/或评论，合并为统一列表。"""
    items = []
    if cfg.get("monitor_posts", True):
        try:
            items += parse_posts(uid)
        except Exception as e:
            log("抓发帖失败 uid=%s: %s" % (uid, e))
        time.sleep(random.uniform(1, 2))
    if cfg.get("monitor_replies", True):
        try:
            items += parse_replies(uid)
        except Exception as e:
            log("抓评论失败 uid=%s: %s" % (uid, e))
    return items


def check_user(cfg, state, user):
    uid = str(user["uid"])
    name = user.get("name") or uid
    items = collect_items(cfg, uid)
    if not items:
        log("用户 %s 没抓到内容（可能被限流或 uid 有误）。" % name)
        return

    seen = set(state.get(uid, []))
    first_time = uid not in state

    if first_time:
        state[uid] = [it["key"] for it in items]
        log("首次监控 %s：记录 %d 条现有内容作为基线（不推送）。" % (name, len(items)))
        return

    new_items = [it for it in items if it["key"] not in seen]
    # 按时间排序，旧的先推
    new_items.sort(key=lambda x: x["time"])
    complete_truncated(new_items, set(), on_error=lambda it, e: log("取全文失败 %s: %s" % (it["key"], e)))
    for it in new_items:
        title, content = build_message(name, it)
        send_push(cfg, title, content)
        seen.add(it["key"])

    if new_items:
        log("用户 %s 发现 %d 条新动态（发帖/评论）。" % (name, len(new_items)))

    merged = [it["key"] for it in items] + list(seen)
    state[uid] = list(dict.fromkeys(merged))[:500]


def main():
    try:
        cfg = load_config()
    except FileNotFoundError as e:
        log(str(e))
        sys.exit(1)
    users = cfg.get("users", [])
    interval = int(cfg.get("poll_interval_seconds", 60))
    if not users:
        log("config.json 里还没有配置要监控的用户。")
        sys.exit(1)

    what = []
    if cfg.get("monitor_posts", True):
        what.append("发帖")
    if cfg.get("monitor_replies", True):
        what.append("评论")
    log("启动监控：%d 个用户，监控[%s]，每 %d 秒一轮。"
        % (len(users), "+".join(what), interval))

    state = load_state()
    while True:
        for u in users:
            check_user(cfg, state, u)
            save_state(state)
            time.sleep(random.uniform(2, 5))
        time.sleep(interval + random.uniform(0, 10))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("已手动停止。")
