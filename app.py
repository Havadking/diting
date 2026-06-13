# -*- coding: utf-8 -*-
"""
东方财富股吧用户监控 - 桌面版 (GUI + Windows 系统通知)

  - 后台定时抓取被监控用户的「发帖 + 评论」
  - 有新动态时弹 Windows 通知，并按时间顺序追加到窗口列表（最新在最下面）
  - 双击列表行用浏览器打开原文
不依赖任何第三方推送服务，无额度限制。
"""
import os
import queue
import threading
import traceback
import webbrowser
from datetime import datetime

import tkinter as tk
from tkinter import ttk, font as tkfont, messagebox

import monitor  # 复用已写好的抓取/解析逻辑

try:
    from winotify import Notification, audio
    HAS_TOAST = True
except Exception:
    HAS_TOAST = False

APP_ID = "东方财富股吧监控"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ERR_LOG = os.path.join(BASE_DIR, "gui_error.log")
MAX_ROWS = 1000

# ---- 配色 ----
C_BG = "#ffffff"
C_STRIPE = "#f4f6fa"
C_HEAD_BG = "#2b3a55"
C_HEAD_FG = "#ffffff"
C_SEL = "#cfe0ff"
C_TOOLBAR = "#f0f2f7"
C_POST = "#0a8f5b"    # 发帖 绿
C_REPLY = "#1d4ed8"   # 评论 蓝
C_REPOST = "#c2620a"  # 转发 橙
C_TWEET = "#7c3aed"   # 推文 紫
C_HIST = "#566072"    # 历史 深灰（可清晰阅读）


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
        self.first_cycle = True
        self.row_link = {}   # iid -> 链接
        self.row_fg = {}     # iid -> 前景色 tag
        self.row_seq = 0
        self._dirty = False
        self._last_tw = 0.0  # 上次抓推特的时间戳

        root.title("东方财富股吧 + 推特 监控 · 桌面版")
        root.geometry("1180x700")
        root.configure(bg=C_BG)
        self._setup_style()
        self._build_ui()
        self._poll_queue()

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

        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("Treeview",
                     font=self.f_base, rowheight=42,
                     background=C_BG, fieldbackground=C_BG, foreground="#1c2330",
                     borderwidth=0, relief="flat")
        st.map("Treeview",
               background=[("selected", C_SEL)],
               foreground=[("selected", "#111")])
        st.configure("Treeview.Heading",
                     font=self.f_bold, relief="flat",
                     background=C_HEAD_BG, foreground=C_HEAD_FG, padding=(8, 6))
        st.map("Treeview.Heading", background=[("active", "#3a4d70")])
        # 现代扁平按钮
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
                 bg=C_TOOLBAR, fg="#2b3a55").pack(side="left", padx=(0, 14))

        self.btn_start = ttk.Button(inner, text="开始监控", style="Accent.TButton",
                                    command=self.toggle)
        self.btn_start.pack(side="left")
        ttk.Button(inner, text="测试通知", style="Tool.TButton",
                   command=self.test_toast).pack(side="left", padx=(8, 0))
        ttk.Button(inner, text="打开配置", style="Tool.TButton",
                   command=self.open_config).pack(side="left", padx=(8, 0))
        ttk.Button(inner, text="清空列表", style="Tool.TButton",
                   command=self.clear_list).pack(side="left", padx=(8, 0))

        self.lbl_users = tk.Label(inner, text="", font=self.f_base,
                                  bg=C_TOOLBAR, fg="#5a6478")
        self.lbl_users.pack(side="right")

        tk.Frame(self.root, bg="#dfe3ea", height=1).pack(fill="x")

        # 列表
        mid = tk.Frame(self.root, bg=C_BG)
        mid.pack(fill="both", expand=True, padx=12, pady=(8, 0))

        cols = ("time", "user", "kind", "bar", "content")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        layout = [("time", "时间", 168, "center"), ("user", "用户", 124, "w"),
                  ("kind", "类型", 90, "w"), ("bar", "股吧", 150, "w"),
                  ("content", "内容（双击打开原文）", 540, "w")]
        for c, txt, w, anc in layout:
            self.tree.heading(c, text=txt, anchor="w")
            self.tree.column(c, width=w, anchor=anc, stretch=(c == "content"))

        self.tree.tag_configure("stripe_even", background=C_BG)
        self.tree.tag_configure("stripe_odd", background=C_STRIPE)
        self.tree.tag_configure("post", foreground=C_POST)
        self.tree.tag_configure("reply", foreground=C_REPLY)
        self.tree.tag_configure("repost", foreground=C_REPOST)
        self.tree.tag_configure("tweet", foreground=C_TWEET)
        self.tree.tag_configure("hist", foreground=C_HIST)

        vsb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.open_selected)

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
            what = []
            if cfg.get("monitor_posts", True):
                what.append("发帖")
            if cfg.get("monitor_replies", True):
                what.append("评论")
            txt = "股吧 %d 人(%s) · 间隔 %ds" % (n, "+".join(what),
                                              cfg.get("poll_interval_seconds", 60))
            if tw:
                txt += "    推特 %d 人 · 间隔 %ds" % (tw, cfg.get("twitter_poll_interval_seconds", 180))
            self.lbl_users.config(text=txt)
        except Exception:
            self.lbl_users.config(text="（config.json 读取失败）")

    # ---------- 控制 ----------
    def toggle(self):
        self.stop() if self.running else self.start()

    def start(self):
        try:
            cfg = monitor.load_config()
            if not cfg.get("users"):
                messagebox.showwarning("提示", "config.json 里还没有配置要监控的用户。")
                return
        except Exception as e:
            messagebox.showerror("配置错误", "读取 config.json 失败：\n%s" % e)
            return
        self.running = True
        self.first_cycle = True
        self.stop_event.clear()
        self.btn_start.config(text="停止监控")
        self._refresh_user_label()
        self.set_status("正在启动…首次抓取会先加载现有内容（灰色，不弹通知）。")
        self.worker = threading.Thread(target=self._run_loop, daemon=True)
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
        self.row_link.clear()
        self.row_fg.clear()

    def open_selected(self, _e=None):
        sel = self.tree.selection()
        if sel and self.row_link.get(sel[0]):
            webbrowser.open(self.row_link[sel[0]])

    # ---------- 后台线程 ----------
    def _emit(self, state, skey, name, items):
        """对一个来源的抓取结果做去重，首轮入历史、之后弹新动态。"""
        seen = set(state.get(skey, []))
        new_items = [it for it in items if it["key"] not in seen]
        state[skey] = list(dict.fromkeys([it["key"] for it in items] + list(seen)))[:500]
        if self.first_cycle:
            self.q.put(("history", name, sorted(items, key=lambda x: x["time"])[-10:]))
        elif new_items:
            new_items.sort(key=lambda x: x["time"])
            self.q.put(("new", name, new_items))

    def _run_loop(self):
        import random
        import time
        state = monitor.load_state()
        while not self.stop_event.is_set():
            try:
                cfg = monitor.load_config()
            except Exception as e:
                self.q.put(("status", "读取配置失败：%s" % e))
                self.stop_event.wait(5)
                continue
            interval = int(cfg.get("poll_interval_seconds", 60))

            # —— 股吧用户 ——
            for u in cfg.get("users", []):
                if self.stop_event.is_set():
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
                self.stop_event.wait(random.uniform(2, 4))

            # —— 推特用户（单独的慢节奏，降低风控/封号风险）——
            tw_users = cfg.get("twitter_users", [])
            tw_interval = int(cfg.get("twitter_poll_interval_seconds", 180))
            if tw_users and (self.first_cycle or time.time() - self._last_tw >= tw_interval):
                self._last_tw = time.time()
                for tu in tw_users:
                    if self.stop_event.is_set():
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
                    self.stop_event.wait(random.uniform(2, 4))

            monitor.save_state(state)
            self.first_cycle = False
            self.q.put(("status", "上次检查 %s · 运行中"
                        % datetime.now().strftime("%H:%M:%S")))
            waited = 0.0
            while waited < interval and not self.stop_event.is_set():
                self.stop_event.wait(1)
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
                        self._add_row(name, it, history=True)
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
            self._sort_and_stripe()
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
        for it in items:
            self._add_row(name, it, history=False)
        if len(items) > 3:
            toast("【%s】%d 条新动态" % (name, len(items)),
                  ("最新：%s" % (items[-1]["content"] or items[-1]["title"]))[:80],
                  items[-1]["link"])
        else:
            for it in items:
                head = "%s %s · %s" % (it["icon"], it["kind"], name)
                body = it["content"] or it["title"] or "(无正文)"
                if it["kind"] == "评论" and it["ctx_text"]:
                    body = "评论《%s》：%s" % (it["ctx_text"][:18], body)
                toast(head, body[:120], it["link"])
        self.set_status("%s 新增 %d 条 · %s"
                        % (name, len(items), datetime.now().strftime("%H:%M:%S")))

    def _add_row(self, name, it, history=False):
        tagmap = {"发帖": "post", "评论": "reply", "转发": "repost",
                  "推文": "tweet", "转推": "tweet"}
        fg = "hist" if history else tagmap.get(it["kind"], "")
        type_txt = "●  " + it["kind"]
        content = it["content"] or it["title"] or "(无正文)"
        if it["kind"] == "评论" and it["ctx_text"]:
            content = "[评论《%s》] %s" % (it["ctx_text"][:14], content)
        content = content.replace("\n", " ").replace("\r", " ").strip()
        iid = "row%d" % self.row_seq
        self.row_seq += 1
        # 先追加到末尾，稍后统一排序
        self.tree.insert("", "end", iid=iid,
                         values=(it["time"], name, type_txt, it["bar"] or "—", content))
        self.row_fg[iid] = fg
        self.row_link[iid] = it["link"]

    def _sort_and_stripe(self):
        rows = list(self.tree.get_children())
        rows.sort(key=lambda iid: self.tree.set(iid, "time"))  # 时间升序：最新在底
        # 超量裁剪最旧的
        if len(rows) > MAX_ROWS:
            for old in rows[:len(rows) - MAX_ROWS]:
                self.row_link.pop(old, None)
                self.row_fg.pop(old, None)
                self.tree.delete(old)
            rows = rows[len(rows) - MAX_ROWS:]
        for i, iid in enumerate(rows):
            self.tree.move(iid, "", i)
            stripe = "stripe_odd" if i % 2 else "stripe_even"
            self.tree.item(iid, tags=(self.row_fg.get(iid, ""), stripe))

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
