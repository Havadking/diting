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
import threading
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
C_TOOLBAR = C_PAGE
C_TEXT = "#1c1c1c"    # 内容主文字
C_MUTED = "#6b7280"   # 类型/时间等次要文字
C_MUTED2 = "#9aa1ac"  # 来源名，比 C_MUTED 更淡
C_BORDER = "#ececec"  # 行与行之间的分隔线
C_POST = "#0a8f5b"    # 发帖 绿
C_REPLY = "#1d4ed8"   # 评论 蓝
C_REPOST = "#c2620a"  # 转发 橙
C_TWEET = "#7c3aed"   # 推文 紫
C_HIST = "#566072"    # 兜底深灰（未知类型的色点）

# 类型用一个小色点表示（不再整行上底色），色点颜色复用上面几个类型色
KIND_DOT = {
    "发帖": C_POST, "评论": C_REPLY, "转发": C_REPOST,
    "转推": C_REPOST, "推文": C_TWEET,
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
        self.user_collapsed = {}  # 日期 -> 是否折叠（用户手动覆盖）
        self._color_map = {}      # 用户名 -> 颜色
        self._muted = set()       # 被静音(只收不提示)的用户名
        self._dirty = False
        self._last_tw = 0.0       # 上次抓推特的时间戳
        self._last_wb = 0.0       # 上次抓微博的时间戳

        root.title("东方财富股吧监控 · 桌面版")
        root.geometry("1180x700")
        root.configure(bg=C_PAGE)
        self._setup_style()
        self._build_ui()
        self._poll_queue()
        # 打开即自动开始监控（省去手动点「开始监控」；可随时点「停止监控」暂停）
        self.root.after(800, lambda: self.start(silent=True))

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
            # sv_ttk 已经把按钮画成圆角，这里只调字体，不再覆盖 background/relief，
            # 否则会把它的圆角边框图片盖掉。
            st.configure("Tool.TButton", font=self.f_base, padding=(14, 7))
            st.configure("Accent.TButton", font=self.f_bold, padding=(16, 7))
        else:
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

        # 列表：自绘极简列表（无网格线，类型用色点表示，不用 Treeview）
        mid = tk.Frame(self.root, bg=C_PAGE)
        mid.pack(fill="both", expand=True, padx=12, pady=(8, 0))

        card = tk.Frame(mid, bg=C_BG, highlightthickness=1, highlightbackground="#e3e3e3")
        card.pack(fill="both", expand=True)

        # 每列的（key, 表头文字, 像素宽度），"内容"列不在这里——它占满剩余空间
        self.COLS = [("time", "时间", 52), ("user", "用户", 92),
                     ("kind", "类型", 44), ("bar", "来源", 108)]
        self.CELL_H = 22   # 每行文字格子的高度
        self.ROW_PADY = 9  # 行的上下留白，加上 CELL_H 就是整行高度

        head = tk.Frame(card, bg=C_HEAD_BG)
        head.pack(fill="x")
        tk.Frame(head, width=14, height=30, bg=C_HEAD_BG).pack(side="left")  # 对齐色点位置
        for _key, txt, w in self.COLS:
            slot = tk.Frame(head, width=w, height=30, bg=C_HEAD_BG)
            slot.pack_propagate(False)
            slot.pack(side="left")
            tk.Label(slot, text=txt, font=self.f_bold, bg=C_HEAD_BG,
                     fg=C_HEAD_FG, anchor="w").pack(side="left", padx=(2, 0))
        tk.Label(head, text="内容（双击打开原文）", font=self.f_bold, bg=C_HEAD_BG,
                 fg=C_HEAD_FG, anchor="w").pack(side="left", padx=(10, 0), pady=6)

        scroll_wrap = tk.Frame(card, bg=C_BG)
        scroll_wrap.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(scroll_wrap, bg=C_BG, highlightthickness=0)
        vsb = ttk.Scrollbar(scroll_wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.list_body = tk.Frame(self.canvas, bg=C_BG)
        self._list_win = self.canvas.create_window((0, 0), window=self.list_body, anchor="nw")
        self.list_body.bind("<Configure>",
                            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfig(self._list_win, width=e.width))

        def _wheel(e):
            self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", _wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

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
        for w in self.list_body.winfo_children():
            w.destroy()
        self.items.clear()
        self.item_keys.clear()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

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
        import random
        import time
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

            monitor.save_state(state)
            self.first_cycle = False
            self.q.put(("status", "上次检查 %s · 运行中"
                        % datetime.now().strftime("%H:%M:%S")))
            waited = 0.0
            while waited < interval and not stop_event.is_set():
                stop_event.wait(1)
                waited += 1

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
                self.canvas.yview_moveto(1.0)
        self.root.after(400, self._poll_queue)

    def _at_bottom(self):
        try:
            return self.canvas.yview()[1] >= 0.985
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
        self.items.append({
            "key": it["key"], "name": name, "kind": it["kind"],
            "icon": it.get("icon") or "",
            "time": it["time"] or "", "bar": it["bar"] or "—",
            "content": content, "link": it["link"],
        })

    # —— 颜色 ——
    @staticmethod
    def _today():
        return datetime.now().strftime("%Y-%m-%d")

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

    def _resolve_fg(self, name):
        return self._color_map.get(name) or C_TEXT

    @staticmethod
    def _elide(text, fnt, max_px):
        """按像素宽度截断文字，超出的用「…」代替（tkinter Label 不会自动省略号）。"""
        text = text or ""
        if fnt.measure(text) <= max_px:
            return text
        while text and fnt.measure(text + "…") > max_px:
            text = text[:-1]
        return (text + "…") if text else "…"

    @staticmethod
    def _bind_recursive(widget, seq, handler):
        """给一个 Frame 行和它所有子控件都绑定同一个事件——tkinter 的鼠标事件
        不会像网页那样从子控件冒泡到父控件，双击/悬停手型必须逐个控件绑。"""
        widget.bind(seq, handler)
        for c in widget.winfo_children():
            MonitorApp._bind_recursive(c, seq, handler)

    # —— 重建列表（自绘 Frame 行 + 日期表头 + 自定义折叠）——
    def _rebuild(self):
        self._refresh_config_maps()
        if len(self.items) > MAX_ROWS:
            self.items.sort(key=lambda x: x["time"])
            drop = self.items[:len(self.items) - MAX_ROWS]
            self.item_keys.difference_update(d["key"] for d in drop)
            self.items = self.items[len(self.items) - MAX_ROWS:]

        at_bottom = self._at_bottom()
        for w in self.list_body.winfo_children():
            w.destroy()

        groups = defaultdict(list)
        for it in self.items:
            groups[(it["time"][:10] or "未知日期")].append(it)

        today = self._today()
        avail_w = max(self.canvas.winfo_width(), 900)
        fixed_w = 14 + sum(w for _k, _t, w in self.COLS) + 14 + 10 + 8 + 24
        content_w = max(avail_w - fixed_w, 160)

        for date in sorted(groups):
            rows = sorted(groups[date], key=lambda x: x["time"])
            collapsed = self.user_collapsed.get(date, date != today)
            self._build_date_header(date, rows, collapsed, today)
            if collapsed:
                continue
            for it in rows:
                self._build_row(it, content_w)

        self.list_body.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        if at_bottom:
            self.canvas.yview_moveto(1.0)

    def _build_date_header(self, date, rows, collapsed, today):
        arrow = "▶" if collapsed else "▼"
        mark = "今天 " if date == today else ""
        bg = "#e4edfb" if date == today else "#f0f1f3"
        fg = "#0b57a4" if date == today else "#3c4147"
        hdr = tk.Frame(self.list_body, bg=bg, cursor="hand2")
        hdr.pack(fill="x")
        lbl = tk.Label(hdr, text="%s %s   %s(%d)" % (arrow, date, mark, len(rows)),
                       font=self.f_bold, bg=bg, fg=fg, anchor="w", padx=14, pady=8,
                       cursor="hand2")
        lbl.pack(fill="x")

        def _toggle(_e=None, d=date):
            self.user_collapsed[d] = not self.user_collapsed.get(d, d != self._today())
            self._rebuild()

        self._bind_recursive(hdr, "<Button-1>", _toggle)

    def _build_row(self, it, content_w):
        fg_name = self._resolve_fg(it["name"])
        dot_color = KIND_DOT.get(it["kind"], C_HIST)

        row = tk.Frame(self.list_body, bg=C_BG, cursor="hand2")
        row.pack(fill="x")
        inner = tk.Frame(row, bg=C_BG, cursor="hand2")
        inner.pack(fill="x", padx=(14, 10), pady=self.ROW_PADY)

        dotwrap = tk.Frame(inner, width=14, height=self.CELL_H, bg=C_BG, cursor="hand2")
        dotwrap.pack_propagate(False)
        dotwrap.pack(side="left")
        dot = tk.Canvas(dotwrap, width=8, height=8, bg=C_BG, highlightthickness=0,
                        cursor="hand2")
        dot.create_oval(1, 1, 7, 7, fill=dot_color, outline=dot_color)
        dot.pack(anchor="w")

        def _cell(text, w, fg, font=None):
            slot = tk.Frame(inner, width=w, height=self.CELL_H, bg=C_BG, cursor="hand2")
            slot.pack_propagate(False)
            slot.pack(side="left")
            tk.Label(slot, text=text, font=font or self.f_base, bg=C_BG,
                     fg=fg, anchor="w", cursor="hand2").pack(side="left", fill="x", padx=(2, 0))

        _cell(it["time"][11:16], 52, C_MUTED)
        _cell(self._elide(it["name"], self.f_bold, 84), 92, fg_name, font=self.f_bold)
        _cell(it["kind"], 44, C_MUTED)
        _cell(self._elide(it["bar"], self.f_base, 100), 108, C_MUTED2)

        content_text = self._elide(it["content"], self.f_base, content_w)
        lbl_content = tk.Label(inner, text=content_text, font=self.f_base,
                               bg=C_BG, fg=C_TEXT, anchor="w", cursor="hand2")
        lbl_content.pack(side="left", fill="x", expand=True, padx=(8, 0))

        tk.Frame(self.list_body, bg=C_BORDER, height=1).pack(fill="x", padx=14)

        link = it["link"]

        def _open(_e=None, url=link):
            webbrowser.open(url)

        self._bind_recursive(row, "<Double-1>", _open)

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
        win.title("用户设置：配色 / 静音")
        win.configure(bg=C_BG)
        win.geometry("1000x560")
        tk.Label(win, text="点色块给用户上色（相同颜色＝同一组，「默认」按类型配色）；勾选「🔕静音」= 该用户只收进列表、不弹通知。",
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
            try:
                with open(monitor.CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
            except Exception as e:
                messagebox.showerror("保存失败", str(e))
                return
            self._rebuild()
            self.set_status("已更新用户设置（配色 / 静音）。")
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
