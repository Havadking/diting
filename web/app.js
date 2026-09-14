/* 谛听 · 网页版前端。React 18 + htm（标签模板，零构建），全局 React / ReactDOM / htm 由 index.html 先加载。
 * 数据流：启动 GET /api/snapshot → 整体替换；之后 EventSource(/api/events) 增量追加。见 docs/web-design.md §6。 */
(function () {
  "use strict";
  const { useState, useEffect, useMemo, useReducer, useRef, useCallback } = React;
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
                    users: [], config: {}, connected: false, loaded: false };
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
        return { ...s, items, keys: new Set(items.map(i => i.key)), status: a.data.status || s.status,
                 users: a.data.users || [], config: a.data.config || {}, loaded: true };
      }
      case "append": {
        const m = mergeItems(s.items, s.keys, a.items);
        return m ? { ...s, ...m } : s;
      }
      case "status": return { ...s, status: a.status };
      case "cleared": return { ...s, items: [], keys: new Set() };
      case "connected": return { ...s, connected: a.value };
      default: return s;
    }
  }

  async function fetchSnapshot(dispatch) {
    const r = await fetch("/api/snapshot", { cache: "no-store" });
    if (!r.ok) throw new Error("snapshot " + r.status);
    dispatch({ type: "snapshot", data: await r.json() });
  }

  /* ---------- 组件 ---------- */
  function Card({ it, color, open, onToggle }) {
    const k = KINDS[it.kind] || KINDS["发帖"];
    const { ctx, body } = useMemo(() => splitCtx(it), [it]);
    const cls = ["card", open && "open", it.kind === "追加" && "append"].filter(Boolean).join(" ");
    const style = color ? { "--uc": color } : undefined;
    return html`
      <div class=${cls} style=${style} tabIndex="0" onClick=${onToggle}
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

  function DateGroup({ date, list, isToday, collapsed, onToggle, colorOf, openKey, setOpenKey }) {
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
              <${Card} key=${it.key} it=${it} color=${colorOf(it.name)} open=${openKey === it.key}
                       onToggle=${() => setOpenKey(k => (k === it.key ? null : it.key))}/>`)}
          </div>`}
      </section>`;
  }

  function App() {
    const [s, dispatch] = useReducer(reducer, initial);
    const [rail, setRail] = useState(() => {
      try { const v = localStorage.getItem("diting.rail"); if (v !== null) return v === "1"; } catch (e) {}
      return window.innerWidth < 900;
    });
    const [openKey, setOpenKey] = useState(null);
    const mainRef = useRef(null);
    const td = today();

    useEffect(() => { try { localStorage.setItem("diting.rail", rail ? "1" : "0"); } catch (e) {} }, [rail]);

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
      const on = (t, fn) => es.addEventListener(t, e => { try { fn(JSON.parse(e.data)); } catch (err) {} });
      on("history", d => dispatch({ type: "append", items: d }));
      on("new", d => dispatch({ type: "append", items: d }));
      on("status", d => dispatch({ type: "status", status: d }));
      on("cleared", () => dispatch({ type: "cleared" }));
      return () => { alive = false; es.close(); };
    }, []);

    // 首屏加载完滚到底（最新在最下面，和 tkinter 版一致）
    useEffect(() => {
      if (s.loaded && mainRef.current) mainRef.current.scrollTop = mainRef.current.scrollHeight;
    }, [s.loaded]);

    const colorMap = useMemo(() => Object.fromEntries(s.users.filter(u => u.color).map(u => [u.name, u.color])), [s.users]);
    const colorOf = useCallback(n => colorMap[n], [colorMap]);
    const todayCount = useMemo(() => {
      const m = {}; let all = 0;
      for (const i of s.items) if (i.time.startsWith(td)) { m[i.name] = (m[i.name] || 0) + 1; all++; }
      return { m, all };
    }, [s.items, td]);
    // 按日期分组，升序：旧日期在上、今天在最下面
    const groups = useMemo(() => {
      const g = new Map();
      for (const i of s.items) { const d = i.time.slice(0, 10) || "未知日期"; if (!g.has(d)) g.set(d, []); g.get(d).push(i); }
      return [...g.entries()].sort((a, b) => a[0].localeCompare(b[0]));
    }, [s.items]);
    // 非今天的日期默认折叠；用户点过的以用户为准
    const [userToggled, setUserToggled] = useState(() => new Map());
    const collapsedOf = d => (userToggled.has(d) ? userToggled.get(d) : d !== td);
    const toggleDate = d => setUserToggled(m => { const n = new Map(m); n.set(d, !collapsedOf(d)); return n; });

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
            ${/* 控制按钮在阶段 5 接上 /api/control，这里先占位 */ null}
            <button class="btn" title=${st.running ? "停止监控" : "开始监控"} disabled>
              ${st.running ? I.pause : I.play}<span class="lbl">${st.running ? "停止监控" : "开始监控"}</span>
            </button>
            <button class="btn" title="用户设置" disabled>${I.gear}<span class="lbl">设置</span></button>
          </div>
        </header>

        <nav class="side">
          <div>
            <h4>监控用户</h4>
            <div class="ulist">
              <button class="urow on" title="全部用户">
                <span class="sw" style=${{ background: "var(--line-strong)" }}/>
                <span class="nm"><span>全部</span></span>
                <span class="cnt">${todayCount.all}</span>
              </button>
              ${s.users.map(u => html`
                <button key=${u.uid + u.name} class="urow" title=${u.name + "（今日 " + (todayCount.m[u.name] || 0) + " 条）"}>
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
                <button key=${k} class="chip on" style=${{ "--c": KINDS[k].fg, "--kbg": KINDS[k].bg }} title=${k}><i/><span>${k}</span></button>`)}
            </div>
          </div>
          <div class="side-foot">
            轮询间隔 <b>${s.config.poll_interval_seconds || "—"} s</b> · 查追加 <b>${s.config.append_check_interval_seconds || "—"} s</b><br/>
            列表 <b>${s.items.length.toLocaleString()}</b> 条
          </div>
        </nav>

        <main class="main" ref=${mainRef}>
          <div class="feed">
            ${!s.loaded && html`<p style=${{ color: "var(--ink-3)", padding: "40px 0", textAlign: "center" }}>${st.text}</p>`}
            ${groups.map(([d, list]) => html`
              <${DateGroup} key=${d} date=${d} list=${list} isToday=${d === td} collapsed=${collapsedOf(d)}
                            onToggle=${() => toggleDate(d)} colorOf=${colorOf} openKey=${openKey} setOpenKey=${setOpenKey}/>`)}
            ${s.loaded && !s.items.length && html`<p style=${{ color: "var(--ink-3)", padding: "40px 0", textAlign: "center" }}>还没有任何动态。</p>`}
          </div>
        </main>
      </div>`;
  }

  ReactDOM.createRoot(document.getElementById("root")).render(html`<${App}/>`);
})();
