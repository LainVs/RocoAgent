# -*- coding: utf-8 -*-
"""peer_send.py —— 给指定会话发一条**临时**消息，不改 jobs.json。

为什么要单独一个脚本
--------------------
`dispatcher.py` 的机制是"改 jobs.json 的 prompt，然后等它派发"。
用它来发一次性消息有个陷阱：prompt 会**留在文件里**，之后每 30 分钟
重复发一遍（2026-11-21 实测踩到，协调消息在 jobs.json 里残留了 3 份）。

本脚本的做法：
    1. 直接调 `opencode run --session <sid> --auto <message>`
    2. **完全不碰 jobs.json**
    3. 用 `inflight_<sid>.lock` 和 dispatcher 互斥，避免同一会话并发

用法：
    python scripts\\peer_send.py --list                    # 列出可发的会话
    python scripts\\peer_send.py --sid ses_xxx --text "..."  # 发文本
    python scripts\\peer_send.py --sid ses_xxx --file msg.md # 发文件内容
    python scripts\\peer_send.py --all --file msg.md         # 发给全部
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

BASE = r"D:\Tools\oc_prompt_ui"
JOBS = os.path.join(BASE, "jobs.json")

OPENCODE = os.path.join(
    os.environ.get("APPDATA", ""), "npm", "node_modules",
    "@opencode", "cli", "bin", "opencode.exe")
if not os.path.exists(OPENCODE):
    OPENCODE = "opencode"

RUN_TIMEOUT = 1800


def log(msg):
    try:
        with open(os.path.join(BASE, "peer_send.log"), "a",
                  encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def jobs():
    try:
        with open(JOBS, encoding="utf-8") as f:
            return json.load(f).get("jobs", [])
    except (OSError, ValueError):
        return []


def inflight_path(sid):
    safe = "".join(c for c in sid if c.isalnum() or c in "-_")
    return os.path.join(BASE, "inflight_%s.lock" % safe)


def send(sid, message, title=""):
    """发一条消息。返回 True/False。"""
    lock = inflight_path(sid)
    # 和 dispatcher 互斥：它派发时会建这个文件
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        print("  %s 正在被 dispatcher 派发，跳过" % (title or sid))
        return False
    try:
        args = [OPENCODE, "run", "--session", sid, "--auto", message]
        p = subprocess.run(
            args, cwd=os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=RUN_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = ((p.stdout or "") + (p.stderr or "")).replace("\n", " ").strip()
        print("  code=%s %s" % (p.returncode, out[:200]))
        log("SEND %s code=%s %s" % (title or sid, p.returncode, out[:200]))
        return p.returncode == 0
    except subprocess.TimeoutExpired:
        print("  超时 %ds（消息已送达，对方还在处理）" % RUN_TIMEOUT)
        log("SEND %s TIMEOUT" % (title or sid))
        return True          # 消息发出去了，只是没等到回复
    except Exception as e:  # noqa: BLE001
        print("  失败 %s: %s" % (type(e).__name__, e))
        log("SEND %s ERROR %s" % (title or sid, e))
        return False
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    js = jobs()
    if "--list" in argv or len(argv) == 0:
        print("可发的会话（来自 jobs.json）：")
        for j in js:
            print("  %-24s sid=%s enabled=%s"
                  % (j.get("title"), j.get("sessionID"), j.get("enabled")))
        print("\n用法：--sid <id> --text \"...\"  或  --all --file msg.md")
        return 0

    msg = None
    if "--text" in argv:
        msg = argv[argv.index("--text") + 1]
    elif "--file" in argv:
        path = argv[argv.index("--file") + 1]
        with open(path, encoding="utf-8") as f:
            msg = f.read()
    if not msg:
        print("缺少 --text 或 --file")
        return 2

    print("消息长度 %d 字" % len(msg))
    targets = []
    if "--all" in argv:
        targets = [j for j in js if j.get("enabled", True)]
    elif "--sid" in argv:
        sid = argv[argv.index("--sid") + 1]
        targets = [j for j in js if j.get("sessionID") == sid]
        if not targets:
            targets = [{"sessionID": sid, "title": sid}]
    else:
        print("需要 --all 或 --sid")
        return 2

    ok = 0
    for j in targets:
        print("\n-> %s" % (j.get("title") or j.get("sessionID")))
        if send(j["sessionID"], msg, j.get("title") or ""):
            ok += 1
    print("\n完成：%d/%d" % (ok, len(targets)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
