# -*- coding: utf-8 -*-
"""test_console.py —— 控制台（ui/console.*、src/ui_server.py）的自检。

为什么单独一个文件（2026-11-21）
--------------------------------
`test_ui.py` 原来是 1642 行 / 170 项，三个会话都在改它：

    [ui-overlay]  悬浮窗 DOM / 配色 / 窗口状态
    [ui-session]  控制台 API / 静态资源 / 日志尾推断 / 图鉴去重
    [主会话]      偶尔动 standalone 相关断言

`ui-overlay` 会话提的拆法（`docs/PLAN_TEST_SPLIT.md`）我同意了：
**按函数归属切，不按位置切** —— 因为 `test_fit_state_sync` /
`test_overlay_contrast` 虽然写在文件中间，但归属悬浮窗。

本文件收的是控制台那五组：

    [1]   /api/* 契约（含 dex 去重、team 技能展开、matches 分页、decision）
    [2]   静态资源
    [3]   live_state 契约
    [3c]  console.css 对比度（WCAG AA，token 现算）
    [3b]  日志尾推断（phase / board / progress / game_status /
          last_decision / log_chunk 中文截断）

留在 `test_ui.py` 的是悬浮窗那几组（DOM / 窗口状态 / 韧性 / 对比度）。
`[4.5] 标注页` 也留在 `test_ui.py` —— 它测的是控制台页面，但复用了
悬浮窗的 `DOM_WORKER`，搬出来要复制整个 worker，不划算。

跑法
----
    python src\\test_console.py            # 全部（约 20 秒，不需要 pywebview）
    python src\\test_console.py --keep     # 跑完不关服务，方便手工点开看

与 `test_ui.py` 共用 `test_ui_common.py` 的工具层（`ok` / `get` /
`wait_health` / `summary`），所以两边的汇总格式一致。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_ui_common import (  # noqa: E402
    ROOT, SRC, UI,
    ok, port_free, wait_health, get, get_raw,
    summary, reset,
)


def test_api(port):
    print("\n[1] ui_server API")
    endpoints = [
        ("/api/health", ("ok",)),
        ("/api/bot", ("running", "phase", "activity", "board", "game")),
        ("/api/stats", ("wins", "losses", "decided",
                        "season", "badge_stuck", "primary")),
        ("/api/matches?limit=3", ("items", "total", "offset")),
        ("/api/team", ("slots",)),
        ("/api/turns?limit=2", ("items",)),
        ("/api/decision", ()),
        ("/api/rounds?limit=2", ("items",)),
        ("/api/dex?limit=2", ("items", "total")),
        ("/api/log?offset=0", ("offset", "lines")),
        ("/api/portraits?side=foe&limit=3", ("items", "total")),
    ]
    for path, keys in endpoints:
        try:
            d = get(port, path)
            missing = [k for k in keys if k not in d]
            ok(not missing, path, "缺字段 %s" % missing if missing else "")
        except Exception as e:
            ok(False, path, str(e)[:60])

    # /api/dex 按名字去重：库里 857 条 implemented，但 103 个名字重复    # （「圣光迪莫」9 条，是同族皮肤变体）。图鉴显示 9 张同名卡会让人
    # 以为数据脏了。去重后应 683 条且名字全唯一。
    try:
        d = get(port, "/api/dex?limit=1000")
        items = d.get("items") or []
        names = [x.get("name") for x in items]
        ok(len(names) == len(set(names)), "图鉴名字唯一（已去重）",
           "%d 条 / %d 个名字" % (len(names), len(set(names))))
        ok(d.get("total") == len(items), "total 与去重后条数一致",
           "%s vs %s" % (d.get("total"), len(items)))
        ok(d.get("deduped") is True, "标记 deduped", str(d.get("deduped")))
        # 分页不能重叠（去重是在 SQL 里做的，分页才对）
        a = get(port, "/api/dex?limit=50&offset=0")
        b = get(port, "/api/dex?limit=50&offset=50")
        ia = {x.get("id") for x in (a.get("items") or [])}
        ib = {x.get("id") for x in (b.get("items") or [])}
        ok(not (ia & ib), "图鉴分页不重叠", "%d / %d" % (len(ia), len(ib)))
        # 搜索同样去重
        s = get(port, "/api/dex?q=%s&limit=50"
                % urllib.request.quote("圣光迪莫"))
        sn = [x.get("name") for x in (s.get("items") or [])]
        ok(len(sn) == len(set(sn)), "搜索也去重",
           "%s -> %d 条" % ("圣光迪莫", len(sn)))
    except Exception as e:
        ok(False, "/api/dex 去重", str(e)[:60])

    # /api/matches 分页：offset 必须真的切片，不能忽略
    try:
        a = get(port, "/api/matches?limit=3&offset=0")
        b = get(port, "/api/matches?limit=3&offset=3")
        ok(a.get("total") == b.get("total"), "分页 total 一致",
           "%s vs %s" % (a.get("total"), b.get("total")))
        ia = [x.get("mid") for x in (a.get("items") or [])]
        ib = [x.get("mid") for x in (b.get("items") or [])]
        ok(not (set(ia) & set(ib)), "offset=3 与 offset=0 不重叠",
           "%s / %s" % (ia[:2], ib[:2]))
        ok(b.get("offset") == 3, "offset 回显", str(b.get("offset")))
    except Exception as e:
        ok(False, "/api/matches 分页", str(e)[:60])

    # /api/team 必须把 moves 展开成技能（名字/属性/威力/耗能）。
    # 配置过期是最隐蔽的 bug（docs/AGENTS.md），UI 不露出来就没法人工核对。
    try:
        t = get(port, "/api/team")
        slots = t.get("slots") or []
        ok(len(slots) > 0, "/api/team 有槽位", "%d 个" % len(slots))
        if slots:
            s0 = slots[0]
            ok("skills" in s0, "槽位带 skills 展开", str(list(s0.keys())[:8]))
            sk = s0.get("skills") or []
            ok(len(sk) == len(s0.get("moves") or []),
               "skills 数与 moves 数一致",
               "%d vs %d" % (len(sk), len(s0.get("moves") or [])))
            # 至少一个技能能查到名字（全查不到说明 id 或联表错了）
            named = [x for x in sk if x.get("name")]
            ok(bool(named) or not sk, "技能能查到名字",
               "%d/%d" % (len(named), len(sk)))
            # 查不到的必须留 {id, name: None}，不能静默丢掉
            for x in sk:
                if not x.get("name"):
                    ok("id" in x, "查不到的技能保留 id", str(x))
                    break
    except Exception as e:
        ok(False, "/api/team 技能展开", str(e)[:60])

    # 战绩口径（2026-11-21）：必须以"游戏内累计战绩"为主。
    #
    # 背景：results.jsonl 里 92% 是 flat，其中 100 条是段位卡读数卡死
    # 在 2/3 造成的假平局。拿它算胜率得到 66.7%，而游戏自己记的是 44.8%。
    try:
        d = get(port, "/api/stats")
        ok(d.get("primary") in ("season", "log"), "/api/stats.primary",
           str(d.get("primary")))
        s = d.get("season")
        if s:
            want = round(100.0 * s["wins"] / max(1, s["decided"]), 1)
            ok(s["rate"] == want, "season.rate 自洽",
               "%d/%d = %.1f%%" % (s["wins"], s["decided"], s["rate"]))
            ok(s["decided"] == s["wins"] + s["losses"],
               "season.decided = wins + losses")
        else:
            ok(True, "season 未读取（机器人还没进过大厅）")
        ok(isinstance(d.get("badge_stuck"), bool),
           "badge_stuck 是布尔", str(d.get("badge_stuck")))
        # 卡死时不能让前端以为胜率可信
        if d.get("badge_stuck"):
            ok(d.get("reliable") is False,
               "段位卡卡死时 reliable 必须为 False")
    except Exception as e:
        ok(False, "/api/stats 口径", str(e)[:60])

    # /api/state 允许为 null（机器人没跑）
    #
    # ★ 超时要给足（2026-11-21）
    # -------------------------
    # 这个接口在独立模式下要现场抓屏 + 识别，**首次请求还会冷加载 ONNX 模型**
    # （实测 4~6 秒）。默认 6s 超时会偶发失败，而失败原因跟被测代码无关
    # —— 只是这一次恰好赶上了冷启动。给 25 秒，把"慢"和"坏"区分开。
    try:
        st = get(port, "/api/state", timeout=25)
        ok(True, "/api/state", "null（机器人没跑）" if st is None else "有数据")
    except Exception as e:
        ok(False, "/api/state", str(e)[:60])


# ============================================================ 2. 静态资源
def test_static(port):
    print("\n[2] 静态资源")
    files = [
        ("/index.html", b"panel"),
        ("/style.css", b"--alpha"),
        ("/app.js", b"bindAlpha"),
        ("/console.html", b"healthGrid"),
        ("/console.css", b".health"),
        ("/console.js", b"refreshHealth"),
    ]
    for path, needle in files:
        try:
            code, body = get_raw(port, path)
            ok(code == 200 and needle in body, path,
               "%d bytes" % len(body))
        except Exception as e:
            ok(False, path, str(e)[:60])

    # 头像（通过 junction）
    for name in ("音速犬", "水灵"):
        p = "/portraits/%s.png" % urllib.request.quote(name)
        try:
            code, body = get_raw(port, p)
            ok(code == 200 and len(body) > 1000, "头像 %s" % name,
               "%d bytes" % len(body))
        except Exception:
            # 回退目录
            p2 = "/portraits_foe/%s.png" % urllib.request.quote(name)
            try:
                code, body = get_raw(port, p2)
                ok(code == 200 and len(body) > 1000,
                   "头像 %s（回退目录）" % name, "%d bytes" % len(body))
            except Exception as e:
                ok(False, "头像 %s" % name, str(e)[:60])


# ============================================================ 3. 契约
def test_contract():
    print("\n[3] live_state 契约")
    sys.path.insert(0, SRC)
    try:
        import live_state as LS
    except Exception as e:
        ok(False, "import live_state", str(e)[:60])
        return

    st = LS.build(
        turn=12, mode="battle",
        me=LS.me_block("测试", ["火"], 50.0, 100, 200, 3, 10),
        foe=LS.foe_block("对手", ["水"], 40.0, (300, 400), (150, 200)),
        skills=[LS.skill_block(1, None, None, None, 100, 50.0)],
    )
    ok(st["me"]["hp_pct"] == 50.0, "me.hp_pct 保留")
    ok(st["foe"]["hp_range"] == [300, 400], "hp_range 转 list")

    s2 = LS.build(me=LS.me_block(None, [], None),
                  foe=LS.foe_block(None, [], None))
    ok(s2["me"]["hp_pct"] is None, "未知血量是 None（不是 0）")
    ok(s2["turn"] is None, "未知回合是 None")
    ok(s2["me"]["name"] is None, "未知名字是 None")

    s3 = LS.build(me=LS.me_block("x", [], 150.0))
    ok(s3["me"]["hp_pct"] == 100.0, "血量上限夹到 100")

    s4 = LS.build(foe=LS.foe_block("y", [], -5.0))
    ok(s4["foe"]["hp_pct"] == 0.0, "血量下限夹到 0")


# ============================================================ 4. DOM
DOM_WORKER = r'''
import ctypes
import ctypes.wintypes
import json, os, sys, time, threading
import webview

port = int(sys.argv[1])
out = sys.argv[2]
result = {}
UD = ctypes.windll.user32

win = webview.create_window(
    "uitest", url="http://127.0.0.1:%d/index.html" % port,
    width=353, height=700, x=-2000, y=-2000,   # 移出屏幕，不干扰用户
    frameless=True, transparent=True, on_top=False, focus=False)


def worker():
    try:
        for _ in range(150):
            time.sleep(0.2)
            if win.native is not None:
                break
        time.sleep(4.0)

        win.evaluate_js("document.body.classList.add('embedded')")
        time.sleep(0.9)

        result["ids"] = win.evaluate_js("""
          (() => {
            const need = ['panel','overlay','hdr','skills','verdict',
                          'meName','mePct','foeName','foePct',
                          'meEnergy','foeHpRange','foeNature',
                          'muOut','muIn','muTag','alpha','btnPass','btnFold'];
            return JSON.stringify(need.filter(id => !document.getElementById(id)));
          })()
        """)
        result["overflow"] = win.evaluate_js("""
          (() => {
            const vw = window.innerWidth;
            const bad = [];
            document.querySelectorAll('#panel *').forEach(el => {
              const r = el.getBoundingClientRect();
              if (r.right > vw + 1) bad.push(el.className || el.tagName);
            });
            return JSON.stringify(bad.slice(0, 5));
          })()
        """)
        result["dims"] = win.evaluate_js("""
          (() => {
            const p = document.getElementById('panel');
            return JSON.stringify({
              w: p.offsetWidth, sw: p.scrollWidth,
              vw: window.innerWidth, dpr: devicePixelRatio
            });
          })()
        """)
        # 窗口物理尺寸（必须 DPI-aware 才是真值，见 docs §五之四）
        # 注意：SetProcessDpiAwarenessContext 必须在创建窗口**之前**调，
        # 这里窗口已经建好了，调它没有意义还可能干扰 WebView2。
        # 所以只读不设，接受"可能读到逻辑值"的局限。
        try:
            hwnd = UD.GetAncestor(win.native.Handle.ToInt32(), 2) or \
                win.native.Handle.ToInt32()
            r = ctypes.wintypes.RECT()
            UD.GetWindowRect(hwnd, ctypes.byref(r))
            result["win"] = json.dumps({
                "w": r.right - r.left, "h": r.bottom - r.top})
        except Exception as e:
            result["win"] = json.dumps({"err": str(e)[:60]})
        win.evaluate_js(
            "document.dispatchEvent(new KeyboardEvent('keydown',"
            "{key:'a', altKey:true}))")
        time.sleep(0.35)
        result["alphaAfterAltA"] = win.evaluate_js(
            "document.getElementById('alpha').value")

        win.evaluate_js(
            "document.dispatchEvent(new KeyboardEvent('keydown',"
            "{key:'t', altKey:true}))")
        time.sleep(0.35)
        result["foldedAfterAltT"] = win.evaluate_js(
            "document.getElementById('overlay').classList.contains('folded')")
    except Exception:
        import traceback
        result["error"] = traceback.format_exc()
    finally:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        os._exit(0)


threading.Thread(target=worker, daemon=True).start()
webview.start()
'''


def test_contrast():
    """设计 token 的对比度回归。

    背景（2026-11-21）：`--ink-3` 原来是 rgba(..., .32)，在 bg-1 上只有
    2.73:1，而它承担的正是 `.card-sub` / `.nav-label` / placeholder
    这些**解释性文字**。这里把它钉住，防止以后又被"调淡一点"。
    """
    print("\n[3c] 对比度（WCAG AA）")
    import re

    def lin(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    def lum(rgb):
        r, g, b = (lin(x) for x in rgb)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    def ratio(a, b):
        la, lb = lum(a), lum(b)
        hi, lo = max(la, lb), min(la, lb)
        return (hi + 0.05) / (lo + 0.05)

    try:
        with open(os.path.join(UI, "console.css"), encoding="utf-8") as f:
            css = f.read()
    except OSError as e:
        ok(False, "读 console.css", str(e)[:60])
        return

    bgs = {}
    for name in ("bg-0", "bg-1", "bg-2", "bg-3"):
        m = re.search(r"--%s:\s*#([0-9A-Fa-f]{6})" % name, css)
        if m:
            h = m.group(1)
            bgs[name] = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    ok(len(bgs) == 4, "解析 4 个底色 token", str(sorted(bgs)))

    def check(name, min_ratio):
        m = re.search(
            r"--%s:\s*rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)"
            % name, css)
        if not m:
            ok(False, "解析 --%s" % name)
            return
        fg = tuple(int(m.group(i)) for i in (1, 2, 3))
        a = float(m.group(4))
        worst_name, worst = None, 99.0
        for bn, bg in bgs.items():
            mixed = tuple(round(a * f + (1 - a) * b) for f, b in zip(fg, bg))
            r = ratio(mixed, bg)
            if r < worst:
                worst_name, worst = bn, r
        ok(worst >= min_ratio, "--%s >= %.1f:1" % (name, min_ratio),
           "最差 %s = %.2f:1" % (worst_name, worst))

    check("ink-1", 7.0)
    check("ink-2", 5.0)
    check("ink-3", 4.5)
    ok("--ink-4" in css, "保留装饰级 token --ink-4")


# ============================================================ 3c2. 悬浮窗对比度
def test_phase_infer():
    """`/api/bot` 的 phase 全部来自日志尾推断，这里用临时日志验证。

    为什么必须测（2026-11-21）：板子掉线时机器人会在
    "BLE waiting for board …" 里循环 30 分钟，而进程、日志时间都正常。
    如果 phase 判错，控制台就会一直显示绿色"运行中" —— 这正是当天的
    故障现场。所以这组断言是防回归的。
    """
    print("\n[3b] 日志尾推断（phase / board / progress）")
    sys.path.insert(0, SRC)
    try:
        import ui_server as U
    except Exception as e:
        ok(False, "import ui_server", str(e)[:60])
        return

    import tempfile

    old_bot = U.BOT_LOG
    fd, bot_path = tempfile.mkstemp(suffix=".log")
    os.close(fd)
    try:
        U.BOT_LOG = bot_path
        cases = [
            ("waiting_board", "20:00:01 BLE waiting for board …\n"),
            ("ble_failed", "20:00:01 BLE FAILED - 180s 内没等到板子，退出\n"),
            ("focus_lost", "20:00:01 FOCUS LOST - key 3 dropped (fg=1 game=2)\n"),
            ("board_offline",
             "20:00:01 BLE key space failed (board offline), attempt 1\n"),
            ("running",
             "20:00:01 state battle=True bar=True me=82.4 foe=33.1\n"),
        ]
        for want, line in cases:
            with open(bot_path, "w", encoding="utf-8") as f:
                f.write(line)
            got = U.bot_activity().get("event")
            ok(got == want, "activity %s" % want, "got=%s" % got)

        # 后面的心跳行必须覆盖前面的等待状态（否则"恢复了"也一直报卡住）
        with open(bot_path, "w", encoding="utf-8") as f:
            f.write("20:00:01 BLE waiting for board …\n"
                    "20:00:20 bot up; rect=(0, 0, 1, 1)\n")
        got = U.bot_activity().get("event")
        ok(got == "running", "心跳覆盖旧的等待状态", "got=%s" % got)
        ok(U.bot_activity().get("counts", {}).get("waiting_board") == 1,
           "counts 记录最近窗口里的次数",
           str(U.bot_activity().get("counts")))
    finally:
        U.BOT_LOG = old_bot
        try:
            os.remove(bot_path)
        except OSError:
            pass

    # 机器人日志里混着 `[standalone]`（UI 服务自己写的感知日志）。
    # 必须验证它不会把机器人事件挤出去 —— 2026-11-21 实测 48KB 尾部
    # 一条机器人事件都没有，phase 推断整个失效。
    old_bot2 = U.BOT_LOG
    fd, mix_path = tempfile.mkstemp(suffix=".log")
    os.close(fd)
    try:
        U.BOT_LOG = mix_path
        with open(mix_path, "w", encoding="utf-8") as f:
            f.write("20:00:01 BLE waiting for board …\n")
            # 大量旁路日志把它压到 16KB 之外
            for i in range(400):
                f.write("20:00:%02d [standalone] FOE -> 水灵 (onnx, 0.97)\n"
                        % (i % 60))
        act = U.bot_activity()
        ok(act.get("event") == "waiting_board",
           "standalone 日志不掩盖机器人事件", str(act.get("event")))
    finally:
        U.BOT_LOG = old_bot2
        try:
            os.remove(mix_path)
        except OSError:
            pass

    # /api/log 按字节 offset 增量拉：中文日志被切在多字节字符中间时，
    # 旧实现会少推进 2~3 字节 -> 下次重复输出同一行（或丢行）。
    # 这个项目的日志全是中文，几乎每次都会命中。
    import io as _io
    old_log = U.BOT_LOG
    fd, logp = tempfile.mkstemp(suffix=".log")
    os.close(fd)
    try:
        U.BOT_LOG = logp
        with _io.open(logp, "w", encoding="utf-8", newline="\n") as f:
            for i in range(120):
                f.write("23:%02d 中文日志测试第 %d 行 —— 音速犬 出招 SKILL\n"
                        % (i % 60, i))
        size = os.path.getsize(logp)
        whole = U.log_chunk(0, max_bytes=size)
        ok(len(whole["lines"]) == 120, "整文件一次读完 120 行",
           str(len(whole["lines"])))
        ok(whole["offset"] == size, "offset 到达 EOF")

        with open(logp, "rb") as f:
            raw = f.read()
        cut = 401
        while raw[cut - 1:cut] == b"\n":
            cut += 1
        with open(logp, "wb") as f:
            f.write(raw[:cut])
        part = U.log_chunk(0, max_bytes=cut)
        with open(logp, "rb") as f:
            f.seek(part["offset"] - 1)
            on_nl = f.read(1) == b"\n"
        ok(on_nl, "中文字符中间切断后 offset 落在换行边界",
           "offset=%d cut=%d" % (part["offset"], cut))

        with open(logp, "wb") as f:
            f.write(raw)
        rest = U.log_chunk(part["offset"], max_bytes=size - part["offset"])
        joined = part["lines"] + rest["lines"]
        ok(len(joined) == len(set(joined)), "续拉不重复",
           "%d 行 / %d 唯一" % (len(joined), len(set(joined))))
        ok(len(joined) == 120, "续拉后总行数不变", str(len(joined)))
    finally:
        U.BOT_LOG = old_log
        try:
            os.remove(logp)
        except OSError:
            pass

    # 游戏窗口：只读 HWND/rect。这里验证的是"算得对"，不是"能找到窗口"——
    # 测试机可能没开游戏，所以两种情况都要能通过。
    #
    # 还要验证 **DPI 声明**：未声明时 Win32 返回逻辑坐标，同一台机器上
    # 会读到 1037x804（真实 2074x1607 的一半）。这个差异会让分辨率告警
    # 报出错误的数字，用户照着改会改错。所以断言"读到的是物理像素"。
    try:
        g = U.game_status()
        ok(isinstance(g, dict) and "found" in g, "game_status 结构",
           str(list(g.keys())))
        if g.get("found"):
            ok(g.get("aspect") is not None, "算出窗口长宽比",
               "%.3f" % (g.get("aspect") or 0))
            ok(isinstance(g.get("scale_ok"), bool), "scale_ok 是布尔",
               str(g.get("scale_ok")))
            # DPI：物理像素下的窗口宽度不该是 1000 出头（那是 200% 缩放
            # 下的逻辑值）。放宽到 >1200 只挡"明显读成逻辑坐标"这一种错。
            ok(int(g.get("w") or 0) > 1200 or int(g.get("h") or 0) > 900,
               "读到的是物理像素（DPI 已声明）",
               "%sx%s" % (g.get("w"), g.get("h")))
            # w/h 必须是**客户区**，和 Screen.scale() 同口径。
            # 实测这台机器：客户区 2048x1536，窗口矩形 2074x1607（含边框）。
            # 报窗口矩形会让用户按错误尺寸去调游戏。
            win = g.get("win") or [None, None]
            ok(win[0] is not None and int(g["w"]) <= int(win[0]),
               "w/h 是客户区（不大于窗口矩形）",
               "client=%sx%s win=%sx%s" % (g["w"], g["h"], win[0], win[1]))
            if g.get("scale_ok") is False:
                ok(bool(g.get("note")), "比例不一致时给出说明",
                   str(g.get("note"))[:40])
        else:
            ok(g.get("note"), "找不到窗口时说明原因", str(g.get("note"))[:40])
    except Exception as e:
        ok(False, "game_status", str(e)[:60])

    old_esp = U.ESP_LOG
    fd, esp_path = tempfile.mkstemp(suffix=".log")
    os.close(fd)
    try:
        U.ESP_LOG = esp_path
        for want, line in [
            ("connected", "19:20:13 connected 14:C1:9F:3A:BB:35 (cached)\n"),
            ("offline", "19:21:13 link down, reconnecting (#1)\n"),
            ("offline",
             "19:21:23 cached->14:C1:9F:3A:BB:35 failed: Device with "
             "address 14:C1:9F:3A:BB:35 was not found.\n"),
            ("idle", "19:22:13 daemon up (waiting=1)\n"),
        ]:
            with open(esp_path, "w", encoding="utf-8") as f:
                f.write(line)
            got = U.board_status().get("state")
            ok(got == want, "board %s" % want, "got=%s" % got)
    finally:
        U.ESP_LOG = old_esp
        try:
            os.remove(esp_path)
        except OSError:
            pass

    # decision：从 turns.jsonl 尾部捞"最后一条真的出招"
    old_turns = U.TURNS
    fd, turn_path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    try:
        U.TURNS = turn_path
        U.CACHE.invalidate()
        ok(U.last_decision() is None, "无回合记录时 decision=None")

        with open(turn_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "10:00:01", "turn": 1,
                                "action": ["skill", 1], "why": "first",
                                "my_pct": 100.0, "foe_pct": 100.0}) + "\n")
            # 只有轮询行（action 为空）不该被当成决策
            f.write(json.dumps({"ts": "10:00:02", "turn": 1,
                                "action": [], "why": None}) + "\n")
            f.write(json.dumps({"ts": "10:00:03", "turn": 2,
                                "action": ["switch", 3],
                                "why": "dying this turn",
                                "my_pct": 12.0, "foe_pct": 40.0}) + "\n")
        U.CACHE.invalidate()
        d = U.last_decision() or {}
        ok(d.get("action") == ["switch", 3], "取最后一条真决策", str(d.get("action")))
        ok(d.get("turn") == 2 and d.get("why") == "dying this turn",
           "带回合号和理由", str(d.get("why")))
        ok(isinstance(d.get("age"), float), "算出 age（秒）", str(d.get("age")))
    finally:
        U.TURNS = old_turns
        U.CACHE.invalidate()
        try:
            os.remove(turn_path)
        except OSError:
            pass

    # progress：没有快照必须是 None，不能是 0 —— 前端要显示"—"而不是 0%
    old_snaps = U.SNAPS
    fd, snap_path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    try:
        U.SNAPS = snap_path
        U.CACHE.invalidate()
        ok(U._progress() is None, "无快照时 progress=None（不是 0）")
        today = time.strftime("%Y-%m-%d")
        with open(snap_path, "w", encoding="utf-8") as f:
            for i, (dw, dl) in enumerate([(1, 0), (0, 1), (1, 0)]):
                f.write(json.dumps({
                    "ts": "%s 10:0%d:00" % (today, i),
                    "wins": 100 + i, "losses": 200 + i,
                    "dw": dw, "dl": dl,
                }) + "\n")
        U.CACHE.invalidate()
        p = U._progress() or {}
        t = p.get("today") or {}
        ok(t.get("wins") == 2 and t.get("losses") == 1,
           "今日增量 2 胜 1 负", str(t))
        ok(t.get("rate") == 66.7, "今日胜率 66.7%", str(t.get("rate")))
        ok((p.get("recent20") or {}).get("decided") == 3, "近 20 局 3 场",
           str(p.get("recent20")))
    finally:
        U.SNAPS = old_snaps
        U.CACHE.invalidate()
        try:
            os.remove(snap_path)
        except OSError:
            pass


# ============================================================ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0,
                    help="指定端口（默认自动挑一个空闲的）")
    ap.add_argument("--keep", action="store_true",
                    help="跑完不关服务，方便手工点开看")
    args = ap.parse_args()

    print("=" * 66)
    print("控制台自检")
    print("=" * 66)

    port = args.port
    if not port:
        port = 8818
        if not port_free(port):
            for p in range(8820, 8840):
                if port_free(p):
                    port = p
                    break

    srv = subprocess.Popen(
        [sys.executable, os.path.join(SRC, "ui_server.py"),
         "--port", str(port)],
        cwd=ROOT, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=0x08000000 | 0x00000008)

    try:
        if not wait_health(port):
            print("服务启动失败（端口 %d）" % port)
            return 2
        print("服务已起：http://127.0.0.1:%d" % port)

        test_api(port)
        test_static(port)
        test_contract()
        test_contrast()
        test_phase_infer()
    finally:
        if args.keep:
            print("服务保留在 %d（--keep）" % port)
        else:
            try:
                srv.terminate()
            except Exception:
                pass

    return summary()


if __name__ == "__main__":
    sys.exit(main())
