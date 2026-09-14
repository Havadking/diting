# -*- coding: utf-8 -*-
"""
谛听 · 监控核心（无任何 UI 依赖）

从 app.py 的 MonitorApp 抽出来的后台抓取线程 + 数据模型：
  - 轮询股吧 / 推特 / 微博，按来源做首轮基线与去重
  - 帖子追加监视（含失败退避）
  - 静音 / 合并阈值 / Windows 通知
  - 把处理好的展示条目写进 messages.db，并广播给所有订阅者

tkinter 版 (app.py) 和网页版 (server.py) 都只是它的一层壳：订阅一个事件队列，
拿到 ("history"|"new"|"status"|"cleared", payload) 后自己决定怎么渲染。
本文件里**不能** import tkinter。
"""
import queue
import random
import threading
import time
from datetime import datetime

import monitor

try:
    from winotify import Notification, audio
    HAS_TOAST = True
except Exception:
    HAS_TOAST = False

APP_ID = "谛听"
MAX_ROWS = 1000
MERGE_LIMIT = 8  # 一轮内同一用户新增超过这么多条才合并通知，否则逐条弹
SUB_QUEUE_SIZE = 200  # 订阅者队列上限，满了丢最旧的，别让挂死的消费者拖住后台线程

# 推特/微博监控功能暂时下线（不抓取、UI 也不显示相关内容），代码保留，改回 True 即可恢复
ENABLE_TWITTER = False
ENABLE_WEIBO = False

# 按「类型」区分的浅色行底色（与文字色同色系但很淡，用户自定义配色只管文字，不影响这层）
KIND_BG = {
    "发帖": "#e4f5ec",
    "评论": "#e9eefb",
    "转发": "#fdf1e2",
    "转推": "#fdf1e2",
    "推文": "#f3ecfb",
    "追加": "#f6ece0",
}

# 自定义可选颜色：取自「中国传统色」，色相分明且白底上当文字清晰可读
PALETTE = [
    ("朱红", "#ED5126"), ("橘橙", "#F97D1C"), ("土黄", "#D6A01D"),
    ("竹绿", "#1BA784"), ("翠蓝", "#1E9EB3"), ("群青", "#1772B4"),
    ("青莲", "#8B2671"), ("品红", "#EF3473"),
]


def toast(title, msg, link=None):
    if not HAS_TOAST:
        return
    try:
        n = Notification(app_id=APP_ID, title=title, msg=msg, launch=link or "")
        n.set_audio(audio.Default, loop=False)
        n.show()
    except Exception:
        pass


class MonitorCore:
    def __init__(self):
        self.worker = None
        self.stop_event = threading.Event()
        self.running = False
        self.first_cycle = True   # 仅用于「启动后立刻抓一次推特」的时机控制
        self._seeded = set()      # 已完成首轮基线的来源(skey)，按来源分别 seed
        self.items = []           # 数据模型：所有动态(展示字段，同 messages.db 表结构)
        self.item_keys = set()    # 去重
        self.lock = threading.Lock()  # 保护 items / item_keys
        self.color_map = {}       # 用户名 -> 颜色
        self.muted = set()        # 被静音(只收不提示)的用户名
        self.status_text = "未启动"
        self.last_check = ""      # 上次轮询结束时刻 HH:MM:SS
        self._subs = []           # 订阅者队列
        self._subs_lock = threading.Lock()
        self._last_tw = 0.0       # 上次抓推特的时间戳
        self._last_wb = 0.0       # 上次抓微博的时间戳
        self._last_append_check = 0.0  # 上次检查帖子追加的时间戳
        self._append_fail_streak = 0   # 连续失败次数，用来算退避间隔
        # 帖子追加监视表：post_id -> {"uid","code","name","expires_at"}，只在后台线程读写，
        # 不落盘——重启后自然清空，靠正常轮询重新发现"发布在24小时内"的帖子来重建，够用了。
        self._append_watch = {}
        self.refresh_config_maps()
        self._load_history_from_db()
        if not HAS_TOAST:
            self.status_text = "未安装 winotify，将只在窗口显示、不弹系统通知。"

    # ---------- 订阅 / 广播 ----------
    def subscribe(self):
        q = queue.Queue(maxsize=SUB_QUEUE_SIZE)
        with self._subs_lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._subs_lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

    def _broadcast(self, etype, payload):
        with self._subs_lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait((etype, payload))
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait((etype, payload))
                except queue.Full:
                    pass

    def _status_payload(self):
        return {"text": self.status_text, "running": self.running, "last_check": self.last_check}

    def set_status(self, text):
        self.status_text = text
        self._broadcast("status", self._status_payload())

    def snapshot(self):
        with self.lock:
            items = list(self.items)
        return {"items": items, "status": self._status_payload()}

    # ---------- 历史 ----------
    def _load_history_from_db(self):
        """启动时先把 SQLite 里存过的消息直接铺进列表(不弹通知)，再由后台线程去拉新的。
        用独立的短连接，读完即关——后台线程会有自己的写连接。"""
        try:
            db = monitor.get_db()
        except Exception:
            self.status_text = "消息持久化数据库打开失败，本次运行的消息不会被保存。"
            return
        try:
            rows = monitor.load_recent_messages(db, MAX_ROWS)
        except Exception:
            rows = []
        finally:
            db.close()
        with self.lock:
            for r in rows:
                if r["key"] in self.item_keys:
                    continue
                self.item_keys.add(r["key"])
                self.items.append(r)

    # ---------- 控制 ----------
    def start(self):
        """启动后台抓取。返回 None 表示成功，否则返回给用户看的错误文案（由壳层决定怎么提示）。"""
        try:
            cfg = monitor.load_config()
            has_tw = ENABLE_TWITTER and cfg.get("twitter_users")
            has_wb = ENABLE_WEIBO and cfg.get("weibo_users")
            if not (cfg.get("users") or has_tw or has_wb):
                return "config.json 里还没有配置要监控的用户。"
        except Exception as e:
            return "读取 config.json 失败：\n%s" % e
        # 先确保没有旧线程还在跑（避免重复推送）
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self.worker.join(timeout=3)
        self.running = True
        self.first_cycle = True
        self._seeded = set()      # 每次启动都重新按来源做首轮基线
        self._last_tw = 0.0
        self._last_wb = 0.0
        self._last_append_check = 0.0
        self._append_fail_streak = 0
        self._append_watch = {}
        self.stop_event = threading.Event()   # 给新线程一个全新的停止信号
        my_stop = self.stop_event
        self.set_status("正在启动…首次抓取会先加载现有内容（不弹通知），过去的日期默认折叠。")
        self.worker = threading.Thread(target=self._run_loop, args=(my_stop,), daemon=True)
        self.worker.start()
        return None

    def stop(self):
        self.running = False
        self.stop_event.set()
        self.set_status("已停止。")

    def test_toast(self):
        toast("🔔 测试通知", "看到这条说明系统通知正常。", "https://guba.eastmoney.com/")
        self.set_status("已发送测试通知，看右下角。")

    def clear(self):
        with self.lock:
            self.items.clear()
            self.item_keys.clear()
        self._broadcast("cleared", {})

    def describe_config(self):
        """状态栏那句「股吧 N 人(发帖+评论) · 间隔 60s」。读配置失败抛异常，由壳层兜底。"""
        cfg = monitor.load_config()
        n = len(cfg.get("users", []))
        tw = len(cfg.get("twitter_users", []))
        wb = len(cfg.get("weibo_users", []))
        what = []
        if cfg.get("monitor_posts", True):
            what.append("发帖")
        if cfg.get("monitor_replies", True):
            what.append("评论")
        txt = "股吧 %d 人(%s) · 间隔 %ds" % (n, "+".join(what),
                                          cfg.get("poll_interval_seconds", 60))
        if ENABLE_TWITTER and tw:
            txt += "   推特 %d 人 · %ds" % (tw, cfg.get("twitter_poll_interval_seconds", 180))
        if ENABLE_WEIBO and wb:
            txt += "   微博 %d 人 · %ds" % (wb, cfg.get("weibo_poll_interval_seconds", 120))
        return txt

    # ---------- 配置映射 ----------
    def refresh_config_maps(self):
        """从配置生成 用户名->颜色 及 静音用户集合。"""
        m = {}
        muted = set()
        try:
            cfg = monitor.load_config()
        except Exception:
            cfg = {}
        groups = cfg.get("groups", {}) or {}
        for u in ((cfg.get("users", []) or []) + (cfg.get("twitter_users", []) or [])
                  + (cfg.get("weibo_users", []) or [])):
            name = u.get("name") or u.get("uid") or u.get("handle")
            if not name:
                continue
            c = u.get("color") or (groups.get(u.get("group")) if u.get("group") else None)
            if c:
                m[name] = c
            if u.get("mute"):
                muted.add(name)
        self.color_map = m
        self.muted = muted

    # ---------- 后台线程 ----------
    def _emit(self, state, skey, name, items, db):
        """对一个来源的抓取结果做去重。该来源**第一次抓成功**时入历史(不提示)，
        之后才弹新动态。按来源分别处理，避免某来源开机时抓取失败就永远不显示。"""
        seen = set(state.get(skey, []))
        new_items = [it for it in items if it["key"] not in seen]
        state[skey] = list(dict.fromkeys([it["key"] for it in items] + list(seen)))[:500]
        if skey not in self._seeded:
            self._seeded.add(skey)
            entries = [self._add_item(name, it, db)
                       for it in sorted(items, key=lambda x: x["time"])[-10:]]
            entries = [e for e in entries if e]
            if entries:
                self._broadcast("history", entries)
        elif new_items:
            new_items.sort(key=lambda x: x["time"])
            self._handle_new(name, new_items, db)

    def _run_loop(self, stop_event):
        state = monitor.load_state()
        db = None
        try:
            db = monitor.get_db()
        except Exception:
            self.set_status("消息持久化数据库打开失败，本次运行的消息不会被保存。")
        try:
            while not stop_event.is_set():
                try:
                    cfg = monitor.load_config()
                except Exception as e:
                    self.set_status("读取配置失败：%s" % e)
                    stop_event.wait(5)
                    continue
                interval = int(cfg.get("poll_interval_seconds", 60))

                # —— 股吧用户 ——
                for u in cfg.get("users", []):
                    if stop_event.is_set():
                        break
                    uid = str(u["uid"])
                    name = u.get("name") or uid
                    try:
                        items = monitor.collect_items(cfg, uid)
                    except Exception as e:
                        self.set_status("抓取 %s 失败：%s" % (name, e))
                        continue
                    if items:
                        self._emit(state, uid, name, items, db)
                        if u.get("check_appends"):
                            self._register_append_watch(uid, name, items)
                    stop_event.wait(random.uniform(2, 4))

                # —— 推特用户（单独的慢节奏，降低风控/封号风险）——
                tw_users = cfg.get("twitter_users", []) if ENABLE_TWITTER else []
                tw_interval = int(cfg.get("twitter_poll_interval_seconds", 180))
                if tw_users and (self.first_cycle or time.time() - self._last_tw >= tw_interval):
                    self._last_tw = time.time()
                    for tu in tw_users:
                        if stop_event.is_set():
                            break
                        handle = str(tu.get("handle") or tu.get("uid") or "").lstrip("@")
                        if not handle:
                            continue
                        name = tu.get("name") or ("@" + handle)
                        try:
                            items = monitor.parse_tweets(handle)
                        except Exception as e:
                            self.set_status("抓推特 %s 失败：%s" % (name, str(e)[:80]))
                            continue
                        if items:
                            self._emit(state, "tw:" + handle, name, items, db)
                        stop_event.wait(random.uniform(2, 4))

                # —— 微博用户（需登录 cookie；单独节奏）——
                wb_users = cfg.get("weibo_users", []) if ENABLE_WEIBO else []
                wb_cookie = cfg.get("weibo_cookie", "")
                wb_interval = int(cfg.get("weibo_poll_interval_seconds", 120))
                if wb_users and wb_cookie and (self.first_cycle or time.time() - self._last_wb >= wb_interval):
                    self._last_wb = time.time()
                    for wu in wb_users:
                        if stop_event.is_set():
                            break
                        uid = str(wu.get("uid") or "")
                        if not uid:
                            continue
                        name = wu.get("name") or ("微博" + uid)
                        try:
                            items = monitor.parse_weibo(uid, wb_cookie)
                        except Exception as e:
                            self.set_status("抓微博 %s 失败：%s" % (name, str(e)[:80]))
                            continue
                        if items:
                            self._emit(state, "wb:" + uid, name, items, db)
                        stop_event.wait(random.uniform(2, 4))

                # —— 帖子追加检查（只查开了 check_appends 的用户；单独节奏，失败会自动退避）——
                append_base = int(cfg.get("append_check_interval_seconds", 300))
                append_interval = self._append_backoff_interval(append_base)
                if self._append_watch and (self.first_cycle
                                            or time.time() - self._last_append_check >= append_interval):
                    self._last_append_check = time.time()
                    self._check_append_watch(state, stop_event, db)

                monitor.save_state(state)
                self.first_cycle = False
                self.last_check = datetime.now().strftime("%H:%M:%S")
                self.set_status("上次检查 %s · 运行中" % self.last_check)
                waited = 0.0
                while waited < interval and not stop_event.is_set():
                    stop_event.wait(1)
                    waited += 1
        finally:
            if db:
                try:
                    db.close()
                except Exception:
                    pass

    # —— 帖子追加监视（只在后台线程读写 self._append_watch，不用加锁）——
    def _register_append_watch(self, uid, name, items):
        """把这一轮抓到的、发布在 24 小时内的帖子登记进监视表，之后定期查它有没有追加。"""
        now = time.time()
        for it in items:
            if it["kind"] not in ("发帖", "转发"):
                continue
            code, post_id = monitor.parse_news_link(it["link"])
            if not post_id or post_id in self._append_watch:
                continue
            try:
                published = datetime.strptime(it["time"], "%Y-%m-%d %H:%M:%S").timestamp()
            except Exception:
                continue
            if now - published > 86400:
                continue
            self._append_watch[post_id] = {
                "uid": uid, "code": code, "name": name,
                "expires_at": published + 86400,
            }

    def _append_backoff_interval(self, base):
        """连续失败越多次，下次检查间隔翻倍拉长（封顶 2 小时），避免在反爬验证生效期间
        还一直按原节奏反复触发；只要有一轮成功就重置回正常间隔。"""
        return min(base * (2 ** self._append_fail_streak), 7200)

    def _check_append_watch(self, state, stop_event, db):
        """查监视表里还没过期的帖子有没有新追加；查到就顺延 24 小时，查不到就让它自然过期。"""
        now = time.time()
        expired = [pid for pid, w in self._append_watch.items() if w["expires_at"] <= now]
        for pid in expired:
            del self._append_watch[pid]
        had_failure = False
        for post_id, w in list(self._append_watch.items()):
            if stop_event.is_set():
                break
            try:
                items = monitor.parse_post_appends(w["code"], post_id)
            except Exception as e:
                had_failure = True
                self.set_status("查追加 %s 失败：%s" % (w["name"], str(e)[:80]))
                continue
            if items:
                self._emit(state, "ap:" + post_id, w["name"], items, db)
                w["expires_at"] = time.time() + 86400
            stop_event.wait(random.uniform(2, 4))
        if had_failure:
            self._append_fail_streak = min(self._append_fail_streak + 1, 6)
            self.set_status("查追加连续失败，已自动放慢检查频率（退避第 %d 级）"
                            % self._append_fail_streak)
        elif self._append_fail_streak:
            self._append_fail_streak = 0

    # ---------- 新动态：入列 + 通知 ----------
    def _handle_new(self, name, items, db):
        self.refresh_config_maps()   # 取最新的静音/颜色设置
        # 只对「确实是新的」条目弹通知（按 key 去重），防止偶发重复推送
        fresh = []
        entries = []
        for it in items:
            e = self._add_item(name, it, db)
            if e:
                fresh.append(it)
                entries.append(e)
        if not fresh:
            return
        self._broadcast("new", entries)
        # 静音用户：只入列表、不弹通知
        if name in self.muted:
            self.set_status("🔕 %s 新增 %d 条（静音·仅入列）· %s"
                            % (name, len(fresh), datetime.now().strftime("%H:%M:%S")))
            return
        # 突发多条时尽量逐条弹（上限 MERGE_LIMIT 条）；超过才合并成一条，避免极端刷屏
        if len(fresh) > MERGE_LIMIT:
            toast("【%s】%d 条新动态" % (name, len(fresh)),
                  ("最新：%s" % (fresh[-1]["content"] or fresh[-1]["title"]))[:80],
                  fresh[-1]["link"])
        else:
            for it in fresh:
                head = "%s %s · %s" % (it["icon"], it["kind"], name)
                body = it["content"] or it["title"] or "(无正文)"
                if it["kind"] == "评论" and it["ctx_text"]:
                    body = "评论《%s》：%s" % (it["ctx_text"][:18], body)
                toast(head, body[:120], it["link"])
        self.set_status("%s 新增 %d 条 · %s"
                        % (name, len(fresh), datetime.now().strftime("%H:%M:%S")))

    # —— 数据模型 ——
    def _add_item(self, name, it, db):
        """把解析函数给的原始 item 处理成展示条目，入列并写库。已存在返回 None。
        列表超过 MAX_ROWS 时按时间丢最旧的（原先是渲染时裁，现在挪到入口处，壳层不用管）。"""
        with self.lock:
            if it["key"] in self.item_keys:
                return None
            self.item_keys.add(it["key"])
            raw = (it["content"] or it["title"] or "").strip()
            if it["kind"] == "评论" and it["ctx_text"]:
                content = "[评论《%s》] %s" % (it["ctx_text"][:14], raw or "(无正文)")
            elif it["kind"] in ("转发", "转推") and (it["ctx_user"] or it["ctx_text"]):
                ctx_user = it["ctx_user"] or "?"
                if it["ctx_text"]:
                    content = ("[转发自 %s《%s》] %s" % (ctx_user, it["ctx_text"][:14], raw)).rstrip()
                else:
                    content = ("[转推自 %s] %s" % (ctx_user, raw)).rstrip()
            else:
                content = raw or "(无正文)"
            content = content.replace("\n", " ").replace("\r", " ").strip()
            entry = {
                "key": it["key"], "name": name, "kind": it["kind"],
                "icon": it.get("icon") or "",
                "time": it["time"] or "", "bar": it["bar"] or "—",
                "content": content, "link": it["link"],
            }
            self.items.append(entry)
            if len(self.items) > MAX_ROWS:
                self.items.sort(key=lambda x: x["time"])
                drop = self.items[:len(self.items) - MAX_ROWS]
                self.item_keys.difference_update(d["key"] for d in drop)
                self.items = self.items[len(self.items) - MAX_ROWS:]
        if db:
            try:
                monitor.save_message(db, entry)
            except Exception:
                pass
        return entry
