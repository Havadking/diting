/* 谛听 · 网页版前端。React 18 + htm（标签模板，零构建），全局 React / ReactDOM / htm 由 index.html 先加载。
 * 数据流：启动 GET /api/snapshot → 整体替换；之后 EventSource(/api/events) 增量追加。见 docs/web-design.md §6。 */
(function () {
  "use strict";
  const { useState, useEffect, useLayoutEffect, useMemo, useReducer, useRef, useCallback } = React;
  const html = htm.bind(React.createElement);

  /* ---------- 常量 ---------- */
  const KINDS = {
    "发帖": { fg: "var(--k-post-fg)", bg: "var(--k-post-bg)" },
    "评论": { fg: "var(--k-reply-fg)", bg: "var(--k-reply-bg)" },
    "转发": { fg: "var(--k-repost-fg)", bg: "var(--k-repost-bg)" },
    "追加": { fg: "var(--k-append-fg)", bg: "var(--k-append-bg)" },
    // 推特下线中，但 core 的 kind 字面量里有这两个，来了也别没配色
    "推文": { fg: "var(--k-reply-fg)", bg: "var(--k-reply-bg)" },
    "转推": { fg: "var(--k-repost-fg)", bg: "var(--k-repost-bg)" },
  };
  const WEEK = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
  const weekday = d => WEEK[new Date(d + "T00:00:00").getDay()];
  const today = () => {
    const d = new Date(), p = n => String(n).padStart(2, "0");
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
  };

  /* ---------- 小图标 ---------- */
  const svg = (inner, fill) => html`<svg viewBox="0 0 24 24" fill=${fill || "none"} stroke=${fill ? "none" : "currentColor"} stroke-width="2" stroke-linecap="round" stroke-linejoin="round" dangerouslySetInnerHTML=${{ __html: inner }}/>`;
  const I = {
    menu: svg('<path d="M4 7h16M4 12h16M4 17h16"/>'),
    chev: svg('<path d="M6 9l6 6 6-6"/>'),
    mute: svg('<path d="M13.7 21a2 2 0 0 1-3.4 0M18.6 13A17 17 0 0 1 18 8a6 6 0 0 0-9.3-5M6.3 6.3A6 6 0 0 0 6 8c0 7-3 9-3 9h14M1 1l22 22"/>'),
    pause: svg('<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>'),
    play: svg('<path d="M7 5v14l12-7z"/>', "currentColor"),
    gear: svg('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),
    trash: svg('<path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/>'),
    bell: svg('<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0"/>'),
    moon: svg('<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>'),
    sun: svg('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
    power: svg('<path d="M18.4 6.6a9 9 0 1 1-12.8 0M12 2v10"/>'),
    down: svg('<path d="M12 5v14M5 12l7 7 7-7"/>'),
    more: svg('<circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/>', "currentColor"),
  };

  /* ---------- 正文里的上下文前缀拆分 ----------
   * core._add_item 把上下文拼进 content：`[评论《xx》] 正文` / `[转发自 某人《xx》] 正文` / `[转推自 某人] 正文`。
   * DB 结构不改，前端拆出来单独渲染成一条小 ctx 条。 */
  const CTX_RE = /^\[(评论|转发自|转推自)\s*([^《\]]*?)\s*(?:《([\s\S]*?)》)?\]\s*([\s\S]*)$/;
  function splitCtx(it) {
    const m = CTX_RE.exec(it.content || "");
    if (!m) return { ctx: null, body: it.content || "" };
    const [, verb, who, title, body] = m;
    const label = verb === "评论" ? "评论" : (verb + (who ? " " + who : ""));
    return { ctx: { label, title: title || "" }, body };
  }

  /* ---------- store ---------- */
  const initial = { items: [], keys: new Set(), status: { text: "连接中…", running: false, last_check: "" },
                    users: [], config: {}, connected: false, loaded: false, quit: false, hasMore: false };
  const byTime = (a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : (a.key < b.key ? -1 : 1));
  function mergeItems(items, keys, incoming) {
    let changed = false;
    const next = items.slice(), nk = new Set(keys);
    for (const e of incoming) {
      if (!e || !e.key || nk.has(e.key)) continue;
      nk.add(e.key); next.push(e); changed = true;
    }
    if (!changed) return null;
    next.sort(byTime);
    return { items: next, keys: nk };
  }
  function reducer(s, a) {
    switch (a.type) {
      case "snapshot": {
        const items = (a.data.items || []).slice().sort(byTime);
        // 重连补漏时保留已经翻出来的更早历史：快照只覆盖最近 N 条，用 merge 而不是整体替换
        const m = s.loaded ? mergeItems(s.items, s.keys, items) : null;
        return { ...s, items: m ? m.items : items, keys: m ? m.keys : new Set(items.map(i => i.key)),
                 status: a.data.status || s.status, users: a.data.users || [], config: a.data.config || {},
                 loaded: true, hasMore: s.loaded ? s.hasMore : !!a.data.has_more };
      }
      case "append": {
        const m = mergeItems(s.items, s.keys, a.items);
        return m ? { ...s, ...m } : s;
      }
      case "older": {
        const m = mergeItems(s.items, s.keys, a.items);
        return { ...s, ...(m || {}), hasMore: a.hasMore };
      }
      case "status": return { ...s, status: a.status };
      case "cleared": return { ...s, items: [], keys: new Set(), hasMore: true };
      case "connected": return { ...s, connected: a.value };
      case "config": return { ...s, users: a.users || s.users };
      case "quit": return { ...s, quit: true, connected: false };
      default: return s;
    }
  }

  const PALETTE = [
    ["朱红", "#ED5126"], ["橘橙", "#F97D1C"], ["土黄", "#D6A01D"], ["竹绿", "#1BA784"],
    ["翠蓝", "#1E9EB3"], ["群青", "#1772B4"], ["青莲", "#8B2671"], ["品红", "#EF3473"],
  ];

  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                  body: JSON.stringify(body || {}) });
    let d = {};
    try { d = await r.json(); } catch (e) {}
    if (!r.ok || d.ok === false) throw new Error(d.error || ("HTTP " + r.status));
    return d;
  }

  async function fetchSnapshot(dispatch) {
    const r = await fetch("/api/snapshot", { cache: "no-store" });
    if (!r.ok) throw new Error("snapshot " + r.status);
    dispatch({ type: "snapshot", data: await r.json() });
  }

  /* ---------- localStorage 小工具（私有窗口/禁存储时 accessor 会抛，全部包起来） ---------- */
  const store = {
    get(k, dflt) { try { const v = localStorage.getItem(k); return v === null ? dflt : JSON.parse(v); } catch (e) { return dflt; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
  };
  const NEW_MARK_MS = 3000;   // 「新」角标 + 高亮保留多久
  const THEMES = ["system", "light", "dark"];
  const applyTheme = t => {
    const root = document.documentElement;
    if (t === "light" || t === "dark") root.setAttribute("data-theme", t); else root.removeAttribute("data-theme");
  };
  const isDarkNow = t => t === "dark" || (t === "system" && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  const BOTTOM_SLACK = 40;    // 距底部多少像素以内算"在底部"

  /* ---------- 组件 ---------- */
  function Card({ it, color, open, isNew, onToggle }) {
    const k = KINDS[it.kind] || KINDS["发帖"];
    const { ctx, body } = useMemo(() => splitCtx(it), [it]);
    const cls = ["card", open && "open", isNew && "new", it.kind === "追加" && "append"].filter(Boolean).join(" ");
    const style = color ? { "--uc": color } : undefined;
    return html`
      <div class=${cls} style=${style} tabIndex="0" onClick=${onToggle} data-key=${it.key}
           onKeyDown=${e => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onToggle())}>
        <div class="stripe"/>
        <div class="body">
          <div class="head">
            <span class="uname">${it.name}</span>
            <span class="pill" style=${{ "--kbg": k.bg, "--kfg": k.fg }}>${it.kind}</span>
            ${it.bar && it.bar !== "—" && html`<span class="bar">${it.bar}</span>`}
            <time class="t" dateTime=${it.time}>${it.time.slice(11, 16)}</time>
          </div>
          ${ctx && html`<div class="ctx">${ctx.label}${ctx.title && html`<b>《${ctx.title}》</b>`}</div>`}
          <div class="txt">${body}</div>
          ${open && html`
            <div class="foot">
              ${it.link && html`<a href=${it.link} target="_blank" rel="noopener" onClick=${e => e.stopPropagation()}>打开原帖 ↗</a>`}
              <span>${it.time}</span>
              <span>${it.key}</span>
            </div>`}
        </div>
      </div>`;
  }

  function DateGroup({ date, list, isToday, collapsed, onToggle, colorOf, openKey, setOpenKey, newKeys }) {
    return html`
      <section>
        <div class="dhead">
          <button onClick=${onToggle}>
            <span class=${"chev" + (collapsed ? " closed" : "")}>${I.chev}</span>
            <span class="d">${isToday ? "今天" : date}</span>
            <span class="wk">${weekday(date)}${isToday ? " · " + date : ""}</span>
          </button>
          <span class="n">${list.length}</span>
          <span class="rule"/>
        </div>
        ${!collapsed && html`
          <div class="cards">
            ${list.map(it => html`
              <${Card} key=${it.key} it=${it} color=${colorOf(it.name)} open=${openKey === it.key} isNew=${newKeys.has(it.key)}
                       onToggle=${() => setOpenKey(k => (k === it.key ? null : it.key))}/>`)}
          </div>`}
      </section>`;
  }

  function Toast({ msg }) {
    return msg ? html`<div class=${"toast" + (msg.kind === "error" ? " error" : "")} role="status">${msg.text}</div>` : null;
  }

  function Drawer({ users, onClose, onSaved, toast }) {
    const [pend, setPend] = useState(() => Object.fromEntries(users.map(u => [u.name, { ...u }])));
    const [saving, setSaving] = useState(false);
    const upd = (nm, patch) => setPend(p => ({ ...p, [nm]: { ...p[nm], ...patch } }));
    useEffect(() => {
      const h = e => e.key === "Escape" && onClose();
      window.addEventListener("keydown", h); return () => window.removeEventListener("keydown", h);
    }, [onClose]);
    const save = async () => {
      setSaving(true);
      try {
        await post("/api/users", { users: Object.values(pend).map(u => ({
          name: u.name, color: u.color || null, mute: !!u.mute, check_appends: !!u.check_appends })) });
        toast("已保存用户设置");
        onSaved && onSaved();
        onClose();
      } catch (e) {
        toast("保存失败：" + e.message, "error");
      } finally { setSaving(false); }
    };
    return html`
      <div class="scrim" onClick=${onClose}/>
      <aside class="drawer" role="dialog" aria-label="用户设置">
        <header>
          <div>
            <h3>用户设置</h3>
            <p>配色 · 静音 · 查追加 — 保存后立刻生效，不用重启</p>
          </div>
          <span class="spacer"/>
          <button class="btn quiet" onClick=${onClose}>关闭</button>
        </header>
        <div class="legend">
          ${PALETTE.map(([nm, hx]) => html`<span key=${hx}><i style=${{ "--c": hx }}/>${nm}</span>`)}
        </div>
        <div class="list">
          ${users.map(u => {
            const p = pend[u.name];
            return html`
              <div class="srow" key=${u.uid + u.name} style=${p.color ? { "--uc": p.color } : undefined}>
                <div class="who"><b>${u.name}</b><span>${u.uid}</span></div>
                <div class="swatches">
                  <button class=${"swatch none" + (!p.color ? " on" : "")} title="默认（按类型配色）"
                          onClick=${() => upd(u.name, { color: null })}>默认</button>
                  ${PALETTE.map(([nm, hx]) => html`
                    <button key=${hx} class=${"swatch" + ((p.color || "").toLowerCase() === hx.toLowerCase() ? " on" : "")} title=${nm}
                            style=${{ "--c": hx }} onClick=${() => upd(u.name, { color: hx })}/>`)}
                </div>
                <div class="toggles">
                  <label class=${"tg" + (p.mute ? " on" : "")} onClick=${() => upd(u.name, { mute: !p.mute })}><i/>静音</label>
                  ${u.check_appends !== null && u.check_appends !== undefined && html`
                    <label class=${"tg" + (p.check_appends ? " on" : "")} onClick=${() => upd(u.name, { check_appends: !p.check_appends })}><i/>查追加</label>`}
                </div>
              </div>`;
          })}
          ${!users.length && html`<p class="empty">config.json 里还没有用户。</p>`}
        </div>
        <footer>
          <button class="btn" onClick=${onClose}>取消</button>
          <button class="btn primary" disabled=${saving} onClick=${save}>${saving ? "保存中…" : "保存"}</button>
        </footer>
      </aside>`;
  }

  function App() {
    const [s, dispatch] = useReducer(reducer, initial);
    const [drawer, setDrawer] = useState(false);
    const [menu, setMenu] = useState(false);
    const [theme, setTheme] = useState(() => { const t = store.get("diting.theme", "system"); return THEMES.includes(t) ? t : "system"; });
    useEffect(() => { applyTheme(theme); store.set("diting.theme", theme); }, [theme]);
    // 点一下在浅/深之间切；当前跟随系统时按"看起来是什么"取反
    const toggleTheme = () => setTheme(t => (isDarkNow(t) ? "light" : "dark"));
    const [olderBusy, setOlderBusy] = useState(false);
    const prependRef = useRef(null);   // 「加载更早」插入前记下滚动高度，渲染后把视口钉在原处
    const [msg, setMsg] = useState(null);
    const toastTimer = useRef(null);
    const toast = useCallback((text, kind) => {
      setMsg({ text, kind });
      clearTimeout(toastTimer.current);
      toastTimer.current = setTimeout(() => setMsg(null), 2600);
    }, []);
    // 点页面任意处收起 ⋯ 菜单
    useEffect(() => {
      if (!menu) return;
      const h = () => setMenu(false);
      window.addEventListener("click", h); return () => window.removeEventListener("click", h);
    }, [menu]);
    const control = useCallback(async (action, okText) => {
      try {
        await post("/api/control", { action });
        if (action === "quit") dispatch({ type: "quit" });
        else if (okText) toast(okText);
      } catch (e) { toast("操作失败：" + e.message, "error"); }
    }, [toast]);
    const act = {
      toggleRun: () => control(s.status.running ? "stop" : "start"),
      test: () => control("test_toast", "已发送测试通知，看右下角"),
      clear: () => window.confirm("清空当前列表？（不会删除 messages.db 里的历史）") && control("clear", "已清空"),
      quit: () => window.confirm("退出谛听？监控会停止，需要重新运行 server.py 才能恢复。") && control("quit"),
    };
    const loadOlder = useCallback(async () => {
      if (olderBusy || !s.hasMore) return;
      const first = s.items[0];
      setOlderBusy(true);
      try {
        const q = first ? "before=" + encodeURIComponent(first.time) + "&before_key=" + encodeURIComponent(first.key)
                        : "before=" + encodeURIComponent("9999-99-99 99:99:99");
        const r = await fetch("/api/items?" + q + "&limit=200", { cache: "no-store" });
        if (!r.ok) throw new Error("HTTP " + r.status);
        const d = await r.json();
        const el = mainRef.current;
        prependRef.current = el ? { h: el.scrollHeight, top: el.scrollTop } : null;
        dispatch({ type: "older", items: d.items || [], hasMore: !!d.has_more });
        if (!(d.items || []).length) toast("没有更早的记录了");
      } catch (e) {
        toast("加载失败：" + e.message, "error");
      } finally { setOlderBusy(false); }
    }, [olderBusy, s.hasMore, s.items, toast]);
    // 滚到顶部附近自动翻页。用 scroll 事件而不是 IntersectionObserver：后台标签页不渲染时 IO 不触发
    const loadOlderRef = useRef(loadOlder); loadOlderRef.current = loadOlder;
    const [rail, setRail] = useState(() => {
      const v = store.get("diting.rail", null);
      return v === null ? window.innerWidth < 900 : !!v;
    });
    const [openKey, setOpenKey] = useState(null);
    // 筛选：user 为 null 表示全部；kindOff 是被关掉的类型
    const [filter, setFilter] = useState(() => {
      const f = store.get("diting.filter", {}) || {};
      return { user: f.user || null, kindOff: new Set(Array.isArray(f.kindOff) ? f.kindOff : []) };
    });
    // 日期折叠：只记用户手动点过的（非今天的），默认非今天折叠、今天展开
    const [toggled, setToggled] = useState(() => new Map(Object.entries(store.get("diting.collapsed", {}) || {})));
    const [newKeys, setNewKeys] = useState(() => new Set());
    const [pendingBelow, setPendingBelow] = useState(0);   // 用户不在底部时到达的新条数（FAB 上的 N）
    const [unread, setUnread] = useState(0);               // 页面不可见时到达的新条数（标签页标题）
    const mainRef = useRef(null);
    const followRef = useRef(false);   // 新条目到达时用户在底部 → 渲染完跟着滚到底
    const quitCloser = useRef(null);
    useEffect(() => { if (s.quit && quitCloser.current) quitCloser.current(); }, [s.quit]);
    const td = today();

    useEffect(() => store.set("diting.rail", rail), [rail]);
    useEffect(() => store.set("diting.filter", { user: filter.user, kindOff: [...filter.kindOff] }), [filter]);
    useEffect(() => {
      const o = {}; for (const [d, v] of toggled) if (d !== td) o[d] = v;
      store.set("diting.collapsed", o);
    }, [toggled, td]);

    const isAtBottom = () => {
      const el = mainRef.current; if (!el) return true;
      return el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_SLACK;
    };
    const scrollToBottom = useCallback((smooth) => {
      const el = mainRef.current; if (!el) return;
      el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
      setPendingBelow(0);
    }, []);

    // 滚到底了就把 FAB 计数清掉
    useEffect(() => {
      const el = mainRef.current; if (!el) return;
      const h = () => {
        if (isAtBottom()) setPendingBelow(0);
        if (el.scrollTop < 80) loadOlderRef.current();
      };
      el.addEventListener("scroll", h, { passive: true });
      return () => el.removeEventListener("scroll", h);
    }, []);

    // 新动态到达：标「新」、算 FAB / 标题计数。筛选条件走 ref 拿最新值，避免 SSE effect 依赖它而反复重连。
    const filterRef = useRef(filter); filterRef.current = filter;
    const matches = (it, f) => (!f.user || it.name === f.user) && !f.kindOff.has(it.kind);
    const onNew = useCallback((entries) => {
      const fresh = (entries || []).filter(e => e && e.key);
      if (!fresh.length) return;
      const wasAtBottom = isAtBottom();
      dispatch({ type: "append", items: fresh });
      setNewKeys(prev => { const n = new Set(prev); fresh.forEach(e => n.add(e.key)); return n; });
      setTimeout(() => setNewKeys(prev => { const n = new Set(prev); fresh.forEach(e => n.delete(e.key)); return n; }), NEW_MARK_MS);
      const visibleCount = fresh.filter(e => matches(e, filterRef.current)).length;
      if (document.visibilityState !== "visible") setUnread(u => u + visibleCount);
      if (wasAtBottom) {
        followRef.current = true;   // 由下面的 useLayoutEffect 在新卡片进 DOM 后立刻滚
      } else if (visibleCount) {
        setPendingBelow(n => n + visibleCount);
      }
    }, [scrollToBottom]);

    // 启动：拉快照 + 开 SSE。断线重连成功后再拉一次快照补漏（EventSource 自己会重连）。
    useEffect(() => {
      let es, everOpened = false, alive = true;
      fetchSnapshot(dispatch).catch(() => {});
      es = new EventSource("/api/events");
      es.onopen = () => {
        if (!alive) return;
        dispatch({ type: "connected", value: true });
        if (everOpened) fetchSnapshot(dispatch).catch(() => {});
        everOpened = true;
      };
      es.onerror = () => alive && dispatch({ type: "connected", value: false });
      quitCloser.current = () => es.close();
      const on = (t, fn) => es.addEventListener(t, e => { try { fn(JSON.parse(e.data)); } catch (err) {} });
      on("history", d => dispatch({ type: "append", items: d }));
      on("new", onNew);
      on("status", d => dispatch({ type: "status", status: d }));
      on("cleared", () => { dispatch({ type: "cleared" }); setPendingBelow(0); });
      on("config", d => dispatch({ type: "config", users: d.users }));
      return () => { alive = false; es.close(); };
    }, [onNew]);

    // 跟随滚动：不用 requestAnimationFrame——标签页在后台时 rAF 不跑，切回来才追，体验像卡住
    useLayoutEffect(() => {
      if (followRef.current) { followRef.current = false; scrollToBottom(true); }
      const el = mainRef.current, p = prependRef.current;
      if (el && p) { prependRef.current = null; el.scrollTop = p.top + (el.scrollHeight - p.h); }
    }, [s.items, scrollToBottom]);

    // 首屏加载完滚到底（最新在最下面，和 tkinter 版一致）；字体晚到会撑高内容，就绪后再补滚一次
    useEffect(() => {
      if (!s.loaded) return;
      scrollToBottom(false);
      if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => scrollToBottom(false));
    }, [s.loaded, scrollToBottom]);

    // 标签页标题带未读数，切回页面清零——放副屏时一眼能看到
    useEffect(() => { document.title = unread ? "(" + unread + ") 谛听" : "谛听"; }, [unread]);
    useEffect(() => {
      const h = () => { if (document.visibilityState === "visible") setUnread(0); };
      document.addEventListener("visibilitychange", h);
      return () => document.removeEventListener("visibilitychange", h);
    }, []);

    const colorMap = useMemo(() => Object.fromEntries(s.users.filter(u => u.color).map(u => [u.name, u.color])), [s.users]);
    const colorOf = useCallback(n => colorMap[n], [colorMap]);
    const todayCount = useMemo(() => {
      const m = {}; let all = 0;
      for (const i of s.items) if (i.time.startsWith(td)) { m[i.name] = (m[i.name] || 0) + 1; all++; }
      return { m, all };
    }, [s.items, td]);
    const shown = useMemo(() => s.items.filter(i => matches(i, filter)), [s.items, filter]);
    // 按日期分组，升序：旧日期在上、今天在最下面
    const groups = useMemo(() => {
      const g = new Map();
      for (const i of shown) { const d = i.time.slice(0, 10) || "未知日期"; if (!g.has(d)) g.set(d, []); g.get(d).push(i); }
      return [...g.entries()].sort((a, b) => a[0].localeCompare(b[0]));
    }, [shown]);
    const collapsedOf = d => (toggled.has(d) ? toggled.get(d) : d !== td);
    const toggleDate = d => setToggled(m => { const n = new Map(m); n.set(d, !collapsedOf(d)); return n; });
    const setUser = u => setFilter(f => ({ ...f, user: f.user === u ? null : u }));
    const toggleKind = k => setFilter(f => { const n = new Set(f.kindOff); n.has(k) ? n.delete(k) : n.add(k); return { ...f, kindOff: n }; });
    const filtering = filter.user || filter.kindOff.size > 0;

    const st = s.status;
    return html`
      <div class=${"app" + (rail ? " rail" : "")}>
        <header class="topbar">
          <div class="brand">
            <button class="iconbtn" title=${rail ? "展开侧栏" : "收起侧栏"} onClick=${() => setRail(r => !r)}>${I.menu}</button>
            <span class="brand-mark">谛听</span>
          </div>
          <div class="status" title=${st.text}>
            <span class=${"dot" + (st.running && s.connected ? "" : " paused")}/>
            <span class="lbl">${!s.connected ? "已断开" : st.running ? "运行中" : "已停止"}${st.last_check ? " · 上次检查" : ""}</span>
            ${st.last_check && html` <time>${st.last_check}</time>`}
          </div>
          <span class="spacer"/>
          <div class="tb-actions">
            <button class="btn" title=${st.running ? "停止监控" : "开始监控"} onClick=${act.toggleRun} disabled=${!s.connected}>
              ${st.running ? I.pause : I.play}<span class="lbl">${st.running ? "停止监控" : "开始监控"}</span>
            </button>
            <button class="btn" title="用户设置" onClick=${() => setDrawer(true)} disabled=${!s.loaded}>${I.gear}<span class="lbl">设置</span></button>
            <button class="btn quiet only-wide" title="测试通知" onClick=${act.test}>${I.bell}</button>
            <button class="btn quiet only-wide" title="清空列表" onClick=${act.clear}>${I.trash}</button>
            <button class="btn quiet only-wide" title=${isDarkNow(theme) ? "切到浅色" : "切到深色"} onClick=${toggleTheme}>${isDarkNow(theme) ? I.sun : I.moon}</button>
            <button class="btn quiet only-wide" title="退出程序" onClick=${act.quit}>${I.power}</button>
            <div class="menu-wrap only-narrow">
              <button class="btn quiet" title="更多" onClick=${e => { e.stopPropagation(); setMenu(m => !m); }}>${I.more}</button>
              ${menu && html`
                <div class="menu" onClick=${e => e.stopPropagation()}>
                  <button onClick=${() => { setMenu(false); act.test(); }}>${I.bell}测试通知</button>
                  <button onClick=${() => { setMenu(false); act.clear(); }}>${I.trash}清空列表</button>
                  <button onClick=${() => { setMenu(false); toggleTheme(); }}>${isDarkNow(theme) ? I.sun : I.moon}${isDarkNow(theme) ? "浅色模式" : "深色模式"}</button>
                  <hr/>
                  <button class="danger" onClick=${() => { setMenu(false); act.quit(); }}>${I.power}退出程序</button>
                </div>`}
            </div>
          </div>
        </header>

        <nav class="side">
          <div>
            <h4>监控用户</h4>
            <div class="ulist">
              <button class=${"urow" + (!filter.user ? " on" : "")} title="全部用户" onClick=${() => setUser(null)}>
                <span class="sw" style=${{ background: "var(--line-strong)" }}/>
                <span class="nm"><span>全部</span></span>
                <span class="cnt">${todayCount.all}</span>
              </button>
              ${s.users.map(u => html`
                <button key=${u.uid + u.name} class=${"urow" + (filter.user === u.name ? " on" : "")}
                        title=${u.name + "（今日 " + (todayCount.m[u.name] || 0) + " 条）"} onClick=${() => setUser(u.name)}>
                  <span class="sw" style=${{ background: u.color || "var(--line-strong)" }}/>
                  <span class="nm"><span>${u.name}</span>${u.mute && I.mute}${u.check_appends && html`<span class="tag-mini">追加</span>`}</span>
                  <span class="cnt">${todayCount.m[u.name] || 0}</span>
                </button>`)}
            </div>
          </div>
          <div class="divider"/>
          <div>
            <h4>动态类型</h4>
            <div class="kinds">
              ${["发帖", "评论", "转发", "追加"].map(k => html`
                <button key=${k} class=${"chip " + (filter.kindOff.has(k) ? "off" : "on")} style=${{ "--c": KINDS[k].fg, "--kbg": KINDS[k].bg }}
                        title=${k} onClick=${() => toggleKind(k)}><i/><span>${k}</span></button>`)}
            </div>
          </div>
          <div class="side-foot">
            轮询间隔 <b>${s.config.poll_interval_seconds || "—"} s</b> · 查追加 <b>${s.config.append_check_interval_seconds || "—"} s</b><br/>
            列表 <b>${s.items.length.toLocaleString()}</b> 条${filtering ? html`，显示 <b>${shown.length}</b>` : ""}
          </div>
        </nav>

        <main class="main" ref=${mainRef}>
          ${s.loaded && !s.connected && !s.quit && html`<div class="banner">已与后台断开，正在重连…（重连后会自动补齐漏掉的动态）</div>`}
          <div class="feed">
            ${!s.loaded && html`<p class="empty">${st.text}</p>`}
            ${s.loaded && s.hasMore && html`
              <div class="older">
                <button class="btn quiet" disabled=${olderBusy} onClick=${loadOlder}>${olderBusy ? "加载中…" : "加载更早的记录"}</button>
              </div>`}
            ${groups.map(([d, list]) => html`
              <${DateGroup} key=${d} date=${d} list=${list} isToday=${d === td} collapsed=${collapsedOf(d)}
                            onToggle=${() => toggleDate(d)} colorOf=${colorOf} openKey=${openKey} setOpenKey=${setOpenKey} newKeys=${newKeys}/>`)}
            ${s.loaded && !s.items.length && html`<p class="empty">还没有任何动态。</p>`}
            ${s.loaded && s.items.length > 0 && !shown.length && html`<p class="empty">当前筛选下没有动态。<button class="link" onClick=${() => setFilter({ user: null, kindOff: new Set() })}>清除筛选</button></p>`}
          </div>
          ${pendingBelow > 0 && html`<button class="fab" onClick=${() => scrollToBottom(true)}>${I.down}<span>${pendingBelow} 条新动态</span></button>`}
        </main>

        ${drawer && html`<${Drawer} users=${s.users} onClose=${() => setDrawer(false)} toast=${toast}/>`}
        <${Toast} msg=${msg}/>
        ${s.quit && html`
          <div class="scrim quit">
            <div class="quit-box">
              <span class="brand-mark">谛听</span>
              <p>程序已退出，可以关掉这个标签页了。<br/>要重新开始监控，请再运行一次 <code>server.py</code>（或双击 run_gui.bat）。</p>
            </div>
          </div>`}
      </div>`;
  }

  ReactDOM.createRoot(document.getElementById("root")).render(html`<${App}/>`);
})();
