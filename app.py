# -*- coding: utf-8 -*-
"""
东方财富股吧用户监控 - 桌面版 (GUI + Windows 系统通知)

  - 后台定时抓取被监控用户的「发帖 + 评论」
  - 有新动态时弹 Windows 通知，并按时间顺序追加到窗口列表（最新在最下面）
  - 双击列表行用浏览器打开原文
不依赖任何第三方推送服务，无额度限制。
"""
import os
import json
import queue
import random
import threading
import time
import traceback
import webbrowser
from collections import defaultdict
from datetime import datetime

import tkinter as tk
from tkinter import ttk, font as tkfont, messagebox, colorchooser

import monitor  # 复用已写好的抓取/解析逻辑

try:
    from winotify import Notification, audio
    HAS_TOAST = True
except Exception:
    HAS_TOAST = False

try:
    import sv_ttk  # 可选：Windows 11 风格现代主题，没装则自动退回旧的手工配色
    HAS_THEME = True
except Exception:
    HAS_THEME = False

APP_ID = "东方财富股吧监控"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ERR_LOG = os.path.join(BASE_DIR, "gui_error.log")
MAX_ROWS = 1000
MERGE_LIMIT = 8  # 一轮内同一用户新增超过这么多条才合并通知，否则逐条弹

# 推特/微博监控功能暂时下线（不抓取、UI 也不显示相关内容），代码保留，改回 True 即可恢复
ENABLE_TWITTER = False
ENABLE_WEIBO = False

# ---- 配色（Fluent 风格浅色：中性灰底 + 白色内容卡片 + 蓝色强调）----
C_PAGE = "#f3f3f3"    # 窗口/工具栏/状态栏 底色
C_BG = "#ffffff"      # 列表、对话框等内容卡片底色
C_HEAD_BG = "#f6f6f7"
C_HEAD_FG = "#1c1c1c"
C_SEL = "#dbeafe"
C_TOOLBAR = C_PAGE
C_POST = "#0a8f5b"    # 发帖 绿
C_REPLY = "#1d4ed8"   # 评论 蓝
C_REPOST = "#c2620a"  # 转发 橙
C_TWEET = "#7c3aed"   # 推文 紫
C_APPEND = "#b3550f"  # 追加 棕
C_HIST = "#566072"    # 历史 深灰（可清晰阅读）

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


class MonitorApp:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.worker = None
        self.stop_event = threading.Event()
        self.running = False
        self.first_cycle = True   # 仅用于「启动后立刻抓一次推特」的时机控制
        self._seeded = set()      # 已完成首轮基线的来源(skey)，按来源分别 seed
        self.items = []           # 数据模型：所有动态(dict)
        self.item_keys = set()    # 去重
        self.row_link = {}        # iid -> 链接（每次重建）
        self.header_date = {}     # 日期表头 iid -> 日期
        self.user_collapsed = {}  # 日期 -> 是否折叠（用户手动覆盖）
        self._color_tags = set()  # 已创建的颜色 tag
        self._color_map = {}      # 用户名 -> 颜色
        self._muted = set()       # 被静音(只收不提示)的用户名
        self._dirty = False
        self._last_tw = 0.0       # 上次抓推特的时间戳
        self._last_wb = 0.0       # 上次抓微博的时间戳
        self._last_append_check = 0.0  # 上次检查帖子追加的时间戳
        self._append_fail_streak = 0   # 连续失败次数，用来算退避间隔
        # 帖子追加监视表：post_id -> {"uid","code","name","expires_at"}，只在后台线程读写，
        # 不落盘——重启后自然清空，靠正常轮询重新发现"发布在24小时内"的帖子来重建，够用了。
        self._append_watch = {}

        root.title("东方财富股吧监控 · 桌面版")
        root.geometry("1180x700")
        root.configure(bg=C_PAGE)
        self._setup_style()
        self._build_ui()
        self._db = self._open_db()
        self._load_history_from_db()
        self._poll_queue()
        # 打开即自动开始监控（省去手动点「开始监控」；可随时点「停止监控」暂停）
        self.root.after(800, lambda: self.start(silent=True))

    def _open_db(self):
        try:
            return monitor.get_db()
        except Exception:
            self.set_status("消息持久化数据库打开失败，本次运行的消息不会被保存。")
            return None

    def _load_history_from_db(self):
        """启动时先把 SQLite 里存过的消息直接铺进列表(不弹通知)，再由后台线程去拉新的。"""
        if not self._db:
            return
        try:
            rows = monitor.load_recent_messages(self._db, MAX_ROWS)
        except Exception:
            return
        for r in rows:
            if r["key"] in self.item_keys:
                continue
            self.item_keys.add(r["key"])
            self.items.append(r)
        if rows:
            self._rebuild()

    # ---------- 样式 ----------
    def _setup_style(self):
        fam = "Microsoft YaHei UI"
        # 确保字体存在，否则退回默认
        if fam not in tkfont.families():
            fam = "Microsoft YaHei" if "Microsoft YaHei" in tkfont.families() else "Segoe UI"
        self.fam = fam
        self.f_base = tkfont.Font(family=fam, size=12)
        self.f_bold = tkfont.Font(family=fam, size=12, weight="bold")
        self.f_title = tkfont.Font(family=fam, size=16, weight="bold")

        if HAS_THEME:
            sv_ttk.set_theme("light")   # Windows 11 Fluent 风格：圆角按钮/卡片式列表/现代滚动条
        st = ttk.Style()
        if not HAS_THEME:
            try:
                st.theme_use("clam")
            except Exception:
                pass

        if HAS_THEME:
            # sv_ttk 已经把 Treeview 画成白色卡片、按钮画成圆角，这里只调字体/行高，
            # 不再覆盖 background/relief，否则会把它的圆角边框图片盖掉。
            st.configure("Treeview", font=self.f_base, rowheight=42)
            st.configure("Tool.TButton", font=self.f_base, padding=(14, 7))
            st.configure("Accent.TButton", font=self.f_bold, padding=(16, 7))
        else:
            st.configure("Treeview",
                         font=self.f_base, rowheight=42,
                         background=C_BG, fieldbackground=C_BG, foreground="#1c2330",
                         borderwidth=0, relief="flat")
            # 现代扁平按钮（无主题库时的手工退路）
            st.configure("Tool.TButton", font=self.f_base, relief="flat",
                         padding=(14, 7), background="#ffffff", borderwidth=1)
            st.map("Tool.TButton",
                   background=[("active", "#e8edf7"), ("pressed", "#dbe4f5")])
            st.configure("Accent.TButton", font=self.f_bold, relief="flat",
                         padding=(16, 7), background="#2563eb", foreground="#ffffff",
                         borderwidth=0)
            st.map("Accent.TButton",
                   background=[("active", "#1d4fd0"), ("pressed", "#1a44b8")])
        st.map("Treeview",
               background=[("selected", C_SEL)],
               foreground=[("selected", "#111")])
        st.configure("Treeview.Heading",
                     font=self.f_bold, relief="flat",
                     background=C_HEAD_BG, foreground=C_HEAD_FG, padding=(8, 6))
        st.map("Treeview.Heading", background=[("active", "#eaeaeb")])

    # ---------- 界面 ----------
    def _build_ui(self):
        # 顶部工具栏
        top = tk.Frame(self.root, bg=C_TOOLBAR)
        top.pack(fill="x")
        inner = tk.Frame(top, bg=C_TOOLBAR)
        inner.pack(fill="x", padx=12, pady=10)

        tk.Label(inner, text="股吧监控", font=self.f_title,
                 bg=C_TOOLBAR, fg="#1c1c1c").pack(side="left", padx=(0, 14))

        self.btn_start = ttk.Button(inner, text="开始监控", style="Accent.TButton",
                                    command=self.toggle)
        self.btn_start.pack(side="left")
        ttk.Button(inner, text="测试通知", style="Tool.TButton",
                   command=self.test_toast).pack(side="left", padx=(8, 0))
        ttk.Button(inner, text="用户设置", style="Tool.TButton",
                   command=self.open_colors).pack(side="left", padx=(8, 0))
        ttk.Button(inner, text="打开配置", style="Tool.TButton",
                   command=self.open_config).pack(side="left", padx=(8, 0))
        ttk.Button(inner, text="清空列表", style="Tool.TButton",
                   command=self.clear_list).pack(side="left", padx=(8, 0))

        self.lbl_users = tk.Label(inner, text="", font=self.f_base,
                                  bg=C_TOOLBAR, fg="#5a6478")
        self.lbl_users.pack(side="right")

        tk.Frame(self.root, bg="#e3e3e3", height=1).pack(fill="x")

        # 列表
        mid = tk.Frame(self.root, bg=C_PAGE)
        mid.pack(fill="both", expand=True, padx=12, pady=(8, 0))

        cols = ("time", "user", "kind", "bar", "content")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        layout = [("time", "时间 / 日期", 138, "w"), ("user", "用户", 130, "w"),
                  ("kind", "类型", 84, "w"), ("bar", "来源", 140, "w"),
                  ("content", "内容（双击打开原文）", 520, "w")]
        for c, txt, w, anc in layout:
            self.tree.heading(c, text=txt, anchor="w")
            self.tree.column(c, width=w, anchor=anc, stretch=(c == "content"))

        for kind, bg in KIND_BG.items():
            self.tree.tag_configure("bg_" + kind, background=bg)
        self.tree.tag_configure("post", foreground=C_POST)
        self.tree.tag_configure("reply", foreground=C_REPLY)
        self.tree.tag_configure("repost", foreground=C_REPOST)
        self.tree.tag_configure("tweet", foreground=C_TWEET)
        self.tree.tag_configure("append", foreground=C_APPEND)
        self.tree.tag_configure("hist", foreground=C_HIST)
        # 日期分组表头样式
        self.tree.tag_configure("datehdr", background="#f0f1f3",
                                foreground="#3c4147", font=self.f_bold)
        self.tree.tag_configure("datehdr_today", background="#e4edfb",
                                foreground="#0b57a4", font=self.f_bold)

        vsb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.open_selected)
        self.tree.bind("<Button-1>", self._on_header_click, add="+")

        # 状态栏
        bar = tk.Frame(self.root, bg=C_TOOLBAR)
        bar.pack(fill="x", side="bottom")
        self.status = tk.Label(bar, text="未启动", font=self.f_base, bg=C_TOOLBAR,
                               fg="#5a6478", anchor="w", padx=12, pady=5)
        self.status.pack(fill="x")

        if not HAS_TOAST:
            self.set_status("未安装 winotify，将只在窗口显示、不弹系统通知。")
        self._refresh_user_label()

    def _refresh_user_label(self):
        try:
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
            self.lbl_users.config(text=txt)
        except Exception:
            self.lbl_users.config(text="（config.json 读取失败）")

    # ---------- 控制 ----------
    def toggle(self):
        self.stop() if self.running else self.start()

    def start(self, silent=False):
        try:
            cfg = monitor.load_config()
            has_tw = ENABLE_TWITTER and cfg.get("twitter_users")
            has_wb = ENABLE_WEIBO and cfg.get("weibo_users")
            if not (cfg.get("users") or has_tw or has_wb):
                if not silent:
                    messagebox.showwarning("提示", "config.json 里还没有配置要监控的用户。")
                return
        except Exception as e:
            if not silent:
                messagebox.showerror("配置错误", "读取 config.json 失败：\n%s" % e)
            return
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
        self.btn_start.config(text="停止监控")
        self._refresh_user_label()
        self.set_status("正在启动…首次抓取会先加载现有内容（不弹通知），过去的日期默认折叠。")
        self.worker = threading.Thread(target=self._run_loop, args=(my_stop,), daemon=True)
        self.worker.start()

    def stop(self):
        self.running = False
        self.stop_event.set()
        self.btn_start.config(text="开始监控")
        self.set_status("已停止。")

    def test_toast(self):
        toast("🔔 测试通知", "看到这条说明系统通知正常。", "https://guba.eastmoney.com/")
        self.set_status("已发送测试通知，看右下角。")

    def open_config(self):
        try:
            os.startfile(monitor.CONFIG_PATH)
        except Exception:
            messagebox.showinfo("配置文件路径", monitor.CONFIG_PATH)

    def clear_list(self):
        self.tree.delete(*self.tree.get_children())
        self.items.clear()
        self.item_keys.clear()
        self.row_link.clear()
        self.header_date.clear()

    def open_selected(self, _e=None):
        sel = self.tree.selection()
        if sel and self.row_link.get(sel[0]):
            webbrowser.open(self.row_link[sel[0]])

    # ---------- 后台线程 ----------
    def _emit(self, state, skey, name, items):
        """对一个来源的抓取结果做去重。该来源**第一次抓成功**时入历史(不提示)，
        之后才弹新动态。按来源分别处理，避免某来源开机时抓取失败就永远不显示。"""
        seen = set(state.get(skey, []))
        new_items = [it for it in items if it["key"] not in seen]
        state[skey] = list(dict.fromkeys([it["key"] for it in items] + list(seen)))[:500]
        if skey not in self._seeded:
            self._seeded.add(skey)
            self.q.put(("history", name, sorted(items, key=lambda x: x["time"])[-10:]))
        elif new_items:
            new_items.sort(key=lambda x: x["time"])
            self.q.put(("new", name, new_items))

    def _run_loop(self, stop_event):
        state = monitor.load_state()
        while not stop_event.is_set():
            try:
                cfg = monitor.load_config()
            except Exception as e:
                self.q.put(("status", "读取配置失败：%s" % e))
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
                    self.q.put(("status", "抓取 %s 失败：%s" % (name, e)))
                    continue
                if items:
                    self._emit(state, uid, name, items)
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
                        self.q.put(("status", "抓推特 %s 失败：%s" % (name, str(e)[:80])))
                        continue
                    if items:
                        self._emit(state, "tw:" + handle, name, items)
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
                        self.q.put(("status", "抓微博 %s 失败：%s" % (name, str(e)[:80])))
                        continue
                    if items:
                        self._emit(state, "wb:" + uid, name, items)
                    stop_event.wait(random.uniform(2, 4))

            # —— 帖子追加检查（只查开了 check_appends 的用户；单独节奏，失败会自动退避）——
            append_base = int(cfg.get("append_check_interval_seconds", 300))
            append_interval = self._append_backoff_interval(append_base)
            if self._append_watch and (self.first_cycle
                                        or time.time() - self._last_append_check >= append_interval):
                self._last_append_check = time.time()
                self._check_append_watch(state, stop_event)

            monitor.save_state(state)
            self.first_cycle = False
            self.q.put(("status", "上次检查 %s · 运行中"
                        % datetime.now().strftime("%H:%M:%S")))
            waited = 0.0
            while waited < interval and not stop_event.is_set():
                stop_event.wait(1)
                waited += 1

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

    def _check_append_watch(self, state, stop_event):
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
                self.q.put(("status", "查追加 %s 失败：%s" % (w["name"], str(e)[:80])))
                continue
            if items:
                self._emit(state, "ap:" + post_id, w["name"], items)
                w["expires_at"] = time.time() + 86400
            stop_event.wait(random.uniform(2, 4))
        if had_failure:
            self._append_fail_streak = min(self._append_fail_streak + 1, 6)
            self.q.put(("status", "查追加连续失败，已自动放慢检查频率（退避第 %d 级）"
                        % self._append_fail_streak))
        elif self._append_fail_streak:
            self._append_fail_streak = 0

    # ---------- 主线程：消费队列 ----------
    def _poll_queue(self):
        scroll_needed = False
        try:
            while True:
                kind, *rest = self.q.get_nowait()
                if kind == "status":
                    self.set_status(rest[0])
                elif kind == "history":
                    name, items = rest
                    for it in items:
                        self._add_item(name, it)
                    self._dirty = True
                    scroll_needed = True
                elif kind == "new":
                    name, items = rest
                    self._handle_new(name, items)
                    self._dirty = True
                    scroll_needed = True
        except queue.Empty:
            pass
        if self._dirty:
            at_bottom = self._at_bottom()
            self._rebuild()
            self._dirty = False
            if scroll_needed and at_bottom:
                self.tree.yview_moveto(1.0)
        self.root.after(400, self._poll_queue)

    def _at_bottom(self):
        try:
            return self.tree.yview()[1] >= 0.985
        except Exception:
            return True

    def _handle_new(self, name, items):
        self._refresh_config_maps()   # 取最新的静音/颜色设置
        # 只对「确实是新的」条目弹通知（按 key 去重），防止偶发重复推送
        fresh = [it for it in items if it["key"] not in self.item_keys]
        for it in items:
            self._add_item(name, it)
        if not fresh:
            return
        # 静音用户：只入列表、不弹通知
        if name in self._muted:
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
    def _add_item(self, name, it):
        if it["key"] in self.item_keys:
            return
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
        if self._db:
            try:
                monitor.save_message(self._db, entry)
            except Exception:
                pass

    # —— 颜色 ——
    @staticmethod
    def _today():
        return datetime.now().strftime("%Y-%m-%d")

    def _color_tag(self, hexcolor):
        tag = "c_" + hexcolor.lstrip("#")
        if tag not in self._color_tags:
            self.tree.tag_configure(tag, foreground=hexcolor)
            self._color_tags.add(tag)
        return tag

    def _refresh_config_maps(self):
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
        self._color_map = m
        self._muted = muted

    def _resolve_fg(self, name, kind):
        c = self._color_map.get(name)
        if c:
            return self._color_tag(c)
        return {"发帖": "post", "评论": "reply", "转发": "repost",
                "推文": "tweet", "转推": "tweet", "追加": "append"}.get(kind, "")

    @staticmethod
    def _resolve_bg(kind):
        return "bg_" + kind if kind in KIND_BG else ""

    # —— 重建列表（扁平 + 日期表头 + 自定义折叠）——
    def _rebuild(self):
        self._refresh_config_maps()
        if len(self.items) > MAX_ROWS:
            self.items.sort(key=lambda x: x["time"])
            drop = self.items[:len(self.items) - MAX_ROWS]
            self.item_keys.difference_update(d["key"] for d in drop)
            self.items = self.items[len(self.items) - MAX_ROWS:]

        at_bottom = self._at_bottom()
        self.tree.delete(*self.tree.get_children())
        self.row_link.clear()
        self.header_date.clear()

        groups = defaultdict(list)
        for it in self.items:
            groups[(it["time"][:10] or "未知日期")].append(it)

        today = self._today()
        seq = 0
        for date in sorted(groups):
            rows = sorted(groups[date], key=lambda x: x["time"])
            collapsed = self.user_collapsed.get(date, date != today)
            arrow = "▶" if collapsed else "▼"
            mark = "今天 " if date == today else ""
            hid = "h_" + date.replace("-", "")
            self.tree.insert("", "end", iid=hid,
                             values=("%s %s" % (arrow, date),
                                     "%s(%d)" % (mark, len(rows)), "", "", ""),
                             tags=("datehdr_today" if date == today else "datehdr",))
            self.header_date[hid] = date
            if collapsed:
                continue
            for it in rows:
                seq += 1
                iid = "r%d" % seq
                kind_txt = ("%s %s" % (it.get("icon") or "", it["kind"])).strip()
                self.tree.insert("", "end", iid=iid,
                                 values=(it["time"][11:16], it["name"],
                                         kind_txt, it["bar"], it["content"]),
                                 tags=(self._resolve_bg(it["kind"]),
                                       self._resolve_fg(it["name"], it["kind"])))
                self.row_link[iid] = it["link"]
        if at_bottom:
            self.tree.yview_moveto(1.0)

    def _on_header_click(self, event):
        row = self.tree.identify_row(event.y)
        if row in self.header_date:
            d = self.header_date[row]
            self.user_collapsed[d] = not self.user_collapsed.get(d, d != self._today())
            self._rebuild()

    # —— 应用内 分组配色 ——
    @staticmethod
    def _hl(sw_list, hexsel):
        """高亮当前选中的色块。"""
        for hx, s in sw_list:
            sel = hexsel and hx.lower() == hexsel.lower()
            s.config(highlightbackground=("#111111" if sel else C_BG),
                     highlightcolor=("#111111" if sel else C_BG))

    def open_colors(self):
        try:
            cfg = monitor.load_config()
        except Exception as e:
            messagebox.showerror("错误", "读取配置失败：%s" % e)
            return
        win = tk.Toplevel(self.root)
        win.title("用户设置：配色 / 静音 / 查追加")
        win.configure(bg=C_BG)
        win.geometry("1000x560")
        tk.Label(win, text="点色块给用户上色（相同颜色＝同一组，「默认」按类型配色）；勾选「🔕静音」= 该用户只收进列表、不弹通知；"
                           "勾选「🔗查追加」= 该用户股吧发帖发布后 24 小时内会额外检查作者有没有追加内容，有追加就顺延 24 小时继续查。",
                 font=self.f_base, bg=C_BG, fg="#5a6478",
                 wraplength=960, justify="left").pack(padx=16, pady=(14, 4), anchor="w")
        # 图例
        legend = tk.Frame(win, bg=C_BG)
        legend.pack(fill="x", padx=16, pady=(0, 8))
        tk.Label(legend, text="可选色：", font=self.f_base, bg=C_BG, fg="#5a6478").pack(side="left")
        for nm, hx in PALETTE:
            tk.Label(legend, text=nm, font=self.f_base, bg=C_BG, fg=hx).pack(side="left", padx=4)

        body = tk.Frame(win, bg=C_BG)
        body.pack(fill="both", expand=True, padx=16)

        # 编辑对象要和 save() 里写回的对象保持一致——推特/微博下线期间不显示也不写回，
        # 否则会把这些用户已有的配色/静音设置当作"没勾选"给清空。
        editable_users = list(cfg.get("users", []))
        rows = [("股吧", u) for u in cfg.get("users", [])]
        if ENABLE_TWITTER:
            editable_users += cfg.get("twitter_users", [])
            rows += [("推特", u) for u in cfg.get("twitter_users", [])]
        if ENABLE_WEIBO:
            editable_users += cfg.get("weibo_users", [])
            rows += [("微博", u) for u in cfg.get("weibo_users", [])]
        groups = cfg.get("groups", {}) or {}
        pend = {}
        previews = {}
        swatches = {}
        mutevars = {}
        appendvars = {}
        for tagname, u in rows:
            name = u.get("name") or u.get("uid") or u.get("handle")
            cur = u.get("color") or (groups.get(u.get("group")) if u.get("group") else None)
            pend[name] = cur
            r = tk.Frame(body, bg=C_BG)
            r.pack(fill="x", pady=4)
            pv = tk.Label(r, text="[%s] %s" % (tagname, name), font=self.f_base,
                          bg=C_BG, width=16, anchor="w", fg=cur or "#1c2330")
            pv.pack(side="left")
            previews[name] = pv

            sw_list = []
            for _nm, hx in PALETTE:
                s = tk.Label(r, bg=hx, width=2, height=1, cursor="hand2",
                             highlightthickness=2, highlightbackground=C_BG, bd=0)
                s.pack(side="left", padx=1)

                def _set(e=None, nm=name, c=hx):
                    pend[nm] = c
                    previews[nm].config(fg=c)
                    self._hl(swatches[nm], c)

                s.bind("<Button-1>", _set)
                sw_list.append((hx, s))
            swatches[name] = sw_list

            def _clr(nm=name):
                pend[nm] = None
                previews[nm].config(fg="#1c2330")
                self._hl(swatches[nm], None)

            ttk.Button(r, text="默认", style="Tool.TButton",
                       command=_clr).pack(side="left", padx=(8, 0))
            mv = tk.BooleanVar(value=bool(u.get("mute")))
            mutevars[name] = mv
            tk.Checkbutton(r, text="🔕静音", variable=mv, font=self.f_base,
                           bg=C_BG, activebackground=C_BG,
                           anchor="w").pack(side="left", padx=(10, 0))
            if tagname == "股吧":  # 「查追加」是股吧帖子特有的功能，推特/微博没有
                av = tk.BooleanVar(value=bool(u.get("check_appends")))
                appendvars[name] = av
                tk.Checkbutton(r, text="🔗查追加", variable=av, font=self.f_base,
                               bg=C_BG, activebackground=C_BG,
                               anchor="w").pack(side="left", padx=(10, 0))
            self._hl(sw_list, cur)

        def save():
            for u in editable_users:
                nm = u.get("name") or u.get("uid") or u.get("handle")
                c = pend.get(nm)
                if c:
                    u["color"] = c
                else:
                    u.pop("color", None)
                if mutevars.get(nm) and mutevars[nm].get():
                    u["mute"] = True
                else:
                    u.pop("mute", None)
                if appendvars.get(nm) and appendvars[nm].get():
                    u["check_appends"] = True
                else:
                    u.pop("check_appends", None)
            try:
                with open(monitor.CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
            except Exception as e:
                messagebox.showerror("保存失败", str(e))
                return
            self._rebuild()
            self.set_status("已更新用户设置（配色 / 静音 / 查追加）。")
            win.destroy()

        ttk.Button(win, text="保存", style="Accent.TButton",
                   command=save).pack(pady=12)

    def set_status(self, text):
        self.status.config(text=text)


def main():
    root = tk.Tk()
    try:
        MonitorApp(root)
        root.mainloop()
    except Exception:
        tb = traceback.format_exc()
        try:
            with open(ERR_LOG, "w", encoding="utf-8") as f:
                f.write(tb)
        except Exception:
            pass
        try:
            messagebox.showerror("程序出错", tb)
        except Exception:
            pass


if __name__ == "__main__":
    main()
