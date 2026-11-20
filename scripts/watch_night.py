# -*- coding: utf-8 -*-
"""watch_night.py —— 整晚运行的健康监控（只读，不干预机器人）。

每 N 分钟扫一次日志，发现以下情况就记录告警：
    1. 超过 X 秒没有新动作（可能卡住）
    2. BLE 离线
    3. 进程重启
    4. 焦点丢失
    5. 出招间隔中位数变差

用法
----
    python scripts\\watch_night.py                # 单次快照
    python scripts\\watch_night.py --loop 300     # 每 5 分钟一次
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time

ROOT = r"D:\Projects\RocoAgent"
LOG = os.path.join(ROOT, "logs", "bot_stdout.log")
ALERT = os.path.join(ROOT, "logs", "night_alerts.jsonl")


def tail_rows(minutes=30):
    """取最近 N 分钟的日志行（按行数近似：每分钟约 20 行）。"""
    try:
        size = os.path.getsize(LOG)
        with io.open(LOG, encoding="utf-8", errors="replace") as f:
            f.seek(max(0, size - minutes * 20 * 120))
            return f.read().split("\n")
    except OSError:
        return []


def parse(rows):
    out = []
    for l in rows:
        m = re.match(r"^(\d{2}):(\d{2}):(\d{2}) (.*)$", l)
        if m:
            t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            out.append((t, m.group(4)))
    return out


def snapshot():
    rows = parse(tail_rows(40))
    if not rows:
        return {"status": "no-log"}

    now = time.localtime()
    now_s = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
    last_t, last_s = rows[-1]
    # 跨零点处理
    idle = (now_s - last_t) % 86400

    acts = [(t, s) for t, s in rows if re.match(r"T\d+\s", s)]
    gaps = []
    for i in range(1, len(acts)):
        g = (acts[i][0] - acts[i - 1][0]) % 86400
        if 0 < g < 400:
            gaps.append(g)
    gaps.sort()

    info = {
        "at": time.strftime("%H:%M:%S"),
        "idle_s": idle,
        "actions_40min": len(acts),
        "gap_median": gaps[len(gaps) // 2] if gaps else None,
        "gap_p90": gaps[int(len(gaps) * 0.9)] if gaps else None,
        "ble_offline": sum(1 for _t, s in rows if "board offline" in s),
        "focus_lost": sum(1 for _t, s in rows if "FOCUS LOST" in s),
        "stuck": sum(1 for _t, s in rows if "STUCK" in s),
        "boots": sum(1 for _t, s in rows if "boot pid=" in s),
        "exits": sum(1 for _t, s in rows if "exited rc=" in s),
        "lip_channel": sum(1 for _t, s in rows if "立绘通道" in s),
        "last": last_s[:70],
    }
    alerts = []
    if idle > 180:
        alerts.append("idle %ds (no log output)" % idle)
    if info["ble_offline"] > 6:
        alerts.append("BLE offline x%d" % info["ble_offline"])
    if info["exits"] > 0:
        alerts.append("process exited x%d" % info["exits"])
    if info["gap_median"] and info["gap_median"] > 30:
        alerts.append("slow turns: median %ds" % info["gap_median"])
    info["alerts"] = alerts
    return info


def main():
    loop_s = 0
    if "--loop" in sys.argv:
        loop_s = int(sys.argv[sys.argv.index("--loop") + 1])

    while True:
        info = snapshot()
        line = json.dumps(info, ensure_ascii=False)
        print(line)
        if info.get("alerts"):
            try:
                with io.open(ALERT, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass
        if not loop_s:
            break
        time.sleep(loop_s)


if __name__ == "__main__":
    main()
