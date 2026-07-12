# -*- coding: utf-8 -*-
"""
东方财富股吧 + 推特(X) - 指定用户动态实时监控

数据源:
  股吧发帖/转发: https://i.eastmoney.com/api/guba/userdynamiclistv2 (type=1)
  股吧评论/回复: https://i.eastmoney.com/api/guba/myreply
  推特发帖:      twitter-cli (twitter user-posts @handle --json)，需 X 账号 cookie
推送渠道: Server酱(serverchan) 或 PushPlus(pushplus)；桌面版用 Windows 通知
"""
import json
import os
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

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 发帖/文章/转发：用「全部动态」接口(type=1)，能拿到股吧短帖（fullarticlelist 只有财富号文章，会漏帖）
POST_API = "https://i.eastmoney.com/api/guba/userdynamiclistv2?uid=%s&pagenum=1&pagesize=20&type=1"
REPLY_API = "https://i.eastmoney.com/api/guba/myreply?uid=%s&pageindex=1"


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
    if not os.path.exists(CONFIG_PATH):
        log("找不到 config.json，请先按 README 填写配置。")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


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
        # 评论可能是回复别人的评论
        to_user = r.get("source_reply_user_nickname") or r.get("source_post_user_nickname") or ""
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
        })
    return items


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
    for it in new_items:
        title, content = build_message(name, it)
        send_push(cfg, title, content)
        seen.add(it["key"])

    if new_items:
        log("用户 %s 发现 %d 条新动态（发帖/评论）。" % (name, len(new_items)))

    merged = [it["key"] for it in items] + list(seen)
    state[uid] = list(dict.fromkeys(merged))[:500]


def main():
    cfg = load_config()
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
