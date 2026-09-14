# -*- coding: utf-8 -*-
"""
离线示例数据 + MockCore：`python server.py --mock` 用。

不读 config.json、不联网、不碰 messages.db，用一份写死的示例动态灌进内存，
再起一个线程每隔几秒随机推一条"新动态"事件——专门用来离线验证前端渲染和 SSE 流程。
示例文案和 docs/web-mock.html 视觉稿是同一份，日期按"今天"相对偏移，保证日期分组永远有今天/昨天/前天。
"""
import random
import threading
from datetime import datetime, timedelta

import core

USERS = [
    {"uid": "6112353845000000", "name": "股海老船长", "color": "#1772B4", "check_appends": True},
    {"uid": "7423000112000000", "name": "半仓过节", "color": "#ED5126"},
    {"uid": "5091223300000000", "name": "茅台信徒", "color": "#1BA784", "mute": True},
    {"uid": "8830011290000000", "name": "量化小张"},
]

# (天数偏移, 时间, 用户名, 类型, 股吧, 展示用 content —— 已按 core._add_item 的规则拼好前缀)
# 天数偏移为 0 时"时间"是负的分钟数（距现在多久之前），这样今天的示例永远排在定时随机推送之前
_RAW = [
    (0, -6, "股海老船长", "发帖", "中际旭创",
     "光模块这波不是炒概念，是真有订单。刚看了英伟达最新的供应链数据，1.6T 的放量节奏比上季度预期提前了一个季度，"
     "国内这几家的份额没丢反涨。回调就是上车机会，我加了三成。"),
    (0, -25, "股海老船长", "追加", "中际旭创",
     "作者更新：补充一下，仓位控制在 5 成以内，别梭哈。今天尾盘量能不太够，明天看能不能站稳 5 日线。"),
    (0, -39, "半仓过节", "评论", "宁德时代",
     "[评论《宁德三季度出货量超预期，储能业》] 储能确实是亮点，但动力电池那块的毛利还在往下走，别只看营收。"),
    (0, -197, "茅台信徒", "发帖", "贵州茅台",
     "1500 以下每跌 50 加一档，这个策略我执行第三年了，年化比大部分基金经理强。批发价企稳了，中秋动销比去年好，慌什么。"),
    (0, -275, "半仓过节", "转发", "沪深300",
     "[转发自 宏观小酒馆《9 月降准概率复盘》] 转给大家看看，这个复盘做得挺细。我的看法：流动性宽松已经 price in 了，别把降准当利好追。"),
    (0, -306, "量化小张", "发帖", "中证1000",
     "开盘 1 分钟小市值因子 IC 又转负了，模型今天减了 20% 敞口。今天大概率是权重日。"),
    (1, "20:14:18", "股海老船长", "发帖", "长江电力",
     "周末复盘：高股息这边又新高了，现在市场就两条腿，一条 AI 硬件一条红利，中间的都没人要。持仓不动。"),
    (1, "16:40:00", "茅台信徒", "评论", "五粮液",
     "[评论《五粮液批价跌破 900 了》] 老窖和五粮液的渠道库存问题比茅台严重得多，这个位置我不会碰。"),
    (1, "15:05:12", "量化小张", "发帖", "北证50",
     "北交所今天成交额创月内新高，微盘风格有回归迹象，但 9 月的季节性不太支持，先观察两天。"),
    (2, "15:02:44", "半仓过节", "发帖", "上证指数",
     "收盘了。这周就一句话：不追高，不割肉，持币过节。"),
]

_ICON = {"发帖": "📝", "评论": "💬", "转发": "🔁", "追加": "➕"}

# 随机新动态的文案池
_NEW_POOL = [
    ("发帖", "盘中快报：{bar} 放量拉升，成交额已经超过昨天全天，注意节奏。"),
    ("发帖", "刚看了 {bar} 的龙虎榜，机构席位净买入，游资在出。"),
    ("评论", "[评论《{bar}今天怎么看》] 缩量阴跌比放量下跌难受，但也说明没人恐慌。"),
    ("转发", "[转发自 财经早知道《{bar} 深度》] 写得比券商研报实在，推荐一读。"),
    ("追加", "作者更新：{bar} 尾盘那笔大单是我说的机构，明天继续观察。"),
]
_BARS = ["中际旭创", "宁德时代", "贵州茅台", "长江电力", "中证1000", "北证50", "沪深300", "上证指数"]


def _entry(day_off, hms, name, kind, bar, content, key=None):
    if isinstance(hms, int):
        t = (datetime.now() + timedelta(minutes=hms)).strftime("%Y-%m-%d %H:%M:%S")
    else:
        d = (datetime.now() - timedelta(days=day_off)).strftime("%Y-%m-%d")
        t = "%s %s" % (d, hms)
    return {
        "key": key or ("M%s_%s" % (kind[0], t.replace("-", "").replace(":", "").replace(" ", ""))),
        "name": name, "kind": kind, "icon": _ICON[kind], "time": t, "bar": bar,
        "content": content, "link": "https://guba.eastmoney.com/",
    }


def sample_items():
    rows = [_entry(*r) for r in _RAW]
    rows.sort(key=lambda x: x["time"])
    return rows


class MockCore(core.MonitorCore):
    """跟真 core 同一套订阅/广播/snapshot，只是数据源换成定时随机造的。"""

    def __init__(self, interval=10):
        # 故意不调父类 __init__（它会读 config.json / messages.db），只搭骨架
        self.worker = None
        self.stop_event = threading.Event()
        self.running = False
        self.items = sample_items()
        self.item_keys = {e["key"] for e in self.items}
        self.lock = threading.Lock()
        self._subs = []
        self._subs_lock = threading.Lock()
        self.status_text = "离线演示模式（--mock）：每 %d 秒随机推一条" % interval
        self.last_check = ""
        self.interval = interval
        self._seq = 0
        self.refresh_config_maps()

    def refresh_config_maps(self):
        self.color_map = {u["name"]: u["color"] for u in USERS if u.get("color")}
        self.muted = {u["name"] for u in USERS if u.get("mute")}

    def describe_config(self):
        return "演示数据 %d 人 · 间隔 %ds" % (len(USERS), self.interval)

    def poll_config(self):
        return {"poll_interval_seconds": self.interval, "append_check_interval_seconds": 300}

    def save_users(self, patch):
        by_name = {u["name"]: u for u in (patch or []) if isinstance(u, dict) and u.get("name")}
        for u in USERS:
            p = by_name.get(u["name"])
            if not p:
                continue
            for k in ("color", "mute", "check_appends"):
                if p.get(k):
                    u[k] = p[k]
                else:
                    u.pop(k, None)
        self.refresh_config_maps()
        self._broadcast("config", {"users": self.list_users()})
        return None

    def test_toast(self):
        self.set_status("（演示模式）假装弹了一条测试通知。")

    def list_users(self):
        return [{"name": u["name"], "uid": u["uid"], "color": u.get("color"),
                 "mute": bool(u.get("mute")), "check_appends": bool(u.get("check_appends"))}
                for u in USERS]

    def start(self):
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self.worker.join(timeout=3)
        self.running = True
        self.stop_event = threading.Event()
        self.set_status("演示模式运行中")
        self.worker = threading.Thread(target=self._loop, args=(self.stop_event,), daemon=True)
        self.worker.start()
        return None

    def _loop(self, stop_event):
        while not stop_event.wait(self.interval):
            self._seq += 1
            kind, tpl = random.choice(_NEW_POOL)
            u = random.choice(USERS)
            bar = random.choice(_BARS)
            now = datetime.now()
            e = _entry(0, now.strftime("%H:%M:%S"), u["name"], kind, bar,
                       tpl.format(bar=bar), key="MOCK%d" % self._seq)
            with self.lock:
                self.items.append(e)
                self.item_keys.add(e["key"])
            self._broadcast("new", [e])
            self.last_check = now.strftime("%H:%M:%S")
            if u["name"] in self.muted:
                self.set_status("🔕 %s 新增 1 条（静音·仅入列）· %s" % (u["name"], self.last_check))
            else:
                self.set_status("%s 新增 1 条 · %s" % (u["name"], self.last_check))
