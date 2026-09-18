# -*- coding: utf-8 -*-
"""
谛听 · 大盘条数据源

抓腾讯公开行情：三大指数（+沪深300）实时价、两市成交额，以及「相对昨日同时段」的放量/缩量。
设计与口径见 docs/market-strip-design.md。

结构：
  - 纯函数：fetch_quotes() / fetch_kline5() / fetch_kline_daily() / phase_of() / build_payload()，不碰线程和 core
  - MarketFeed：独立后台线程，按交易阶段决定轮询节奏，拉到新数据就通过传入的 broadcast
    回调发一个 ("market", payload) 事件；latest 属性给 /api/snapshot 用
  - MockMarketFeed：--mock 用，数据全在本地随机造，不联网

跟股吧监控线程完全独立：监控停了大盘条照样刷；服务一起来它就跑。

数据源选腾讯而不是东财：东财 push2 行情集群会在连续请求几十次后直接断连（TLS 层 reset，
连 curl 都 000），换了 IP 也一样；腾讯 qt.gtimg.cn / ifzq.gtimg.cn 不要 Referer、不限频、数字与东财一致。
"""
import json
import random
import re
import threading
import time
import urllib.request
from datetime import datetime, timedelta

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# (腾讯代码, 纯数字代码, 条上显示的短名)。顺序 = 条上从左到右；短名自己给，接口返回的全名放不下。
INDICES = [
    ("sh000001", "000001", "上证"),
    ("sz399001", "399001", "深成"),
    ("sz399006", "399006", "创业板"),
    ("sh000300", "000300", "沪深300"),
]
# 「两市」= 上证指数 + 深证成指（沪市 + 深市，与行情软件首页口径一致，不含北交所）。
# 成交额（万亿）用来显示，成交量（手）用来算放量/缩量——「量」本来就指成交量。
MARKET_SYMS = ("sh000001", "sz399001")

QUOTE_URL = "https://qt.gtimg.cn/q=%s"                                  # 实时快照，GBK，一行一个
KLINE5_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline?param=%s,m5,,%d"   # 5 分钟 K，跨天
DAILY_URL = "https://ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,%d,qfq"  # 日 K

VOL_THRESHOLD = 0.10     # 放量/缩量的判定阈值（±10%），先写死，看一周数据再决定要不要做成配置
PRE_OPEN = (9, 15)       # 集合竞价起点；用 9:15 而不是 9:30，让盘前也能看到指数在动


# ---------- 抓取 ----------
def _get(url, encoding="utf-8"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode(encoding, "ignore")


_QUOTE_LINE = re.compile(r'v_s_(\w+)="([^"]*)"')


def fetch_quotes():
    """
    实时快照。返回 {sym: {"code","nm","px","chg","vol","amt"}}，vol 单位手、amt 单位元。
    腾讯 s_ 简版字段（~ 分隔）：1 名称 2 代码 3 现价 4 涨跌额 5 涨跌幅% 6 成交量(手) 7 成交额(万元)
    """
    body = _get(QUOTE_URL % ",".join("s_" + s for s, _, _ in INDICES), encoding="gbk")
    raw = dict(_QUOTE_LINE.findall(body))
    out = {}
    for sym, code, nm in INDICES:
        f = (raw.get(sym) or "").split("~")
        if len(f) < 8:
            continue
        try:
            out[sym] = {"code": code, "nm": nm, "px": float(f[3]), "chg": float(f[5]),
                        "vol": float(f[6] or 0), "amt": float(f[7] or 0) * 1e4}
        except ValueError:
            continue
    if len(out) < 3:
        raise RuntimeError("行情接口只返回了 %d 个指数" % len(out))
    return out


def fetch_kline5(sym, lmt=100):
    """5 分钟 K 线，跨天。返回 [("YYYY-MM-DD HH:MM", 成交量手), ...]，lmt=100 约覆盖两个交易日（每天 48 根）。"""
    data = json.loads(_get(KLINE5_URL % (sym, lmt)))
    rows = []
    for r in (data.get("data") or {}).get(sym, {}).get("m5") or []:
        try:
            ts = str(r[0])   # 202609181500
            rows.append(("%s-%s-%s %s:%s" % (ts[:4], ts[4:6], ts[6:8], ts[8:10], ts[10:12]), float(r[5])))
        except (IndexError, ValueError, TypeError):
            continue
    if not rows:
        raise RuntimeError("%s 的 5 分钟 K 线为空" % sym)
    return rows


def fetch_kline_daily(sym, lmt=3):
    """日 K，用来判断最近一个交易日是哪天（法定节假日落在工作日时靠它兜底）。返回 [("YYYY-MM-DD", 成交量手), ...]。"""
    data = json.loads(_get(DAILY_URL % (sym, lmt)))
    d = (data.get("data") or {}).get(sym, {})
    rows = []
    for r in d.get("day") or d.get("qfqday") or []:
        try:
            rows.append((str(r[0]), float(r[5])))
        except (IndexError, ValueError, TypeError):
            continue
    return rows


# ---------- 交易阶段 ----------
def phase_of(now):
    """只按本机时间 + 星期判断：pre / open / lunch / closed / holiday。节假日误判由 K 线兜底（见 build_payload）。"""
    if now.weekday() >= 5:
        return "holiday"
    hm = (now.hour, now.minute)
    if hm < PRE_OPEN:
        return "closed"
    if hm < (9, 30):
        return "pre"
    if hm < (11, 30):
        return "open"
    if hm < (13, 0):
        return "lunch"
    if hm < (15, 0):
        return "open"
    return "closed"


def _sum_until(klines, date, hm):
    """把某天的 5 分钟 K 线累加到 hm（"HH:MM"，含）。K 线的时间戳是区间结束时刻，09:35 那根含 09:30~09:35。"""
    return sum(v for ts, v in klines if ts[:10] == date and ts[11:16] <= hm)


def _round5(now):
    """当前时刻四舍五入到 5 分钟（对应 K 线的结束时刻），返回 "HH:MM"。误差最多 2.5 分钟，对量比影响 <1%。"""
    m = int(round(now.minute / 5.0)) * 5
    h = now.hour + (1 if m == 60 else 0)
    return "%02d:%02d" % (h, m % 60)


def build_payload(quotes, klines, daily, now):
    """
    把抓到的原始数据算成前端要的形状（见 docs/market-strip-design.md §5）。
    quotes: fetch_quotes()；klines: {sym: fetch_kline5()}；daily: {sym: fetch_kline_daily()}；后两者可以是空 dict。
    """
    phase = phase_of(now)
    today = now.strftime("%Y-%m-%d")
    hm_now = (now.hour, now.minute)
    # 交易日判断：K 线里有没有今天这根。盘中 9:40 之后 / 收盘之后还没有，就当今天休市（法定节假日），
    # 数据按最近一个交易日展示；早于 9:40 的时候还太早不下结论，按时钟给的阶段走。
    dates = {ts[:10] for rows in klines.values() for ts, _ in rows}
    dates |= {d for rows in daily.values() for d, _ in rows}
    trade_date = today
    if today not in dates:
        past = sorted(d for d in dates if d < today)
        if past:
            trade_date = past[-1]
        if (phase in ("open", "lunch") and hm_now >= (9, 40)) or (phase == "closed" and hm_now >= (15, 0)):
            phase = "holiday"

    idx = [{"code": q["code"], "nm": q["nm"], "px": round(q["px"], 2), "chg": round(q["chg"], 2) or 0.0}   # or 0.0：去掉 -0.0
           for sym, _, _ in INDICES for q in [quotes.get(sym)] if q]
    amt = sum((quotes.get(s) or {}).get("amt", 0.0) for s in MARKET_SYMS)
    today_vol = sum((quotes.get(s) or {}).get("vol", 0.0) for s in MARKET_SYMS)

    vol = None
    if phase != "pre" and all(klines.get(s) for s in MARKET_SYMS):
        # 「昨日」= K 线里 trade_date 之前的最后一个交易日
        prev_days = sorted(d for d in dates if d < trade_date)
        if prev_days:
            prev = prev_days[-1]
            if phase in ("open", "lunch"):
                hm = _round5(now) if phase == "open" else "11:30"
                base = sum(_sum_until(klines[s], prev, hm) for s in MARKET_SYMS)
                vs = "vs 昨日同时段"
            else:
                base = sum(_sum_until(klines[s], prev, "15:00") for s in MARKET_SYMS)
                vs = "vs 昨日全天" if phase == "closed" else "%s 全天 vs 前一日" % trade_date[5:]
            if base > 0 and today_vol > 0:
                ratio = today_vol / base - 1
                tag = "more" if ratio >= VOL_THRESHOLD else "less" if ratio <= -VOL_THRESHOLD else "flat"
                vol = {"ratio": round(ratio, 4), "tag": tag, "vs": vs}

    return {"phase": phase, "at": now.strftime("%H:%M:%S"), "trade_date": trade_date,
            "idx": idx, "amt": amt, "vol": vol, "err": None}


# ---------- 后台线程 ----------
class MarketFeed:
    """
    独立线程：按阶段决定节奏拉行情，算好后 broadcast("market", payload)。
    失败不抛：保留上次 payload、把错误写进 err、按 10s→30s→60s→5min 退避。
    """
    QUOTE_INTERVAL = 10        # 盘中拉实时行情的间隔
    KLINE_INTERVAL = 60        # 盘中刷新 5 分钟 K 线（算昨日同时段）的间隔
    IDLE_INTERVAL = 60         # 午休 / 临近开盘 的检查间隔
    CLOSED_INTERVAL = 30 * 60  # 收盘后 / 休市：低频兜底（跨天、临近开盘时能醒过来）
    BACKOFF = (10, 30, 60, 300)

    def __init__(self, broadcast):
        self._broadcast = broadcast
        self.latest = None
        self._thread = None
        self._stop = threading.Event()
        self._klines = {}
        self._daily = {}
        self._kline_at = 0.0
        self._fail = 0

    # 三个抓取方法拆出来是为了让 MockMarketFeed 只换数据源
    def _fetch_quotes(self):
        return fetch_quotes()

    def _fetch_klines(self):
        return {s: fetch_kline5(s) for s in MARKET_SYMS}

    def _fetch_daily(self):
        return {MARKET_SYMS[0]: fetch_kline_daily(MARKET_SYMS[0])}

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, args=(self._stop,), daemon=True, name="market-feed")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _interval_for(self, phase):
        if phase in ("open", "pre"):
            return self.QUOTE_INTERVAL
        if phase == "lunch":
            return self.IDLE_INTERVAL
        # closed / holiday：临近开盘（8:45 之后）改成 1 分钟，保证 9:15 能准时切到 pre
        now = datetime.now()
        if phase == "closed" and now.weekday() < 5 and (8, 45) <= (now.hour, now.minute) < PRE_OPEN:
            return self.IDLE_INTERVAL
        return self.CLOSED_INTERVAL

    def _refresh_once(self):
        now = datetime.now()
        # K 线不用每次都拉：盘中每 KLINE_INTERVAL 秒一次，其它阶段本来就低频、每次都跟着刷
        if not self._klines or (time.time() - self._kline_at) >= self.KLINE_INTERVAL:
            self._klines = self._fetch_klines()
            self._daily = self._fetch_daily()
            self._kline_at = time.time()
        return build_payload(self._fetch_quotes(), self._klines, self._daily, now)

    def _loop(self, stop):
        while not stop.is_set():
            try:
                payload = self._refresh_once()
                self._fail = 0
                self.latest = payload
                wait = self._interval_for(payload["phase"])
            except Exception as e:
                self._fail += 1
                wait = self.BACKOFF[min(self._fail, len(self.BACKOFF)) - 1]
                msg = "行情接口失败 %d 次：%s，%s后重试" % (
                    self._fail, _short_err(e), "%d 秒" % wait if wait < 60 else "%d 分钟" % (wait // 60))
                now = datetime.now()
                base = self.latest or {"phase": phase_of(now), "trade_date": now.strftime("%Y-%m-%d"),
                                       "idx": [], "amt": 0, "vol": None, "at": now.strftime("%H:%M:%S")}
                self.latest = dict(base, err=msg)
            self._broadcast("market", self.latest)
            stop.wait(wait)


def _short_err(e):
    s = str(e) or e.__class__.__name__
    return s if len(s) <= 60 else s[:60] + "…"


class MockMarketFeed(MarketFeed):
    """--mock：不联网。指数在基准附近随机游走，成交量/额随开盘时间线性增长；阶段仍按真实时钟判断。"""
    QUOTE_INTERVAL = 5

    def __init__(self, broadcast):
        super().__init__(broadcast)
        self._rnd = random.Random()
        self._base = {"sh000001": 3891.96, "sz399001": 13563.74, "sz399006": 3351.56, "sh000300": 4492.32}
        self._px = dict(self._base)
        self._day_vol = 1.05e9 * (0.85 + 0.4 * self._rnd.random())   # 今日两市全天成交量（手），随机放量或缩量
        self._prev_vol = 1.05e9                                       # 昨日全天

    def _fetch_quotes(self):
        now = datetime.now()
        # 收盘后 / 休市按全天算；盘中按「开盘以来的分钟数」占 240 分钟的比例
        frac = 1.0 if phase_of(now) in ("closed", "holiday") else _trading_minutes(now) / 240.0
        out = {}
        for sym, code, nm in INDICES:
            self._px[sym] *= 1 + self._rnd.uniform(-0.0008, 0.0008)
            share = {"sh000001": 0.45, "sz399001": 0.55}.get(sym, 0)
            vol = self._day_vol * share * frac
            out[sym] = {"code": code, "nm": nm, "px": self._px[sym], "chg": (self._px[sym] / self._base[sym] - 1) * 100,
                        "vol": vol, "amt": vol * 100 * (12.0 if sym == "sh000001" else 18.0)}   # 每手 100 股 × 均价
        return out

    def _fetch_klines(self):
        # 昨天：48 根，开盘高、盘中低、尾盘略高的常见形态；今天只造到当前时刻
        now = datetime.now()
        prev = _prev_weekday(now)
        rows = {}
        for sym in MARKET_SYMS:
            share = {"sh000001": 0.45, "sz399001": 0.55}[sym]
            lst = []
            for d, total in ((prev, self._prev_vol * share), (now, self._day_vol * share)):
                for i, hm in enumerate(_slots5()):
                    if d is now and (phase_of(now) not in ("closed", "holiday")) and hm > _round5(now):
                        break
                    w = 1.6 if i < 6 else 0.8 if i < 40 else 1.3
                    lst.append(("%s %s" % (d.strftime("%Y-%m-%d"), hm), total / 48 * w))
            rows[sym] = lst
        return rows

    def _fetch_daily(self):
        now = datetime.now()
        prev = _prev_weekday(now)
        rows = [(prev.strftime("%Y-%m-%d"), self._prev_vol * 0.45)]
        if now.weekday() < 5 and (now.hour, now.minute) >= (9, 30):
            rows.append((now.strftime("%Y-%m-%d"), self._day_vol * 0.45))
        return {MARKET_SYMS[0]: rows}


def _slots5():
    """一个交易日的 48 个 5 分钟 K 线结束时刻。"""
    out = []
    for h, m0, m1 in ((9, 35, 60), (10, 0, 60), (11, 0, 35), (13, 5, 60), (14, 0, 60), (15, 0, 5)):
        for m in range(m0, m1, 5):
            out.append("%02d:%02d" % (h, m))
    return out


def _trading_minutes(now):
    """今天开盘以来的交易分钟数（0~240），演示模式用来让成交量随时间增长。"""
    hm = now.hour * 60 + now.minute
    am = min(max(hm - (9 * 60 + 30), 0), 120)
    pm = min(max(hm - 13 * 60, 0), 120)
    return am + pm


def _prev_weekday(now):
    d = now - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d
