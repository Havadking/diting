# -*- coding: utf-8 -*-
"""
AI 日报：把某位博主某一天的全部动态整理成提示词，交给 OpenAI 兼容的聊天接口总结。

纯函数模块，不碰 core / SQLite / 线程：拿到条目列表和接口配置就能跑。
接口配置（config.json 的 "ai" 字段）：

    "ai": {
      "active": "<profile id>",
      "profiles": [{"id": "...", "name": "DeepSeek", "base_url": "https://api.deepseek.com",
                    "api_key": "sk-...", "model": "deepseek-flash", "thinking": "low"}],
      "prompt": ""        # 覆盖默认的「总结要求」，空串就用 DEFAULT_REQUEST
    }

只做 OpenAI 兼容格式（DeepSeek / 千问 / Kimi / OpenAI 都是这一套）：POST {base_url}/chat/completions。

thinking 是 DeepSeek 的思考模式开关（见 THINKING_MODES）：""/缺省 = 请求里不带，模型按自己的默认来
（deepseek-flash 默认思考且 effort=high）；"off" 关；"low"/"high"/"max" 开并指定强度。
只有 DeepSeek 认 `thinking` / `reasoning_effort` 这两个参数，别的厂商可能 400，所以默认不带。
"""
import json
import re
import urllib.request
import urllib.error
from datetime import datetime

WEEK = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

SYSTEM_PROMPT = (
    "你是一个股吧动态整理助手。用户会给你某位东方财富股吧博主某一天的全部发帖、转发、追加和评论回复"
    "（评论会附上被回复者的原话，格式是「问(某人): … → 答: …」）。\n"
    "你的任务是忠实归纳博主本人的观点和操作：不添加你自己的判断，不给投资建议，不评价博主水平。"
    "信息不足就写「未提及」，不要推测、不要编造价位。引用博主原话时在前面标注时间，如「(10:39)」。"
    "输出用 Markdown，用中文。"
)

# 用户可在设置页覆盖这一段（config.ai.prompt）。头部的「博主/日期/条数」和尾部的原始动态由程序拼接。
DEFAULT_REQUEST = """请按下面的结构输出：

1. **一句话总览**：今天的整体态度（偏多 / 偏空 / 观望）和核心逻辑
2. **大盘与板块观点**：只写博主明确表达的
3. **个股操作台账**（Markdown 表格）：股票 | 动作（买入/卖出/减仓/加仓/做T/持有/观察/不建议） | 价位或条件 | 依据（时间 + 原话摘要）。同一只股票一天多次操作合并到一行，按时间顺序写
4. **回答粉丝的个股判断**：粉丝问的股票 → 博主的结论（一句话），只列有明确结论的
5. **明日计划 / 关注点**：博主明确说的，没有就写「未提及」
6. **其它**：一句话带过与操作无关的内容（争吵、闲聊等）"""

# 思考模式取值 → 请求体里附加的字段。DeepSeek 文档：思考模式下 temperature 等参数不报错但被忽略。
THINKING_MODES = {
    "": {},
    "off": {"thinking": {"type": "disabled"}},
    "low": {"thinking": {"type": "enabled"}, "reasoning_effort": "low"},
    "high": {"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
    "max": {"thinking": {"type": "enabled"}, "reasoning_effort": "max"},
}

STOCK_TAG_RE = re.compile(r"\$([^$()]{1,20})\((?:SH|SZ|BJ|HK|US)?(\w+)\)\$")
CTX_RE = re.compile(r"^\[(评论|转发自|转推自)\s*(.*?)\]\s*", re.S)


def _clean(text):
    """`$诺德股份(SH600110)$` → `诺德股份(600110)`；压掉多余空白。"""
    text = STOCK_TAG_RE.sub(r"\1(\2)", text or "")
    text = re.sub(r"[ \t　]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _split_ctx(content):
    """core._add_item 拼出来的 `[评论《xx》] 正文` / `[转发自 某人《xx》] 正文` 前缀拆开。"""
    m = CTX_RE.match(content or "")
    if not m:
        return "", "", content or ""
    return m.group(1), m.group(2).strip(), content[m.end():]


def preprocess(items):
    """去噪：丢纯图片条目；「转发自己的帖」和同一分钟的「追加」正文完全一样，只留转发那条。
    返回按时间升序的 (time, kind, bar, ctx_kind, ctx_text, body, quote_user, quote_text)。"""
    rows = []
    forward_bodies = set()
    for it in sorted(items, key=lambda x: (x["time"], x["key"])):
        ctx_kind, ctx_text, body = _split_ctx(it.get("content", ""))
        body = _clean(body)
        if not body or body == "(无正文)":
            continue
        if it["kind"] == "转发":
            forward_bodies.add(body)
        rows.append({
            "time": it["time"][11:16], "kind": it["kind"], "bar": (it.get("bar") or "").replace("吧", "", 1) if it.get("bar") else "",
            "ctx_kind": ctx_kind, "ctx_text": _clean(ctx_text)[:24], "body": body,
            "quote_user": it.get("quote_user") or "", "quote_text": _clean(it.get("quote_text") or ""),
        })
    return [r for r in rows if not (r["kind"] == "追加" and r["body"] in forward_bodies)]


def format_rows(rows):
    lines = []
    for r in rows:
        where = ("@" + r["bar"]) if r["bar"] and r["bar"] != "—" else ""
        if r["kind"] == "评论":
            head = "[%s 评论 %s 帖%s]" % (r["time"], where, r["ctx_text"])
            if r["quote_text"]:
                lines.append("%s 问(%s): %s → 答: %s" % (head, r["quote_user"] or "网友", r["quote_text"], r["body"]))
            else:
                lines.append("%s %s" % (head, r["body"]))
        elif r["kind"] == "转发":
            lines.append("[%s 转发 %s 原帖 %s] %s" % (r["time"], where, r["ctx_text"], r["body"]))
        elif r["kind"] == "追加":
            lines.append("[%s 追加] %s" % (r["time"], r["body"]))
        else:
            lines.append("[%s %s %s] %s" % (r["time"], r["kind"], where, r["body"]))
    return "\n".join(lines)


def build_prompt(name, date, items, request=None):
    """返回 (system, user, 有效条数)。request 为空就用 DEFAULT_REQUEST。"""
    rows = preprocess(items)
    try:
        wd = WEEK[datetime.strptime(date, "%Y-%m-%d").weekday()]
    except Exception:
        wd = ""
    user = "博主：%s\n日期：%s %s\n有效动态：%d 条\n\n%s\n\n--- 以下是当天全部动态（按时间顺序）---\n%s" % (
        name, date, wd, len(rows), (request or "").strip() or DEFAULT_REQUEST, format_rows(rows))
    return SYSTEM_PROMPT, user, len(rows)


# ---------- 调接口 ----------
def _endpoint(base_url):
    b = (base_url or "").strip().rstrip("/")
    if not b:
        raise ValueError("接口地址为空")
    if not b.startswith(("http://", "https://")):
        b = "https://" + b
    if b.endswith("/chat/completions"):
        return b
    return b + "/chat/completions"


# 日报默认给的输出上限。推理模型（deepseek-reasoner 这类）的思考过程也计入 completion token，
# 八九十条动态的思考轻松就是上万 token，8000 会被吃光、正文为空；所以按推理模型的量级给。
# 不支持这么大的模型（deepseek-chat 上限 8192）会回 HTTP 400 报有效区间，chat() 会解析出上限重试一次。
SUMMARY_MAX_TOKENS = 32000

MAX_TOKENS_RANGE_RE = re.compile(r"max_tokens.*?\[\s*\d+\s*,\s*(\d+)\s*\]", re.S)


def chat(profile, system, user, timeout=300, max_tokens=SUMMARY_MAX_TOKENS):
    """一次非流式聊天补全。返回 (正文, usage 字典)。出错抛 RuntimeError，信息尽量带上服务端给的原因。

    max_tokens 超过模型上限被 400 拒绝时，从错误信息里解析出上限再试一次。"""
    if not (profile.get("api_key") or "").strip():
        raise RuntimeError("这个配置还没填 API Key")
    if not (profile.get("model") or "").strip():
        raise RuntimeError("这个配置还没填模型名")
    try:
        return _chat_once(profile, system, user, timeout, max_tokens)
    except _MaxTokensTooLarge as e:
        if e.limit >= max_tokens:
            raise RuntimeError(e.detail)
        return _chat_once(profile, system, user, timeout, e.limit)


class _MaxTokensTooLarge(Exception):
    def __init__(self, limit, detail):
        super().__init__(detail)
        self.limit, self.detail = limit, detail


def _chat_once(profile, system, user, timeout, max_tokens):
    payload = {
        "model": profile["model"].strip(),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "stream": False,
    }
    payload.update(THINKING_MODES.get(profile.get("thinking") or "", {}))
    req = urllib.request.Request(_endpoint(profile.get("base_url")), data=json.dumps(payload).encode("utf-8"), headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + profile["api_key"].strip(),
        "Accept": "application/json",
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "ignore")
            detail = (json.loads(raw).get("error") or {}).get("message") or raw
        except Exception:
            detail = raw
        detail = "HTTP %d：%s" % (e.code, str(detail or e.reason)[:200])
        m = e.code == 400 and MAX_TOKENS_RANGE_RE.search(str(detail))
        if m:
            raise _MaxTokensTooLarge(int(m.group(1)), detail)
        raise RuntimeError(detail)
    except urllib.error.URLError as e:
        raise RuntimeError("连不上接口：%s" % e.reason)
    try:
        data = json.loads(body)
    except Exception:
        raise RuntimeError("接口返回的不是 JSON：%s" % body[:120])
    if data.get("error"):
        raise RuntimeError(str((data["error"] or {}).get("message") or data["error"])[:200])
    try:
        choice = data["choices"][0]
        text = (choice["message"].get("content") or "").strip()
    except Exception:
        raise RuntimeError("接口返回里没有 choices[0].message.content：%s" % body[:120])
    if not text:
        # 推理模型（deepseek-reasoner / deepseek-flash 这类）的思考过程也算 completion token，
        # 思考太长会把 max_tokens 吃光，finish_reason=length 且 content 为空——别把空串当结果存起来
        if choice.get("finish_reason") == "length":
            raise RuntimeError("模型输出被截断（finish_reason=length）：思考过程把 %d 个输出 token 花完了，正文是空的。换非推理模型或减少当天条目" % max_tokens)
        raise RuntimeError("模型返回了空内容（finish_reason=%s）" % choice.get("finish_reason"))
    return text, data.get("usage") or {}


def summarize(profile, name, date, items, request=None):
    """生成一天的日报。返回 dict：text / model / item_count / usage / created_at。"""
    system, user, n = build_prompt(name, date, items, request)
    if n == 0:
        raise RuntimeError("这一天没有可总结的动态")
    text, usage = chat(profile, system, user)
    return {
        "text": text, "model": profile.get("model", ""), "item_count": n, "usage": usage,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def ping(profile):
    """设置页「测试连接」：发个极短的请求，回来就算通。"""
    text, _ = chat(profile, "你是测试助手。", "只回复两个字：连通", timeout=60, max_tokens=20)
    return text
