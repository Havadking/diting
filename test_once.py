# -*- coding: utf-8 -*-
"""一次性测试：抓取 config.json 里第一个用户的最新发帖+评论并打印（不推送、不写状态）。
确认抓取和解析是否正常。运行: python test_once.py
"""
import sys
import monitor

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

cfg = monitor.load_config()
users = cfg.get("users", [])
if not users:
    print("config.json 里没有配置用户。")
    sys.exit(1)

u = users[0]
uid = str(u["uid"])
name = u.get("name") or uid
print("正在抓取用户：%s (uid=%s)\n" % (name, uid))

items = monitor.collect_items(cfg, uid)
items.sort(key=lambda x: x["time"], reverse=True)
print("共抓到 %d 条（发帖+评论），显示最新 8 条：\n" % len(items))
for it in items[:8]:
    print("%s [%s] %s  股吧:%s" % (it["icon"], it["time"], it["kind"], it["bar"]))
    if it["kind"] == "评论" and (it["ctx_user"] or it["ctx_text"]):
        print("   评论于: %s《%s》" % (it["ctx_user"], it["ctx_text"][:30]))
    if it["title"]:
        print("   标题:", it["title"])
    c = it["content"]
    print("   内容:", (c[:70] + "...") if len(c) > 70 else c)
    print("   链接:", it["link"])
    print()
