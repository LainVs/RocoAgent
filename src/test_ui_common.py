# -*- coding: utf-8 -*-
"""test_ui_common.py —— 前端测试的共享层。

为什么单独拆出来（2026-11-21）
------------------------------
`test_ui.py` 原来 1642 行、170 项，三个会话都在改它：

    [ui-overlay]  悬浮窗 DOM / 配色 / 窗口状态
    [ui-session]  控制台 API / 静态资源 / 日志尾推断
    [主会话]      偶尔动 standalone 相关断言

并行改同一个文件必然互相覆盖。所以按归属拆成两个文件，
这个模块放**两边都要用**的东西。

分步计划（`docs/PLAN_TEST_SPLIT.md`）：

    ui-m83  只抽共享层，不改任何断言   <- 本文件
    ui-m84  真正拆出 test_console.py   <- 等控制台会话确认

用法
----
    from test_ui_common import (
        ROOT, SRC, UI,
        ok, port_free, wait_health, get, get_raw,
        summary, reset,
    )
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
UI = os.path.join(ROOT, "ui")

FAILS = []
PASSES = 0


def reset():
    """清空计数（同一进程里跑多组测试时用）。"""
    global PASSES
    FAILS.clear()
    PASSES = 0


def ok(cond, msg, detail=""):
    """记一条断言。返回 cond，方便链式用。"""
    global PASSES
    if cond:
        PASSES += 1
        print("  OK    %s%s" % (msg, ("  " + detail) if detail else ""))
    else:
        FAILS.append(msg + (("  " + detail) if detail else ""))
        print("  FAIL  %s%s" % (msg, ("  " + detail) if detail else ""))
    return cond


def port_free(port):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def wait_health(port, timeout=12):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/api/health" % port,
                    timeout=1.2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def get(port, path, timeout=6):
    with urllib.request.urlopen(
            "http://127.0.0.1:%d%s" % (port, path), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get_raw(port, path, timeout=6):
    with urllib.request.urlopen(
            "http://127.0.0.1:%d%s" % (port, path), timeout=timeout) as r:
        return r.status, r.read()


def summary(title="全部通过"):
    """打印汇总并返回退出码。

    为什么统一（2026-11-21）：拆成两个文件后如果各自 print，
    格式容易不一致（一个写"全部通过（170 项）"，一个写"all pass"）。
    抽出来保证两边输出一样，也方便以后加 CI。
    """
    print()
    print("=" * 66)
    if FAILS:
        print("%d 项失败 / %d 项通过" % (len(FAILS), PASSES))
        for f in FAILS:
            print("  - %s" % f)
        return 1
    print("%s（%d 项）" % (title, PASSES))
    return 0
