# -*- coding: utf-8 -*-
"""check_ui_api.py —— 逐个实测 UI 的 API 接口。

为什么需要
----------
用户问"UI 搭建的接口对不对、识别检查信息获取做的对不对"。
光看代码不算验证 —— 要实际打一遍接口，看返回结构。

检查项
------
1. 每个 API 是否可达（HTTP 200）
2. 返回的 JSON 结构是否包含前端期望的字段
3. 字段值的语义是否正确（比如 /api/state 的 my_pct 应该是 0~100）

跑法：
    python scripts\\check_ui_api.py
    python scripts\\check_ui_api.py --port 8770
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAILS = []
WARNS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


def warn(msg):
    print("  warn %s" % msg)
    WARNS.append(msg)


def get(url, timeout=10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read().decode("utf-8", "ignore")
            try:
                return r.status, json.loads(body)
            except Exception:
                return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return None, str(e)


def find_running_port():
    """找正在跑的 ui_server 端口。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "
             "'python.exe' -and $_.CommandLine -like '*ui_server*' } | "
             "ForEach-Object { $_.CommandLine }"],
            capture_output=True, text=True, encoding="utf-8",
            errors="ignore", timeout=30).stdout
    except Exception:
        return None
    import re
    for m in re.finditer(r"--port\s+(\d+)", out or ""):
        return int(m.group(1))
    return None


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()

    port = args.port or find_running_port()
    if not port:
        print("没有在跑的 ui_server。先启动：")
        print("  python src\\ui.py --serve-only")
        return 1
    base = "http://127.0.0.1:%d" % port
    print("测试目标: %s" % base)
    print()

    # ---------- 1. health ----------
    print("=== /api/health ===")
    st, d = get(base + "/api/health")
    check(st == 200, "HTTP 200（实际 %s）" % st)
    check(isinstance(d, dict), "返回 JSON 对象")
    if isinstance(d, dict):
        print("    内容: %s" % json.dumps(d, ensure_ascii=False)[:200])

    # ---------- 2. bot ----------
    print()
    print("=== /api/bot ===")
    st, d = get(base + "/api/bot")
    check(st == 200, "HTTP 200（实际 %s）" % st)
    if isinstance(d, dict):
        print("    字段: %s" % list(d.keys()))
        # 前端期望的字段
        for k in ("running", "pid", "uptime_s"):
            if k in d:
                print("    %-10s = %s" % (k, d[k]))
            else:
                warn("/api/bot 缺字段 %s" % k)
    else:
        check(False, "返回不是 dict")

    # ---------- 3. state（最关键的实时状态） ----------
    print()
    print("=== /api/state ===")
    st, d = get(base + "/api/state")
    check(st == 200, "HTTP 200（实际 %s）" % st)
    if d is None:
        # 机器人没在跑时 live_state.json 不存在，返回 null 是**正确行为**
        bot_st, bot_d = get(base + "/api/bot")
        running = bool(isinstance(bot_d, dict) and bot_d.get("running"))
        if running:
            check(False, "机器人 running=True 但 /api/state 返回 null")
        else:
            print("  ok   机器人未运行 -> /api/state 返回 null（正确）")
    elif isinstance(d, dict):
        keys = list(d.keys())
        print("    字段: %s" % keys)
        for k in ("battle", "my_pct", "foe_pct", "skills", "me", "foe"):
            if k not in d:
                warn("/api/state 缺字段 %s" % k)
        mp = d.get("my_pct")
        if mp is not None:
            check(0 <= mp <= 100, "my_pct 在 0~100（%s）" % mp)
        fp = d.get("foe_pct")
        if fp is not None:
            check(0 <= fp <= 100, "foe_pct 在 0~100（%s）" % fp)
        sk = d.get("skills")
        if isinstance(sk, list):
            check(len(sk) == 4, "skills 有 4 个槽位（实际 %d）" % len(sk))
            if sk:
                s0 = sk[0]
                if isinstance(s0, dict):
                    print("    skills[0] 字段: %s" % list(s0.keys()))
        print("    battle=%s me=%s foe=%s" % (
            d.get("battle"), d.get("me"), d.get("foe")))
    else:
        check(False, "返回类型异常: %r" % type(d).__name__)

    # ---------- 4. log ----------
    print()
    print("=== /api/log ===")
    st, d = get(base + "/api/log?offset=0")
    check(st == 200, "HTTP 200（实际 %s）" % st)
    if isinstance(d, dict):
        check("offset" in d, "有 offset 字段")
        check("lines" in d, "有 lines 字段")
        n = len(d.get("lines") or [])
        print("    offset=%s lines=%d 行" % (d.get("offset"), n))
        if n:
            print("    首行: %s" % str((d.get("lines") or [""])[0])[:100])
    else:
        check(False, "返回不是 dict")

    # ---------- 5. stats ----------
    print()
    print("=== /api/stats ===")
    st, d = get(base + "/api/stats")
    check(st == 200, "HTTP 200（实际 %s）" % st)
    if isinstance(d, dict):
        print("    字段: %s" % list(d.keys()))
        print("    内容: %s" % json.dumps(d, ensure_ascii=False)[:250])

    # ---------- 6. turns ----------
    print()
    print("=== /api/turns ===")
    st, d = get(base + "/api/turns?limit=3")
    check(st == 200, "HTTP 200（实际 %s）" % st)
    if isinstance(d, dict):
        print("    字段: %s" % list(d.keys()))
        n = len(d.get("turns") or d.get("items") or [])
        print("    返回 %d 条" % n)
        sample = (d.get("turns") or d.get("items") or [None])[0]
        if sample:
            print("    首条字段: %s" % list(sample.keys()))
    elif isinstance(d, list):
        print("    返回 %d 条" % len(d))
        if d:
            print("    首条字段: %s" % list(d[0].keys()))

    # ---------- 7. rounds ----------
    print()
    print("=== /api/rounds ===")
    st, d = get(base + "/api/rounds?limit=3")
    check(st == 200, "HTTP 200（实际 %s）" % st)
    if isinstance(d, dict):
        print("    字段: %s" % list(d.keys()))
        n = len(d.get("rounds") or d.get("items") or [])
        print("    返回 %d 条" % n)
        sample = (d.get("rounds") or d.get("items") or [None])[0]
        if sample:
            print("    首条: %s" % json.dumps(sample, ensure_ascii=False)[:180])
    elif isinstance(d, list):
        print("    返回 %d 条" % len(d))

    # ---------- 8. dex ----------
    print()
    print("=== /api/dex ===")
    st, d = get(base + "/api/dex?q=" + urllib.parse.quote("音速犬") + "&limit=3")
    check(st == 200, "HTTP 200（实际 %s）" % st)
    if isinstance(d, dict):
        print("    字段: %s" % list(d.keys()))
        n = len(d.get("items") or d.get("results") or [])
        print("    返回 %d 条" % n)
        sample = (d.get("items") or d.get("results") or [None])[0]
        if sample:
            print("    首条: %s" % json.dumps(sample, ensure_ascii=False)[:180])
    elif isinstance(d, list):
        print("    返回 %d 条" % len(d))

    # ---------- 9. team ----------
    print()
    print("=== /api/team ===")
    st, d = get(base + "/api/team")
    check(st == 200, "HTTP 200（实际 %s）" % st)
    if isinstance(d, dict):
        print("    字段: %s" % list(d.keys()))
        slots = d.get("slots") or d.get("team") or []
        print("    返回 %d 只" % len(slots))
        if slots:
            print("    首条字段: %s" % list(slots[0].keys()))
            print("    首条: %s" % json.dumps(slots[0], ensure_ascii=False)[:180])
    elif isinstance(d, list):
        print("    返回 %d 只" % len(d))
        if d:
            print("    首条: %s" % json.dumps(d[0], ensure_ascii=False)[:180])

    # ---------- 10. 前端期望 vs 后端返回 ----------
    print()
    print("=== 前端 JS 引用的 API ===")
    js_dir = os.path.join(ROOT, "ui")
    used = set()
    import re
    # 注意：字符类要包含下划线，否则 /api/portrait/auto_confirm 会被截成 auto
    pat = re.compile(r"""['"`](/api/[a-z0-9_/]+)""")
    for fn in ("app.js", "console.js"):
        p = os.path.join(js_dir, fn)
        if not os.path.exists(p):
            continue
        txt = open(p, encoding="utf-8", errors="ignore").read()
        for m in pat.finditer(txt):
            used.add(m.group(1))
    print("  app.js/console.js 里出现的 %d 个: %s" % (len(used), sorted(used)))

    # 直接读后端源码，看每个路径有没有注册（比打接口更准，能区分 GET/POST）
    srv = os.path.join(ROOT, "src", "ui_server.py")
    srv_txt = open(srv, encoding="utf-8", errors="ignore").read() \
        if os.path.exists(srv) else ""
    missing = []
    for u in sorted(used):
        registered = ('path == "%s"' % u) in srv_txt
        if registered:
            print("    %-28s OK" % u)
        else:
            missing.append(u)
            print("    %-28s **后端没有这个路由**" % u)
    if missing:
        warn("前端调用了后端没注册的接口: %s" % missing)

    print()
    if FAILS:
        print("FAILED %d:" % len(FAILS))
        for f in FAILS:
            print("  -", f)
    if WARNS:
        print("WARN %d:" % len(WARNS))
        for w in WARNS:
            print("  -", w)
    if not FAILS and not WARNS:
        print("ALL PASS")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
