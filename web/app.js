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
  const userInitial = name => {
    if (!name) return "?";
    const clean = String(name).trim().replace(/^[@#\s]+/, "");
    const chars = Array.from(clean);
    return (chars[0] || "?").toUpperCase();
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
    expand: svg('<path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/>'),
    shrink: svg('<path d="M3 8h5V3M21 8h-5V3M3 16h5v5M21 16h-5v5"/>'),
    rows: svg('<path d="M4 6h16M4 12h16M4 18h16"/>'),
    cards: svg('<rect x="4" y="4" width="16" height="6" rx="1.5"/><rect x="4" y="14" width="16" height="6" rx="1.5"/>'),
    search: svg('<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.35-4.35"/>'),
    x: svg('<path d="M18 6L6 18M6 6l12 12"/>'),
    volume: svg('<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07M19.07 4.93a10 10 0 0 1 0 14.14"/>'),
    volumeX: svg('<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/>'),
  };

  let audioCtx = null;
  function playDing(volume = 0.5) {
    try {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (!AudioContext) return;
      if (!audioCtx) audioCtx = new AudioContext();
      if (audioCtx.state === "suspended") audioCtx.resume();
      const now = audioCtx.currentTime;
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(880, now);
      osc.frequency.exponentialRampToValueAtTime(1760, now + 0.08);
      const vol = Math.max(0, Math.min(1, volume));
      gain.gain.setValueAtTime(vol * 0.4, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.28);
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.start(now);
      osc.stop(now + 0.3);
    } catch (e) {}
  }

  function highlight(text, kw) {
    if (!kw || !text) return text;
    const str = String(text);
    const escaped = kw.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const parts = str.split(new RegExp(`(${escaped})`, "gi"));
    if (parts.length <= 1) return text;
    return parts.map((part, i) =>
      part.toLowerCase() === kw.toLowerCase() ? html`<mark class="kw" key=${i}>${part}</mark>` : part
    );
  }

  /* ---------- 股票代码/名称自动识别与行情联动增强 ---------- */
  const POPULAR_STOCKS = {
    // 光模块 / CPO / AI硬件
    "中际旭创": "300308", "新易盛": "300502", "天孚通信": "300394", "工业富联": "601138",
    "中科曙光": "603019", "浪潮信息": "000977", "软通动力": "301236", "胜宏科技": "300476",
    // 芯片 / 半导体 / 算力
    "寒武纪": "688256", "海光信息": "688041", "中芯国际": "688981", "北方华创": "002371",
    "兆易创新": "603986", "韦尔股份": "603501", "澜起科技": "688008",
    // 新能源 / 电池 / 汽车
    "宁德时代": "300750", "比亚迪": "002594", "赛力斯": "601127", "长安汽车": "000625",
    "江淮汽车": "600418", "长城汽车": "601633", "亿纬锂能": "300014",
    // 白酒 / 消费
    "贵州茅台": "600519", "五粮液": "000858", "泸州老窖": "000568", "山西汾酒": "600809",
    // 券商 / 金融
    "东方财富": "300059", "同花顺": "300033", "指南针": "300803", "中信证券": "600030",
    "中国平安": "601318", "招商银行": "600036",
    // 消费电子 / PCB / 铜箔材料
    "立讯精密": "002475", "歌尔股份": "002241", "沪电股份": "002463", "生益科技": "600183",
    "铜冠铜箔": "301217", "德福科技": "301511", "致尚科技": "301486", "金牛化工": "600722",
    "莲花控股": "600186", "诺德股份": "600110", "桂林旅游": "000978",
    // 资源 / 红利
    "紫金矿业": "601899", "洛阳钼业": "603993", "长江电力": "600900", "中国神华": "601088",
    "中国移动": "600941", "中国海油": "600938", "药明康德": "603259",
    // 核心指数
    "上证指数": "000001", "沪深300": "000300", "创业板指": "399006", "科创50": "000688"
  };

  function getMarket(code) {
    if (!code || code.length !== 6) return "sh";
    if (/^(?:60[0135]|68[89]|900|51|56|58)/.test(code)) return "sh";
    if (/^(?:00[0123]|30[01]|200|15|16)/.test(code)) return "sz";
    if (/^(?:920|8[378]|43)/.test(code)) return "bj";
    return "sh";
  }

  function getStockQuoteUrl(code, market) {
    const m = (market || getMarket(code)).toLowerCase();
    if (m === "bj") return `https://quote.eastmoney.com/concept/bj${code}.html`;
    return `https://quote.eastmoney.com/${m}${code}.html`;
  }

  function parseStockMatch(matchText, stockDict) {
    if (!matchText) return null;
    // 1. $Name(Code)$ 如 $莲花控股(SH600186)$
    let m = /^\$([^\$\r\n\(\)]+?)\((?:([A-Za-z]{2}))?(\d{6})\)\$$/.exec(matchText);
    if (m) {
      const [, name, market, code] = m;
      return { text: matchText, name: name.trim(), code, market: market || getMarket(code) };
    }
    // 2. Name(Code) / Name（Code） 如 莲花控股(600186) 或 德福科技（SZ301511）
    m = /^([^\r\n\(\)（）]+?)[（\(](?:([A-Za-z]{2}))?(\d{6})[）\)]$/.exec(matchText);
    if (m) {
      const [, name, market, code] = m;
      return { text: matchText, name: name.trim(), code, market: market || getMarket(code) };
    }
    // 3. $Code$ 如 $SH600186$ 或 $600186$
    m = /^\$(?:([A-Za-z]{2}))?(\d{6})\$$/.exec(matchText);
    if (m) {
      const [, market, code] = m;
      return { text: matchText, name: "", code, market: market || getMarket(code) };
    }
    // 4. $Name$ 如 $莲花控股$
    m = /^\$([^\$\r\n]+?)\$$/.exec(matchText);
    if (m && stockDict && stockDict[m[1].trim()]) {
      const name = m[1].trim();
      const code = stockDict[name];
      return { text: matchText, name, code, market: getMarket(code) };
    }
    // 5. SH600186 / SZ301217
    m = /^([A-Za-z]{2})(\d{6})$/.exec(matchText);
    if (m) return { text: matchText, name: "", code: m[2], market: m[1] };
    // 6. 600186.SH / 301217.SZ
    m = /^(\d{6})\.([A-Za-z]{2})$/.exec(matchText);
    if (m) return { text: matchText, name: "", code: m[1], market: m[2] };
    // 7. 纯 6 位 A 股证券代码
    m = /^(\d{6})$/.exec(matchText);
    if (m) return { text: matchText, name: "", code: m[1], market: getMarket(m[1]) };
    // 8. 字典中的股票名称/吧名
    if (stockDict && stockDict[matchText]) {
      const code = stockDict[matchText];
      return { text: matchText, name: matchText, code, market: getMarket(code) };
    }
    return null;
  }

  function buildStockRegex(stockDict) {
    const names = Object.keys(stockDict || {}).filter(k => k.length >= 2).sort((a, b) => b.length - a.length);
    const namePattern = names.map(n => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
    const patterns = [
      "(\\$[^\\$\\r\\n\\(\\)]+?\\((?:[A-Za-z]{2})?\\d{6}\\)\\$)",
      "([\\u4e00-\\u9fa5A-Za-z0-9]{2,8}[（\\(](?:[A-Za-z]{2})?\\d{6}[）\\)])",
      "(\\$[^\\$\\r\\n]{2,12}\\$)",
      "(?<![0-9a-zA-Z])((?:SH|SZ|BJ)\\d{6})(?![0-9a-zA-Z])",
      "(?<![0-9a-zA-Z])(\\d{6}\\.(?:SH|SZ|BJ))(?![0-9a-zA-Z])",
      "(?<![0-9a-zA-Z])(60[0135]\\d{3}|68[89]\\d{3}|00[0123]\\d{3}|30[01]\\d{3}|920\\d{3}|8[37]\\d{4}|43\\d{4}|51\\d{4}|15\\d{4}|16\\d{4})(?![0-9a-zA-Z])"
    ];
    if (namePattern) patterns.push("(" + namePattern + ")");
    return new RegExp(patterns.join("|"), "gi");
  }

  function renderRichContent(text, kw, stockDict, stockRegex) {
    if (!text) return "";
    if (!stockRegex) return highlight(text, kw);

    const elements = [];
    let lastIdx = 0;
    let m;
    stockRegex.lastIndex = 0;

    while ((m = stockRegex.exec(text)) !== null) {
      if (m.index > lastIdx) {
        elements.push(highlight(text.slice(lastIdx, m.index), kw));
      }
      const matchText = m[0];
      const info = parseStockMatch(matchText, stockDict);
      if (info && info.code) {
        const url = getStockQuoteUrl(info.code, info.market);
        const isTag = matchText.startsWith("$") && matchText.endsWith("$");
        const tip = info.name
          ? `查看 ${info.name} (${info.code}) 东方财富个股行情 ↗`
          : `查看 ${info.code} 东方财富个股行情 ↗`;
        elements.push(html`
          <a class=${"stock-link" + (isTag ? " stock-tag" : "")}
             href=${url}
             target="_blank"
             rel="noopener"
             title=${tip}
             key=${m.index}
             onClick=${e => e.stopPropagation()}>
            ${highlight(matchText, kw)}
          </a>
        `);
      } else {
        elements.push(highlight(matchText, kw));
      }
      lastIdx = stockRegex.lastIndex;
    }

    if (lastIdx < text.length) {
      elements.push(highlight(text.slice(lastIdx), kw));
    }

    return elements.length === 1 ? elements[0] : elements;
  }



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
  function Card({ it, color, open, isNew, onToggle, kw, stockDict, stockRegex }) {
    const k = KINDS[it.kind] || KINDS["发帖"];
    const { ctx, body } = useMemo(() => splitCtx(it), [it]);
    const cls = ["card", open && "open", isNew && "new", it.kind === "追加" && "append"].filter(Boolean).join(" ");
    const style = color ? { "--uc": color } : undefined;
    const titleTip = !open ? `${it.name} [${it.kind}] ${it.bar && it.bar !== "—" ? "· " + it.bar : ""}: ${it.content || ""}` : undefined;

    const barCode = useMemo(() => {
      if (it.link) {
        const m = /news,(\d{6})/i.exec(it.link);
        if (m) return m[1];
      }
      if (it.bar && stockDict && stockDict[it.bar]) return stockDict[it.bar];
      return "";
    }, [it.link, it.bar, stockDict]);
    const barUrl = barCode ? getStockQuoteUrl(barCode) : null;

    return html`
      <div class=${cls} style=${style} tabIndex="0" onClick=${onToggle} data-key=${it.key} title=${titleTip}
           onKeyDown=${e => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onToggle())}>
        <div class="stripe"/>
        <div class="body">
          <div class="head">
            <span class="uname">${highlight(it.name, kw)}</span>
            <span class="pill" style=${{ "--kbg": k.bg, "--kfg": k.fg }}>${it.kind}</span>
            ${it.bar && it.bar !== "—" && (barUrl ? html`
              <a class="bar stock-bar-link" href=${barUrl} target="_blank" rel="noopener"
                 title=${"查看 " + it.bar + " 东方财富行情 ↗"} onClick=${e => e.stopPropagation()}>
                ${highlight(it.bar, kw)}
              </a>` : html`<span class="bar">${highlight(it.bar, kw)}</span>`)}
            <time class="t" dateTime=${it.time}>${it.time.slice(11, 16)}</time>
          </div>
          ${ctx && html`<div class="ctx">${ctx.label}${ctx.title && html`<b>《${renderRichContent(ctx.title, kw, stockDict, stockRegex)}》</b>`}</div>`}
          <div class="txt">${renderRichContent(body, kw, stockDict, stockRegex)}</div>
          ${open && html`
            <div class="foot">
              ${it.link && html`<a href=${it.link} target="_blank" rel="noopener" onClick=${e => e.stopPropagation()}>打开原帖 ↗</a>`}
              ${barUrl && html`<a href=${barUrl} target="_blank" rel="noopener" onClick=${e => e.stopPropagation()}>行情 ↗</a>`}
              <span>${it.time}</span>
              <span>${it.key}</span>
            </div>`}
        </div>
      </div>`;
  }

  function UnreadDivider({ time, onClear }) {
    return html`
      <div class="unread-divider" role="separator" aria-label="上次看到这里">
        <span class="line"/>
        <span class="badge">🔴 上次看到这里${time ? " (" + time + ")" : ""}</span>
        <span class="line"/>
        <button class="clear-btn" title="清除未读标记" onClick=${onClear}>✕</button>
      </div>`;
  }

  function DateGroup({ date, list, isToday, collapsed, onToggle, colorOf, openKey, setOpenKey, newKeys, kw, firstUnreadKey, dividerTime, onClearDivider, stockDict, stockRegex }) {
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
              <${React.Fragment} key=${it.key}>
                ${it.key === firstUnreadKey && html`<${UnreadDivider} time=${dividerTime} onClear=${onClearDivider}/>`}
                <${Card} it=${it} color=${colorOf(it.name)} open=${openKey === it.key} isNew=${newKeys.has(it.key)}
                         kw=${kw} stockDict=${stockDict} stockRegex=${stockRegex}
                         onToggle=${() => setOpenKey(k => (k === it.key ? null : it.key))}/>
              </${React.Fragment}>`)}
          </div>`}
      </section>`;
  }


  function Toast({ msg }) {
    return msg ? html`<div class=${"toast" + (msg.kind === "error" ? " error" : "")} role="status">${msg.text}</div>` : null;
  }

  function Drawer({ users, config, sound, onToggleSound, onClose, onSaved, toast }) {
    const [pend, setPend] = useState(() => Object.fromEntries(users.map(u => [String(u.uid), { ...u }])));
    const [pollSec, setPollSec] = useState(config.poll_interval_seconds || 60);
    const [appendSec, setAppendSec] = useState(config.append_check_interval_seconds || 300);
    const [uidInput, setUidInput] = useState("");
    const [nameInput, setNameInput] = useState("");
    const [probing, setProbing] = useState(false);
    const [adding, setAdding] = useState(false);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
      setPend(Object.fromEntries(users.map(u => [String(u.uid), { ...u }])));
    }, [users]);

    const upd = (uid, patch) => setPend(p => ({ ...p, [uid]: { ...p[uid], ...patch } }));

    useEffect(() => {
      const h = e => e.key === "Escape" && onClose();
      window.addEventListener("keydown", h); return () => window.removeEventListener("keydown", h);
    }, [onClose]);

    const probeUser = async () => {
      const u = uidInput.trim();
      if (!u || !/^\d+$/.test(u)) return toast("请输入合法的纯数字 UID", "error");
      setProbing(true);
      try {
        const r = await fetch("/api/probe_user?uid=" + encodeURIComponent(u));
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || "探测失败");
        setNameInput(d.user.name || "");
        toast("已获取到昵称: " + d.user.name);
      } catch (e) {
        toast("获取昵称失败：" + e.message, "error");
      } finally { setProbing(false); }
    };

    const addUser = async () => {
      const u = uidInput.trim();
      if (!u || !/^\d+$/.test(u)) return toast("请输入合法的纯数字 UID", "error");
      if (users.some(x => String(x.uid) === u)) return toast("该 UID 已经在关注列表中", "error");
      setAdding(true);
      try {
        await post("/api/users/manage", {
          action: "add",
          user: { uid: u, name: nameInput.trim() || undefined }
        });
        toast("已成功添加关注用户");
        setUidInput("");
        setNameInput("");
        onSaved && onSaved();
      } catch (e) {
        toast("添加失败：" + e.message, "error");
      } finally { setAdding(false); }
    };

    const delUser = async (u) => {
      if (!window.confirm("确定取消关注 " + u.name + " (" + u.uid + ")？")) return;
      try {
        await post("/api/users/manage", { action: "delete", uid: String(u.uid) });
        toast("已取消关注 " + u.name);
        onSaved && onSaved();
      } catch (e) {
        toast("删除失败：" + e.message, "error");
      }
    };

    const save = async () => {
      setSaving(true);
      try {
        await post("/api/users/manage", {
          action: "update_all",
          users: Object.values(pend).map(u => ({
            uid: String(u.uid),
            name: (u.name || "").trim(),
            color: u.color || null,
            mute: !!u.mute,
            check_appends: !!u.check_appends
          })),
          config: {
            poll_interval_seconds: Number(pollSec) || 60,
            append_check_interval_seconds: Number(appendSec) || 300
          }
        });
        toast("已保存用户设置与参数");
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
            <h3>用户设置与参数</h3>
            <p>增删监控 · 配色 · 静音 · 查追加 · 轮询周期</p>
          </div>
          <span class="spacer"/>
          <button class="btn quiet" onClick=${onClose}>关闭</button>
        </header>

        <div class="add-box">
          <h4>+ 添加监控用户</h4>
          <div class="add-form">
            <input class="input-text uid-input" placeholder="输入用户数字 UID" value=${uidInput}
                   onInput=${e => setUidInput(e.target.value)} onKeyDown=${e => e.key === "Enter" && probeUser()}/>
            <button class="btn quiet" disabled=${probing} onClick=${probeUser}>${probing ? "检测中…" : "检测昵称"}</button>
            <input class="input-text name-input" placeholder="备注名（选填）" value=${nameInput}
                   onInput=${e => setNameInput(e.target.value)} onKeyDown=${e => e.key === "Enter" && addUser()}/>
            <button class="btn primary" disabled=${adding} onClick=${addUser}>${adding ? "添加中…" : "确认添加"}</button>
          </div>
        </div>

        <div class="legend">
          ${PALETTE.map(([nm, hx]) => html`<span key=${hx}><i style=${{ "--c": hx }}/>${nm}</span>`)}
        </div>

        <div class="list">
          ${users.map(u => {
            const uidStr = String(u.uid);
            const p = pend[uidStr] || u;
            return html`
              <div class="srow" key=${uidStr} style=${p.color ? { "--uc": p.color } : undefined}>
                <div class="who">
                  <input class="uname-edit" value=${p.name} title="点击直接修改备注名"
                         onInput=${e => upd(uidStr, { name: e.target.value })}/>
                  <span>${u.uid}</span>
                </div>
                <div class="swatches">
                  <button class=${"swatch none" + (!p.color ? " on" : "")} title="默认（按类型配色）"
                          onClick=${() => upd(uidStr, { color: null })}>默认</button>
                  ${PALETTE.map(([nm, hx]) => html`
                    <button key=${hx} class=${"swatch" + ((p.color || "").toLowerCase() === hx.toLowerCase() ? " on" : "")} title=${nm}
                            style=${{ "--c": hx }} onClick=${() => upd(uidStr, { color: hx })}/>`)}
                </div>
                <div class="toggles">
                  <label class=${"tg" + (p.mute ? " on" : "")} onClick=${() => upd(uidStr, { mute: !p.mute })}><i/>静音</label>
                  ${u.check_appends !== null && u.check_appends !== undefined && html`
                    <label class=${"tg" + (p.check_appends ? " on" : "")} onClick=${() => upd(uidStr, { check_appends: !p.check_appends })}><i/>查追加</label>`}
                  <button class="btn-del" title="取消关注该用户" onClick=${() => delUser(u)}>${I.trash}</button>
                </div>
              </div>`;
          })}
          ${!users.length && html`<p class="empty">暂未关注任何用户，请在上方输入 UID 添加。</p>`}
        </div>

        <div class="cfg-box">
          <h4>运行与提醒参数</h4>
          <div class="cfg-row">
            <label>轮询间隔: <input type="number" class="input-text" min="10" max="3600" value=${pollSec} onInput=${e => setPollSec(e.target.value)}/> 秒</label>
            <label>查追加间隔: <input type="number" class="input-text" min="30" max="7200" value=${appendSec} onInput=${e => setAppendSec(e.target.value)}/> 秒</label>
          </div>
          <div class="cfg-row" style=${{ marginTop: "10px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <label style=${{ display: "inline-flex", alignItems: "center", gap: "6px", cursor: "pointer" }}>
              <input type="checkbox" checked=${sound} onChange=${e => onToggleSound && onToggleSound(e.target.checked)}/>
              <span>后台盯盘声音提示（清脆音）</span>
            </label>
            <button class="btn quiet" style=${{ padding: "3px 10px", fontSize: "12px" }} onClick=${() => playDing(0.6)}>${I.volume} 试听</button>
          </div>
        </div>

        <footer>
          <button class="btn" onClick=${onClose}>取消</button>
          <button class="btn primary" disabled=${saving} onClick=${save}>${saving ? "保存中…" : "保存设置"}</button>
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
    const [dense, setDense] = useState(() => store.get("diting.dense", false));
    useEffect(() => store.set("diting.dense", dense), [dense]);
    const toggleDense = () => setDense(d => !d);

    // 全屏：平板/手机浏览器地址栏收不掉，用 Fullscreen API 兜底。iPad Safari 只认 webkit 前缀，
    // 已经是「添加到主屏幕」独立窗口（standalone）时没有地址栏、也没这个 API，按钮不显示。
    const fsDoc = document;
    const fsSupported = !!(fsDoc.fullscreenEnabled || fsDoc.webkitFullscreenEnabled);
    const isFs = () => !!(fsDoc.fullscreenElement || fsDoc.webkitFullscreenElement);
    const [fs, setFs] = useState(isFs);
    useEffect(() => {
      const on = () => setFs(isFs());
      fsDoc.addEventListener("fullscreenchange", on);
      fsDoc.addEventListener("webkitfullscreenchange", on);
      return () => { fsDoc.removeEventListener("fullscreenchange", on); fsDoc.removeEventListener("webkitfullscreenchange", on); };
    }, []);
    const toggleFs = () => {
      const fail = () => toast("浏览器不允许网页全屏，试试「添加到主屏幕」", "error");
      try {
        // 标准 API 返回 Promise、被拒绝时 reject；webkit 前缀版同步执行，没返回值
        const p = isFs()
          ? (fsDoc.exitFullscreen || fsDoc.webkitExitFullscreen).call(fsDoc)
          : (el => (el.requestFullscreen || el.webkitRequestFullscreen).call(el))(fsDoc.documentElement);
        if (p && p.catch) p.catch(fail);
      } catch (e) { fail(); }
    };

    const [sound, setSound] = useState(() => store.get("diting.sound", true));
    useEffect(() => store.set("diting.sound", sound), [sound]);
    const toggleSound = () => setSound(s => !s);
    const soundRef = useRef(sound); soundRef.current = sound;
    const usersRef = useRef(s.users); usersRef.current = s.users;

    const lastSeenKeyRef = useRef(null);
    const leaveTimeRef = useRef(null);
    const [firstUnreadKey, setFirstUnreadKey] = useState(null);
    const [dividerTime, setDividerTime] = useState(null);

    // 监听页面可见性：切走时记录最新条目的 key；切回时若有新动态，标记未读断点
    useEffect(() => {
      const onVisibility = () => {
        if (document.visibilityState === "hidden") {
          const latest = s.items[s.items.length - 1];
          if (latest) {
            lastSeenKeyRef.current = latest.key;
            leaveTimeRef.current = new Date().toTimeString().slice(0, 5);
          }
        } else if (document.visibilityState === "visible") {
          setUnread(0);
          if (lastSeenKeyRef.current && s.items.length > 0) {
            const idx = s.items.findIndex(i => i.key === lastSeenKeyRef.current);
            if (idx !== -1 && idx < s.items.length - 1) {
              setFirstUnreadKey(s.items[idx + 1].key);
              setDividerTime(leaveTimeRef.current || new Date().toTimeString().slice(0, 5));
            }
          }
          const latest = s.items[s.items.length - 1];
          if (latest) lastSeenKeyRef.current = latest.key;
        }
      };
      document.addEventListener("visibilitychange", onVisibility);
      return () => document.removeEventListener("visibilitychange", onVisibility);
    }, [s.items]);

    useEffect(() => {
      if (document.visibilityState === "visible" && s.items.length > 0 && !firstUnreadKey) {
        lastSeenKeyRef.current = s.items[s.items.length - 1].key;
      }
    }, [s.items, firstUnreadKey]);

    const [query, setQuery] = useState("");
    const [searchDb, setSearchDb] = useState(null);
    const [searchBusy, setSearchBusy] = useState(false);
    const searchInputRef = useRef(null);

    useEffect(() => {
      const h = e => {
        if (e.key === "/" && document.activeElement && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) {
          e.preventDefault();
          searchInputRef.current && searchInputRef.current.focus();
        }
      };
      window.addEventListener("keydown", h);
      return () => window.removeEventListener("keydown", h);
    }, []);

    const doDbSearch = async () => {
      const q = query.trim();
      if (!q) return;
      setSearchBusy(true);
      try {
        const r = await fetch("/api/search?q=" + encodeURIComponent(q) + "&limit=200");
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || "搜索失败");
        setSearchDb(d.items || []);
        if (!(d.items || []).length) toast("未在历史库中找到关于 \"" + q + "\" 的动态");
      } catch (e) {
        toast("搜索失败: " + e.message, "error");
      } finally { setSearchBusy(false); }
    };

    const clearSearch = () => { setQuery(""); setSearchDb(null); };

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
      if (soundRef.current) {
        const isBackground = document.visibilityState !== "visible" || !document.hasFocus();
        if (isBackground) {
          const users = usersRef.current || [];
          const hasAudible = fresh.some(e => {
            const u = users.find(x => x.name === e.name);
            return !u || !u.mute;
          });
          if (hasAudible) {
            playDing(0.6);
          }
        }
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

    const colorMap = useMemo(() => Object.fromEntries(s.users.filter(u => u.color).map(u => [u.name, u.color])), [s.users]);
    const colorOf = useCallback(n => colorMap[n], [colorMap]);
    const todayCount = useMemo(() => {
      const m = {}; let all = 0;
      for (const i of s.items) if (i.time.startsWith(td)) { m[i.name] = (m[i.name] || 0) + 1; all++; }
      return { m, all };
    }, [s.items, td]);

    const activeQuery = query.trim();
    const shown = useMemo(() => s.items.filter(i => matches(i, filter)), [s.items, filter]);

    const filteredShown = useMemo(() => {
      if (searchDb !== null) return searchDb;
      if (!activeQuery) return shown;
      const q = activeQuery.toLowerCase();
      return shown.filter(i =>
        (i.content && i.content.toLowerCase().includes(q)) ||
        (i.name && i.name.toLowerCase().includes(q)) ||
        (i.bar && i.bar.toLowerCase().includes(q))
      );
    }, [searchDb, activeQuery, shown]);

    // 按日期分组，升序：旧日期在上、今天在最下面
    const groups = useMemo(() => {
      const g = new Map();
      for (const i of filteredShown) {
        const d = i.time.slice(0, 10) || "未知日期";
        if (!g.has(d)) g.set(d, []);
        g.get(d).push(i);
      }
      return [...g.entries()].sort((a, b) => a[0].localeCompare(b[0]));
    }, [filteredShown]);

    const collapsedOf = d => {
      if (activeQuery || searchDb !== null) return false;
      if (firstUnreadKey && groups.some(([gDate, gList]) => gDate === d && gList.some(it => it.key === firstUnreadKey))) return false;
      return toggled.has(d) ? toggled.get(d) : d !== td;
    };
    const toggleDate = d => setToggled(m => { const n = new Map(m); n.set(d, !collapsedOf(d)); return n; });
    const setUser = u => setFilter(f => ({ ...f, user: f.user === u ? null : u }));
    const toggleKind = k => setFilter(f => { const n = new Set(f.kindOff); n.has(k) ? n.delete(k) : n.add(k); return { ...f, kindOff: n }; });
    const filtering = filter.user || filter.kindOff.size > 0;

    const stockDict = useMemo(() => {
      const dict = { ...POPULAR_STOCKS };
      for (const it of s.items) {
        if (!it) continue;
        let code = "";
        if (it.link) {
          const m = /news,(\d{6})/i.exec(it.link);
          if (m) code = m[1];
        }
        if (it.bar && it.bar !== "—") {
          if (code) {
            dict[it.bar] = code;
            if (it.bar.endsWith("吧")) {
              const nm = it.bar.slice(0, -1);
              if (nm.length >= 2) dict[nm] = code;
            }
          }
        }
        if (it.content) {
          const tagRe = /\$([^\$\r\n\(\)]+?)\((?:[A-Za-z]{2})?(\d{6})\)\$/g;
          let tm;
          while ((tm = tagRe.exec(it.content)) !== null) {
            const nm = tm[1].trim();
            const c = tm[2];
            if (nm.length >= 2 && nm.length <= 8) {
              dict[nm] = c;
              dict[nm + "吧"] = c;
            }
          }
        }
      }
      return dict;
    }, [s.items]);

    const stockRegex = useMemo(() => buildStockRegex(stockDict), [stockDict]);

    const st = s.status;
    return html`
      <div class=${"app" + (rail ? " rail" : "") + (dense ? " dense" : "")}>
        <header class="topbar">
          <div class="brand">
            <button class="iconbtn" title=${rail ? "展开侧栏" : "收起侧栏"} onClick=${() => setRail(r => !r)}>${I.menu}</button>
            <span class="brand-mark">谛听</span>
          </div>
          <div class="status" title=${st.text}>
            <span class=${"dot" + (st.running && s.connected ? "" : " paused")}/>
            <span class="lbl">
              <span class="lbl-main">${!s.connected ? "已断开" : st.running ? "运行中" : "已停止"}</span>
              ${st.last_check && html`<span class="lbl-sub"> · 上次检查</span>`}
            </span>
            ${st.last_check && html` <time>${st.last_check}</time>`}
          </div>

          <div class="search-wrap">
            <span class="search-icon">${I.search}</span>
            <input ref=${searchInputRef} class="search-input" placeholder="搜索关键词、股票或博主... (/)"
                   value=${query}
                   onInput=${e => { setQuery(e.target.value); if (searchDb !== null) setSearchDb(null); }}
                   onKeyDown=${e => {
                     if (e.key === "Enter") doDbSearch();
                     if (e.key === "Escape") { clearSearch(); searchInputRef.current && searchInputRef.current.blur(); }
                   }}/>
            ${query && html`<button class="search-clear" title="清空搜索 (Esc)" onClick=${clearSearch}>${I.x}</button>`}
          </div>

          <span class="spacer"/>
          <div class="tb-actions">
            <button class=${"btn quiet only-wide" + (sound ? "" : " muted")}
                    title=${sound ? "声音提示：开启（后台有新动态时响铃）" : "声音提示：关闭（点击开启）"}
                    onClick=${toggleSound}>
              ${sound ? I.volume : I.volumeX}
            </button>
            <button class=${"btn quiet only-wide" + (dense ? " active" : "")} title=${dense ? "切到标准卡片模式" : "切到紧凑单行模式"} onClick=${toggleDense}>
              ${dense ? I.cards : I.rows}
            </button>
            <button class="btn" title=${st.running ? "停止监控" : "开始监控"} onClick=${act.toggleRun} disabled=${!s.connected}>
              ${st.running ? I.pause : I.play}<span class="lbl">${st.running ? "停止监控" : "开始监控"}</span>
            </button>
            <button class="btn" title="用户设置" onClick=${() => setDrawer(true)} disabled=${!s.loaded}>${I.gear}<span class="lbl">设置</span></button>
            <button class="btn quiet only-wide" title="测试通知" onClick=${act.test}>${I.bell}</button>
            <button class="btn quiet only-wide" title="清空列表" onClick=${act.clear}>${I.trash}</button>
            <button class="btn quiet only-wide" title=${isDarkNow(theme) ? "切到浅色" : "切到深色"} onClick=${toggleTheme}>${isDarkNow(theme) ? I.sun : I.moon}</button>
            ${fsSupported && html`<button class="btn quiet only-wide" title=${fs ? "退出全屏" : "全屏（平板盯盘去掉地址栏）"} onClick=${toggleFs}>${fs ? I.shrink : I.expand}</button>`}
            <button class="btn quiet only-wide" title="退出程序" onClick=${act.quit}>${I.power}</button>
            <div class="menu-wrap only-narrow">
              <button class="btn quiet" title="更多" onClick=${e => { e.stopPropagation(); setMenu(m => !m); }}>${I.more}</button>
              ${menu && html`
                <div class="menu" onClick=${e => e.stopPropagation()}>
                  <button onClick=${() => { setMenu(false); toggleSound(); }}>${sound ? I.volume : I.volumeX}${sound ? "声音提示：开" : "声音提示：关"}</button>
                  <button onClick=${() => { setMenu(false); toggleDense(); }}>${dense ? I.cards : I.rows}${dense ? "卡片模式" : "紧凑模式"}</button>
                  <button onClick=${() => { setMenu(false); act.test(); }}>${I.bell}测试通知</button>
                  <button onClick=${() => { setMenu(false); act.clear(); }}>${I.trash}清空列表</button>
                  <button onClick=${() => { setMenu(false); toggleTheme(); }}>${isDarkNow(theme) ? I.sun : I.moon}${isDarkNow(theme) ? "浅色模式" : "深色模式"}</button>
                  ${fsSupported && html`<button onClick=${() => { setMenu(false); toggleFs(); }}>${fs ? I.shrink : I.expand}${fs ? "退出全屏" : "全屏"}</button>`}
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
                <span class="sw all" style=${{ background: "var(--line-strong)" }}>全</span>
                <span class="nm"><span>全部</span></span>
                <span class="cnt">${todayCount.all}</span>
              </button>
              ${s.users.map(u => html`
                <button key=${u.uid + u.name} class=${"urow" + (filter.user === u.name ? " on" : "")}
                        title=${u.name + "（今日 " + (todayCount.m[u.name] || 0) + " 条）"} onClick=${() => setUser(u.name)}>
                  <span class=${"sw" + (!u.color ? " default-bg" : "")} style=${{ background: u.color || "var(--line-strong)" }}>
                    ${userInitial(u.name)}
                  </span>
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
            列表 <b>${s.items.length.toLocaleString()}</b> 条${filtering ? html`，显示 <b>${filteredShown.length}</b>` : ""}
          </div>
        </nav>

        <main class="main" ref=${mainRef}>
          ${s.loaded && !s.connected && !s.quit && html`<div class="banner">已与后台断开，正在重连…（重连后会自动补齐漏掉的动态）</div>`}
          <div class="feed">
            ${searchDb !== null && html`
              <div class="search-banner">
                <span>在历史数据库中找到 <b>${searchDb.length}</b> 条关于 "<b>${query}</b>" 的记录</span>
                <button class="btn quiet" onClick=${clearSearch}>返回全部动态</button>
              </div>`}
            ${searchDb === null && activeQuery && html`
              <div class="search-banner">
                <span>当前已加载列表中匹配到 <b>${filteredShown.length}</b> 条关于 "<b>${query}</b>" 的动态</span>
                <button class="btn quiet" disabled=${searchBusy} onClick=${doDbSearch}>${searchBusy ? "检索中…" : "检索全库历史 ↵"}</button>
              </div>`}

            ${!s.loaded && html`<p class="empty">${st.text}</p>`}
            ${s.loaded && s.hasMore && searchDb === null && !activeQuery && html`
              <div class="older">
                <button class="btn quiet" disabled=${olderBusy} onClick=${loadOlder}>${olderBusy ? "加载中…" : "加载更早的记录"}</button>
              </div>`}
            ${groups.map(([d, list]) => html`
              <${DateGroup} key=${d} date=${d} list=${list} isToday=${d === td} collapsed=${collapsedOf(d)}
                            onToggle=${() => toggleDate(d)} colorOf=${colorOf} openKey=${openKey} setOpenKey=${setOpenKey} newKeys=${newKeys} kw=${activeQuery}
                            firstUnreadKey=${firstUnreadKey} dividerTime=${dividerTime} onClearDivider=${() => setFirstUnreadKey(null)}
                            stockDict=${stockDict} stockRegex=${stockRegex}/>`)}
            ${s.loaded && !s.items.length && html`<p class="empty">还没有任何动态。</p>`}
            ${s.loaded && s.items.length > 0 && !filteredShown.length && html`<p class="empty">当前筛选/搜索下没有动态。<button class="link" onClick=${() => { clearSearch(); setFilter({ user: null, kindOff: new Set() }); }}>清除筛选与搜索</button></p>`}
          </div>
          ${pendingBelow > 0 && html`<button class="fab" onClick=${() => scrollToBottom(true)}>${I.down}<span>${pendingBelow} 条新动态</span></button>`}
        </main>

        ${drawer && html`<${Drawer} users=${s.users} config=${s.config} sound=${sound} onToggleSound=${setSound} onClose=${() => setDrawer(false)} onSaved=${() => fetchSnapshot(dispatch).catch(() => {})} toast=${toast}/>`}
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
