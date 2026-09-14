# -*- coding: utf-8 -*-
"""
谛听 · 网页版入口：本地 HTTP 服务 + 浏览器页面

    python server.py                 # 读 config.json，起监控，自动打开浏览器
    python server.py --mock          # 离线演示：不读配置不联网，用示例数据 + 定时随机推送
    python server.py --no-browser    # 不自动开浏览器（调试用）
    python server.py --port 18000    # 指定端口（默认 17777，被占自动 +1）

纯标准库。只绑 127.0.0.1。接口见 docs/web-design.md §5。
"""
import argparse
import json
import os
import queue
import socket
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
DEFAULT_PORT = 17777
SSE_PING_SECONDS = 25

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
}

# 阶段 3 之前 web/ 目录还不存在，先给个占位页，至少能看出服务活着
PLACEHOLDER_HTML = """<!doctype html><meta charset="utf-8"><title>谛听</title>
<body style="font-family:Microsoft YaHei UI,sans-serif;padding:40px;color:#1b1f27">
<h1 style="font-family:Noto Serif SC,SimSun,serif">谛听</h1>
<p>后端已启动，前端页面（<code>web/</code>）尚未就绪。</p>
<p>试试 <a href="/api/snapshot">/api/snapshot</a> 或 <code>curl -N /api/events</code>。</p>
</body>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "diting/0.1"
    core = None  # 由 main() 注入

    # ---- 工具 ----
    def _send_json(self, obj, status=HTTPStatus.OK):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, data, ctype, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _send_error_json(self, status, msg):
        self._send_json({"ok": False, "error": msg}, status)

    def log_message(self, fmt, *args):
        # SSE 长连接和静态文件太吵，只记 API 与错误
        if self.path.startswith("/api/events") or self.path.startswith("/assets/"):
            return
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # ---- 路由 ----
    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        if path == "/":
            return self._serve_static("index.html")
        if path.startswith("/assets/"):
            return self._serve_static(path[len("/assets/"):])
        if path == "/api/snapshot":
            return self._api_snapshot()
        if path == "/api/events":
            return self._api_events()
        self._send_error_json(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self):
        # 阶段 5 再实现 /api/control 与 /api/users；这里先把 Origin 校验立好
        if not self._origin_ok():
            return self._send_error_json(HTTPStatus.FORBIDDEN, "bad origin")
        self._send_error_json(HTTPStatus.NOT_FOUND, "not found")

    def _origin_ok(self):
        origin = self.headers.get("Origin", "")
        host = self.headers.get("Host", "")
        return bool(origin) and origin == "http://" + host

    # ---- 静态文件 ----
    def _serve_static(self, rel):
        if not os.path.isdir(WEB_DIR):
            if rel == "index.html":
                return self._send_bytes(PLACEHOLDER_HTML.encode("utf-8"), MIME[".html"])
            return self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
        root = os.path.realpath(WEB_DIR)
        full = os.path.realpath(os.path.join(root, rel.replace("/", os.sep)))
        # 防目录穿越：解析后的真实路径必须仍在 web/ 之下
        if not (full == root or full.startswith(root + os.sep)) or not os.path.isfile(full):
            return self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            data = f.read()
        self._send_bytes(data, MIME.get(ext, "application/octet-stream"))

    # ---- API ----
    def _api_snapshot(self):
        self._send_json(self.core.snapshot())

    def _api_events(self):
        core = self.core
        q = core.subscribe()
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            # 连上先给一条当前状态，前端不用等下一轮
            self._sse_write("status", core._status_payload())
            while True:
                try:
                    etype, payload = q.get(timeout=SSE_PING_SECONDS)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                self._sse_write(etype, payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout):
            pass
        finally:
            core.unsubscribe(q)
            # 长连接断了就别再回 keep-alive 循环里读下一个请求，否则会在对端已关的 socket 上
            # 抛 ConnectionAbortedError，被 socketserver 当异常打一整屏 traceback
            self.close_connection = True

    def _sse_write(self, etype, payload):
        data = json.dumps(payload, ensure_ascii=False)
        self.wfile.write(("event: %s\ndata: %s\n\n" % (etype, data)).encode("utf-8"))
        self.wfile.flush()


class Server(ThreadingHTTPServer):
    daemon_threads = True  # SSE 长连接线程别拖住退出
    # 标准库默认开 SO_REUSEADDR，在 Windows 上这会让"端口已被另一个进程监听"时 bind 照样成功
    # （然后两个进程抢连接），端口顺延逻辑就形同虚设。关掉它；Windows 的 TIME_WAIT 本来也不挡重绑。
    allow_reuse_address = False


def bind_server(port, tries=20):
    """从 port 开始逐个试，被占就 +1。"""
    last = None
    for p in range(port, port + tries):
        try:
            return Server(("127.0.0.1", p), Handler), p
        except OSError as e:
            last = e
    raise SystemExit("端口 %d~%d 全被占用：%s" % (port, port + tries - 1, last))


def main():
    ap = argparse.ArgumentParser(description="谛听 · 网页版")
    ap.add_argument("--mock", action="store_true", help="离线演示：示例数据 + 定时随机推送，不读配置不联网")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--mock-interval", type=int, default=10, help="--mock 模式下推送间隔（秒）")
    args = ap.parse_args()

    if args.mock:
        import mock_data
        core = mock_data.MockCore(interval=args.mock_interval)
    else:
        import core as core_mod
        core = core_mod.MonitorCore()
    Handler.core = core

    httpd, port = bind_server(args.port)
    url = "http://127.0.0.1:%d" % port

    err = core.start()
    if err:
        core.set_status(err.replace("\n", " "))
        print("监控未启动：%s" % err.replace("\n", " "), file=sys.stderr)

    print("谛听 网页版已启动：%s%s" % (url, "（--mock 演示模式）" if args.mock else ""))
    if not args.no_browser:
        threading.Timer(0.3, webbrowser.open, [url]).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        core.stop()
        httpd.server_close()


if __name__ == "__main__":
    main()
