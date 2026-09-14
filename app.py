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
import traceback
import webbrowser
from collections import defaultdict
from datetime import datetime

import tkinter as tk
from tkinter import ttk, font as tkfont, messagebox

import monitor  # 只为 CONFIG_PATH（打开配置 / 保存用户设置）
from core import MonitorCore, KIND_BG, PALETTE, MAX_ROWS, ENABLE_TWITTER, ENABLE_WEIBO

try:
    import sv_ttk  # 可选：Windows 11 风格现代主题，没装则自动退回旧的手工配色
    HAS_THEME = True
except Exception:
    HAS_THEME = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ERR_LOG = os.path.join(BASE_DIR, "gui_error.log")

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


class MonitorApp:
    def __init__(self, root):
        self.root = root
        self.core = MonitorCore()     # 抓取 / 去重 / 通知 / 持久化全在这里，本类只管画
        self.q = self.core.subscribe()
        self.row_link = {}        # iid -> 链接（每次重建）
        self.row_item = {}        # iid -> 完整 item dict（右键查看全文用，每次重建）
        self.header_date = {}     # 日期表头 iid -> 日期
        self.user_collapsed = {}  # 日期 -> 是否折叠（用户手动覆盖）
        self._pending_render = [] # 已入 core.items 但还没渲染进 Treeview 的新条目
        self._row_seq = 0         # 行 iid 计数器，全量重建和快速追加共用，避免 iid 撞车
        self._date_row_count = {} # 日期 -> 该日期总条数（表头「(N)」用，快速追加时增量更新）
        self._color_tags = set()  # 已创建的颜色 tag
        self._dirty = False

        root.title("谛听 · 东方财富股吧监控")
        root.geometry("1180x700")
        root.configure(bg=C_PAGE)
        self._setup_style()
        self._build_ui()
        self.set_status(self.core.status_text)
        if self.core.items:
            self._rebuild()
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

        tk.Label(inner, text="谛听", font=self.f_title,
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
                  ("content", "内容（双击打开原文 / 右键看全文）", 520, "w")]
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
        self.tree.bind("<Button-3>", self._on_right_click)

        # 状态栏
        bar = tk.Frame(self.root, bg=C_TOOLBAR)
        bar.pack(fill="x", side="bottom")
        self.status = tk.Label(bar, text="未启动", font=self.f_base, bg=C_TOOLBAR,
                               fg="#5a6478", anchor="w", padx=12, pady=5)
        self.status.pack(fill="x")

        self._refresh_user_label()

    def _refresh_user_label(self):
        try:
            self.lbl_users.config(text=self.core.describe_config())
        except Exception:
            self.lbl_users.config(text="（config.json 读取失败）")

    # ---------- 控制 ----------
    def toggle(self):
        self.stop() if self.core.running else self.start()

    def start(self, silent=False):
        err = self.core.start()
        if err:
            if not silent:
                if err.startswith("读取"):
                    messagebox.showerror("配置错误", err)
                else:
                    messagebox.showwarning("提示", err)
            return
        self.btn_start.config(text="停止监控")
        self._refresh_user_label()

    def stop(self):
        self.core.stop()
        self.btn_start.config(text="开始监控")

    def test_toast(self):
        self.core.test_toast()

    def open_config(self):
        try:
            os.startfile(monitor.CONFIG_PATH)
        except Exception:
            messagebox.showinfo("配置文件路径", monitor.CONFIG_PATH)

    def clear_list(self):
        self.core.clear()
        self._clear_tree()

    def _clear_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.row_link.clear()
        self.row_item.clear()
        self.header_date.clear()
        self._pending_render.clear()
        self._date_row_count.clear()

    def open_selected(self, _e=None):
        sel = self.tree.selection()
        if sel and self.row_link.get(sel[0]):
            webbrowser.open(self.row_link[sel[0]])

    def _on_right_click(self, event):
        """右键一行：弹小窗看全文。Treeview 列宽有限，长内容会被视觉裁掉，
        底层数据其实是完整的，不用重新请求，直接从 self.row_item 拿。"""
        row = self.tree.identify_row(event.y)
        it = self.row_item.get(row)
        if not it:
            return
        self.tree.selection_set(row)
        self._show_full_content(it, event.x_root, event.y_root)

    @staticmethod
    def _monitor_work_area(x, y):
        """Windows 下拿 (x,y) 所在物理显示器的可用区域，用来夹取弹窗位置——
        tkinter 的 winfo_screenwidth/height 只认主屏尺寸，多屏时如果直接拿它当
        边界夹坐标，副屏（尤其是坐标比主屏更靠右/靠下，或者干脆是负坐标摆在
        主屏左侧/上方的情况）点击一律会被夹回主屏，表现就是"弹窗跑去主屏角落"。
        用 MonitorFromPoint 找到鼠标点所在的那块屏幕，再用 GetMonitorInfo 拿它
        真实的坐标范围（可能是负数），才能正确地"只夹在同一块屏幕内"。"""
        try:
            import ctypes
            from ctypes import wintypes

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                            ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]

            user32 = ctypes.windll.user32
            # HMONITOR 在 64 位上是指针宽度，不显式声明返回类型 ctypes 会按 c_int
            # 截断，句柄值就错了，后面 GetMonitorInfoW 会传进去一个野句柄。
            user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
            user32.MonitorFromPoint.restype = wintypes.HANDLE
            user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
            user32.GetMonitorInfoW.restype = wintypes.BOOL
            MONITOR_DEFAULTTONEAREST = 2
            pt = wintypes.POINT(int(x), int(y))
            hmon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                r = mi.rcWork
                return r.left, r.top, r.right, r.bottom
        except Exception:
            pass
        return None

    def _show_full_content(self, it, x, y):
        win = tk.Toplevel(self.root)
        win.title("%s · %s" % (it["name"], it["kind"]))
        win.configure(bg=C_BG)

        head = "%s%s · %s · %s" % (it.get("icon") or "", it["kind"], it["name"], it["time"])
        if it.get("bar") and it["bar"] != "—":
            head += " · " + it["bar"]
        tk.Label(win, text=head, font=self.f_bold, bg=C_BG, fg="#1c1c1c",
                 anchor="w", wraplength=580, justify="left").pack(fill="x", padx=16, pady=(14, 6))

        body = tk.Frame(win, bg=C_BG)
        body.pack(fill="both", expand=True, padx=16)
        txt = tk.Text(body, wrap="word", font=self.f_base, bg=C_BG, fg="#1c1c1c",
                      relief="flat", height=12, width=60, padx=4, pady=4)
        vsb = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=vsb.set)
        txt.insert("1.0", it["content"] or "(无正文)")
        txt.configure(state="disabled")
        txt.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        btns = tk.Frame(win, bg=C_BG)
        btns.pack(fill="x", padx=16, pady=12)

        def _copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(it["content"] or "")
            self.set_status("已复制内容到剪贴板。")

        ttk.Button(btns, text="复制内容", style="Tool.TButton",
                   command=_copy).pack(side="left")
        ttk.Button(btns, text="打开原文", style="Tool.TButton",
                   command=lambda: webbrowser.open(it["link"])).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="关闭", style="Tool.TButton",
                   command=win.destroy).pack(side="right")

        # 内容都装进去之后窗口才有真实尺寸，这时候再定位才能准——创建 Toplevel 后
        # 立刻 geometry("+x+y") 是错的：那会儿窗口还没内容，尺寸没定，等 pack 完
        # 窗口管理器常常会把它挪到别的地方，而不是停在鼠标点击的位置。
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        area = self._monitor_work_area(x, y)
        if area:
            left, top, right, bottom = area
        else:  # 拿不到就退回单屏假设，好歹不崩
            left, top = 0, 0
            right, bottom = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        px = max(left, min(x, right - w))
        py = max(top, min(y, bottom - h))
        win.geometry("+%d+%d" % (px, py))

    # ---------- 后台线程 ----------
    # ---------- 主线程：消费队列 ----------
    def _poll_queue(self):
        """消费 core 的事件队列。条目已经是处理好的展示字段（core 入列并写库后才广播），
        这里只负责攒起来渲染。"""
        scroll_needed = False
        try:
            while True:
                etype, payload = self.q.get_nowait()
                if etype == "status":
                    self.set_status(payload["text"])
                elif etype in ("history", "new"):
                    self._pending_render.extend(payload)
                    self._dirty = True
                    scroll_needed = True
                elif etype == "cleared":
                    self._clear_tree()
        except queue.Empty:
            pass
        if self._dirty:
            at_bottom = self._at_bottom()
            pending = self._pending_render
            self._pending_render = []
            if self._can_fast_append(pending):
                self._append_pending_rows(pending)
            else:
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

    def _resolve_fg(self, name, kind):
        c = self.core.color_map.get(name)
        if c:
            return self._color_tag(c)
        return {"发帖": "post", "评论": "reply", "转发": "repost",
                "推文": "tweet", "转推": "tweet", "追加": "append"}.get(kind, "")

    @staticmethod
    def _resolve_bg(kind):
        return "bg_" + kind if kind in KIND_BG else ""

    # —— 重建列表（扁平 + 日期表头 + 自定义折叠）——
    def _rebuild(self):
        self.core.refresh_config_maps()
        self._pending_render = []  # 全量重建会把 core.items 全部渲染一遍，不留待追加的尾巴
        with self.core.lock:
            items = list(self.core.items)

        at_bottom = self._at_bottom()
        self.tree.delete(*self.tree.get_children())
        self.row_link.clear()
        self.row_item.clear()
        self.header_date.clear()
        self._date_row_count.clear()
        self._row_seq = 0

        groups = defaultdict(list)
        for it in items:
            groups[(it["time"][:10] or "未知日期")].append(it)

        today = self._today()
        for date in sorted(groups):
            rows = sorted(groups[date], key=lambda x: x["time"])
            self._date_row_count[date] = len(rows)
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
                self._row_seq += 1
                iid = "r%d" % self._row_seq
                kind_txt = ("%s %s" % (it.get("icon") or "", it["kind"])).strip()
                self.tree.insert("", "end", iid=iid,
                                 values=(it["time"][11:16], it["name"],
                                         kind_txt, it["bar"], it["content"]),
                                 tags=(self._resolve_bg(it["kind"]),
                                       self._resolve_fg(it["name"], it["kind"])))
                self.row_link[iid] = it["link"]
                self.row_item[iid] = it
        if at_bottom:
            self.tree.yview_moveto(1.0)

    def _can_fast_append(self, pending):
        """能不能走快速追加：新条目全属于「今天」、今天的表头已经在树里、没被折叠、
        也不需要触发 MAX_ROWS 裁剪。不满足就老老实实走全量重建，图个稳。"""
        if not pending:
            return False
        with self.core.lock:
            if len(self.core.items) >= MAX_ROWS:
                return False
        today = self._today()
        hid = "h_" + today.replace("-", "")
        if not self.tree.exists(hid):
            return False
        if self.user_collapsed.get(today, False):
            return False
        return all(it["time"][:10] == today for it in pending)

    def _append_pending_rows(self, pending):
        """常见情况（有新动态但不用挪动/删除已有行）走这条快路：只插入新行、更新
        当天表头的计数，不清空重建整棵 1000 行的树——那样又慢，还会把用户正在看
        的滚动位置弹飞（旧实现每来一条新消息就全量重建一次，这是之前"滚动卡"的
        主因：树被清空重插时，没在最底部的滚动位置没法保持，等于每次都给拽回去）。"""
        today = self._today()
        hid = "h_" + today.replace("-", "")
        self._date_row_count[today] = self._date_row_count.get(today, 0) + len(pending)
        self.tree.item(hid, values=("▼ %s" % today,
                                    "今天 (%d)" % self._date_row_count[today], "", "", ""))
        for it in sorted(pending, key=lambda x: x["time"]):
            self._row_seq += 1
            iid = "r%d" % self._row_seq
            kind_txt = ("%s %s" % (it.get("icon") or "", it["kind"])).strip()
            self.tree.insert("", "end", iid=iid,
                             values=(it["time"][11:16], it["name"],
                                     kind_txt, it["bar"], it["content"]),
                             tags=(self._resolve_bg(it["kind"]),
                                   self._resolve_fg(it["name"], it["kind"])))
            self.row_link[iid] = it["link"]
            self.row_item[iid] = it

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
