# -*- coding: utf-8 -*-
"""test_ui.py —— 前端自检（不需要游戏，不需要机器人）。

为什么需要
----------
前端的验证一直是靠临时脚本做的，散在临时目录里，下次接手的人没法复现。
这个脚本把能自动化的检查固化下来：

  1. ui_server 的所有 API 能返回合法 JSON
  2. live_state 契约（复用 test_live_state 的检查点）
  3. 静态资源可访问（index/console/css/js/头像）
  4. 面板 DOM 结构完整（用 pywebview 真实渲染）
  5. 快捷键、折叠、透明度、穿透的交互逻辑

跑法
----
    python src/test_ui.py              # 全部（约 40 秒）
    python src/test_ui.py --no-dom     # 跳过需要渲染的部分（3 秒）
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
UI = os.path.join(ROOT, "ui")

FAILS = []
PASSES = 0


def ok(cond, msg, detail=""):
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


# ============================================================ 1. API
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


def test_dom(port):
    print("\n[4] 面板 DOM（真实渲染，子进程）")
    import tempfile
    import threading

    tmp = os.path.join(tempfile.gettempdir(), "roco_ui_dom.json")
    try:
        os.remove(tmp)
    except OSError:
        pass

    script = os.path.join(tempfile.gettempdir(), "roco_ui_dom_worker.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(DOM_WORKER)

    p = subprocess.Popen(
        [sys.executable, script, str(port), tmp],
        cwd=ROOT, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=0x08000000)

    # 最多等 40 秒
    end = time.time() + 40
    while time.time() < end:
        if os.path.exists(tmp):
            break
        time.sleep(0.4)

    try:
        p.terminate()
    except Exception:
        pass

    if not os.path.exists(tmp):
        ok(False, "DOM 测试超时（40s 内没出结果）")
        return

    try:
        with open(tmp, encoding="utf-8") as f:
            result = json.load(f)
    except Exception as e:
        ok(False, "DOM 结果解析", str(e)[:60])
        return

    if "error" in result:
        ok(False, "DOM 渲染", result["error"][:120])
        return

    miss = json.loads(result.get("ids") or "[]")
    ok(not miss, "所有关键元素存在", ("缺 %s" % miss) if miss else "")

    bad = json.loads(result.get("overflow") or "[]")
    ok(not bad, "无元素横向溢出", str(bad) if bad else "")

    dims = json.loads(result.get("dims") or "{}")
    ok(dims.get("sw", 9999) <= dims.get("vw", 0),
       "面板不超出视口",
       "sw=%s vw=%s" % (dims.get("sw"), dims.get("vw")))

    # 窗口尺寸合理性：不检查精确比例（外部读到的口径可能是逻辑值，
    # 见 docs §五之四），只要求"窗口不是 0 且不比视口小"。
    win_sz = json.loads(result.get("win") or "{}")
    got_w = win_sz.get("w") or 0
    got_h = win_sz.get("h") or 0
    ok(got_w > 100 and got_h > 100,
       "窗口尺寸合理", "%dx%d" % (got_w, got_h))

    ok(result.get("alphaAfterAltA") not in (None, "100", 100),
       "Alt+A 改了透明度", "alpha=%s" % result.get("alphaAfterAltA"))
    ok(result.get("foldedAfterAltT") is True,
       "Alt+T 折叠了", "folded=%s" % result.get("foldedAfterAltT"))


# ============================================================ 4.5 标注
def test_label(port):
    print("\n[4.5] 头像标注 API")

    def post(path, body):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (port, path), data=data,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    # 列表
    try:
        d = get(port, "/api/portraits?side=foe&limit=3")
        ok("items" in d and "total" in d, "待标注列表", "total=%s" % d.get("total"))
        ok("groups" in d, "同款分组字段存在")
    except Exception as e:
        ok(False, "待标注列表", str(e)[:60])

    # 非法 side
    try:
        d = get(port, "/api/portraits?side=bogus")
        ok(d.get("total") == 0, "非法 side 返回空")
    except Exception as e:
        ok(False, "非法 side", str(e)[:60])

    # 缺参数 -> 拒绝
    try:
        r = post("/api/portrait/label", {"side": "foe", "stem": "", "species": ""})
        ok(r.get("ok") is False, "缺参数被拒", r.get("msg"))
    except Exception as e:
        ok(False, "缺参数", str(e)[:60])

    # 不存在的文件 -> 拒绝（不会误删）
    try:
        r = post("/api/portrait/label",
                 {"side": "foe", "stem": "_unknown_NOSUCHFILE",
                  "species": "水灵"})
        ok(r.get("ok") is False, "不存在的文件被拒", r.get("msg"))
    except Exception as e:
        ok(False, "不存在的文件", str(e)[:60])

    # 不存在的物种 -> 拒绝
    try:
        r = post("/api/portrait/label",
                 {"side": "foe", "stem": "_unknown_NOSUCHFILE",
                  "species": "绝对不存在的精灵ZZZ"})
        ok(r.get("ok") is False, "未知物种被拒", r.get("msg"))
    except Exception as e:
        ok(False, "未知物种", str(e)[:60])

    # 模糊匹配：错字能救回库里名字，乱码要拒绝
    try:
        sys.path.insert(0, SRC)
        import ui_server as _U
        ok(_U._safe_species("冥古龙") == "冥古龙", "精确匹配")
        got = _U._safe_species("冥古尤")
        ok(got == "冥古龙", "编辑距离匹配（错字）", "冥古尤 -> %s" % got)
        ok(_U._safe_species("07417") is None, "数字被拒")
        ok(_U._safe_species("0/366") is None, "血量被拒")
        ok(_U._safe_species("") is None, "空串被拒")
    except Exception as e:
        ok(False, "模糊匹配", str(e)[:60])

    # OCR 结果文件（可能还没跑过，不算失败）
    try:
        p = os.path.join(ROOT, "logs", "ocr_names.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            n_ok = sum(1 for v in d.values() if v.get("text"))
            ok(len(d) > 0, "OCR 结果文件可读",
               "%d 条，有效 %d" % (len(d), n_ok))
        else:
            ok(True, "OCR 结果文件（还没跑，跳过）")
    except Exception as e:
        ok(False, "OCR 结果文件", str(e)[:60])

    # 批量确认：只接受精确匹配 + 高置信度
    try:
        import ui_server as _U
        r = _U.portrait_auto_confirm("foe", min_conf=0.9, dry_run=True)
        ok(r.get("dry_run") is True, "批量确认 dry_run 模式")
        ok("n" in r, "批量确认返回候选数", "n=%s" % r.get("n"))
        # 所有候选必须是精确匹配（模糊匹配不进批量）
        cands = r.get("candidates") or []
        bad = [c for c in cands
               if _U._safe_species(c["species"]) != c["species"]]
        ok(not bad, "候选全是精确匹配", str(bad[:2]) if bad else "")
        ok(all(c["conf"] >= 0.9 for c in cands),
           "候选置信度都 >= 0.9")
    except Exception as e:
        ok(False, "批量确认", str(e)[:60])

    # 垃圾检测：场景截图判 False，名字条判 True
    try:
        import tempfile
        import cv2
        import numpy as np
        import ui_server as _U

        tmpd = tempfile.mkdtemp()
        # 场景渐变（无文字）-> 应判 False
        junk = np.zeros((55, 210, 3), dtype=np.uint8)
        for y in range(55):
            junk[y, :] = (120 + y, 130 + y, 140 + y)
        jp = os.path.join(tmpd, "junk.png")
        cv2.imwrite(jp, junk)
        ok(_U._looks_like_nameplate(jp) is False, "场景截图被判为垃圾")

        # 白字黑底 -> 应判 True
        keep = np.zeros((55, 210, 3), dtype=np.uint8)
        keep[:, :] = (20, 20, 25)
        cv2.putText(keep, "AB", (20, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
                    (250, 250, 250), 3)
        kp = os.path.join(tmpd, "keep.png")
        cv2.imwrite(kp, keep)
        ok(_U._looks_like_nameplate(kp) is True, "名字条不被判为垃圾")

        # 清理临时目录
        import shutil
        shutil.rmtree(tmpd, ignore_errors=True)
    except Exception as e:
        ok(False, "垃圾检测", str(e)[:60])


# ============================================================ 5. 窗口状态
def test_window_state():
    print("\n[5] 窗口状态持久化")
    sys.path.insert(0, SRC)
    try:
        import overlay as OV
    except Exception as e:
        ok(False, "import overlay", str(e)[:60])
        return

    import json as _json

    # 备份现有状态
    backup = None
    if os.path.exists(OV._STATE_FILE):
        try:
            with open(OV._STATE_FILE, encoding="utf-8") as f:
                backup = f.read()
        except Exception:
            pass

    try:
        # 1. 写入后能读回
        OV.save_window_state(x=700, y=400, w=353, h=468, folded=True)
        st = OV.load_window_state()
        ok(st.get("x") == 700 and st.get("y") == 400,
           "位置往返", str(st))
        ok(st.get("w") == 353 and st.get("h") == 468, "尺寸往返")
        ok(st.get("folded") is True, "折叠状态往返")

        # 2. 部分更新不清掉其它字段
        OV.save_window_state(x=800)
        st2 = OV.load_window_state()
        ok(st2.get("x") == 800 and st2.get("y") == 400,
           "部分更新保留其它字段", str(st2))

        # 3. 文件不存在时返回空 dict（不抛异常）
        try:
            os.remove(OV._STATE_FILE)
        except OSError:
            pass
        st3 = OV.load_window_state()
        ok(st3 == {}, "文件不存在时返回空 dict", str(st3))

        # 4. 没有残留 .tmp
        ok(not os.path.exists(OV._STATE_FILE + ".tmp"), "无残留 .tmp")
    finally:
        # 恢复
        try:
            if backup is not None:
                with open(OV._STATE_FILE, "w", encoding="utf-8") as f:
                    f.write(backup)
            else:
                try:
                    os.remove(OV._STATE_FILE)
                except OSError:
                    pass
        except Exception:
            pass


# ============================================================ 6. 韧性
RESILIENCE_WORKER = r'''
import json, os, sys, time, threading
import webview

port = int(sys.argv[1])
iso = sys.argv[2]
out = sys.argv[3]
result = {}

win = webview.create_window(
    "rtest", url="http://127.0.0.1:%d/index.html" % port,
    width=353, height=600, x=-2000, y=-2000,
    frameless=True, transparent=True, on_top=False, focus=False)


def write_state(turn, hp, n):
    sk = [{"i": i, "name": None, "element": None, "cost": None,
           "dmg": v, "pct": p, "usable": True, "note": None}
          for i, (v, p) in enumerate(
              [(245, 58.0), (186, 44.0), (92, 22.0)][:n], 1)]
    st = {
        "t": time.time(), "seq": turn, "mode": "battle", "turn": turn,
        "me": {"name": "测试", "types": ["龙"], "hp_pct": hp,
               "hp_abs": int(490 * hp / 100), "hp_max": 490,
               "energy": 6, "energy_max": 10, "buffs": [], "trait": None},
        "foe": {"name": "对手", "types": ["水"], "hp_pct": 45.2,
                "hp_range": [349, 419], "def_range": [178, 231],
                "nature": None, "nature_conf": None, "known_skills": []},
        "skills": sk,
        "matchup": {"out": 3.0, "in": 0.5, "tag": "克制"},
        "verdict": None,
        "system": {"fps_ms": 4.2,
                   "detectors": {"bar": True, "skillbar": True,
                                 "countdown": True},
                   "conflicts": 0, "gate": None},
    }
    p = os.path.join(iso, "live_state.json")
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(p + ".tmp", p)


def worker():
    try:
        for _ in range(150):
            time.sleep(0.2)
            if win.native is not None:
                break
        time.sleep(4.0)

        def snap():
            return {
                "pct": win.evaluate_js(
                    "document.getElementById('mePct').textContent"),
                "turn": win.evaluate_js(
                    "document.getElementById('sysTurn').textContent"),
                "skills": win.evaluate_js(
                    "document.querySelectorAll('.skill').length"),
                "state": win.evaluate_js(
                    "document.getElementById('sysState').textContent"),
            }

        result["a_initial"] = snap()

        write_state(5, 60.0, 3)
        time.sleep(1.8)
        result["b_changed"] = snap()

        live = os.path.join(iso, "live_state.json")
        try:
            os.remove(live)
        except OSError:
            pass
        time.sleep(2.0)
        result["c_removed"] = snap()

        write_state(9, 30.0, 2)
        time.sleep(2.0)
        result["d_restored"] = snap()

        # ---- 空帧判定 + DOM 折叠（2026-11-21）
        #
        # 直接调 app.js 里的 render()，不依赖 /api/state ——
        # 这样测的是前端逻辑本身，不受独立感知耗时影响。
        result["e_empty"] = win.evaluate_js("""
        (function () {
          const mk = (o) => Object.assign({
            mode: 'battle',
            system: { detectors: { bar: true, skillbar: true,
                                   countdown: true } },
            me: {}, foe: {}, skills: [],
          }, o);

          const judge = {
            allEmpty: isEmptyFrame(mk({ standalone: true,
              system: { detectors: { bar: false, skillbar: false,
                                     countdown: false } } })),
            hpOnly: isEmptyFrame(mk({ me: { hp_pct: 78, name: null } })),
            nameOnly: isEmptyFrame(mk({ me: { hp_pct: null, name: 'A' } })),
            skillOnly: isEmptyFrame(mk({
              skills: [{ i: 1, dmg: 245, pct: 58 }] })),
            skillNa: isEmptyFrame(mk({
              skills: [{ i: 1, dmg: null, pct: null }] })),
            foeHp: isEmptyFrame(mk({ foe: { hp_pct: 45, name: null } })),
            // 越界血量是误读（实测有 163% 的假读数），不能当数据
            hpNoise: isEmptyFrame(mk({
              foe: { hp_pct: 163, name: null },
              system: { detectors: { bar: false, skillbar: false,
                                     countdown: false } },
            })),
            // 合理血量 + 没名字 + 没技能 -> 仍算空帧（只有血量不足以
            // 说明"在战斗"，可能只是主界面满血）
            hpOnlyNoName: isEmptyFrame(mk({
              foe: { hp_pct: 8.6, name: null },
              system: { detectors: { bar: true, skillbar: false,
                                     countdown: false } },
            })),
            // 服务端报错（遮挡）-> 一定是空帧，且文案要用它的原因
            errCovered: isEmptyFrame({
              error: '游戏窗口被其他窗口遮挡。请点一下游戏窗口把它切到前台',
              covered: true, standalone: true,
            }),
          };

          // 空帧渲染
          render({
            mode: 'battle', standalone: true, seq: 9001,
            system: { detectors: { bar: false, skillbar: false,
                                   countdown: false } },
            me: {}, foe: {}, skills: [],
          });
          const panel = document.getElementById('panel');
          const box = document.getElementById('noDataBox');
          const selfSec = document.querySelector('.side.self');
          judge.emptyFlag = panel.dataset.empty || '';
          judge.boxText = box ? box.textContent : '';
          judge.selfHidden = selfSec
            ? getComputedStyle(selfSec).display === 'none' : null;
          judge.stateText = document.getElementById('sysState').textContent;

          // 数据回来后恢复
          render({
            mode: 'battle', seq: 9002, turn: 5,
            system: { detectors: { bar: true, skillbar: true,
                                   countdown: true } },
            me: { name: '音速犬', hp_pct: 78, types: ['龙'],
                  hp_abs: 382, hp_max: 490, energy: 6, buffs: [] },
            foe: { name: '圆环鲸羽', hp_pct: 45, types: ['水'] },
            skills: [{ i: 1, dmg: 245, pct: 58, name: null,
                       element: null, cost: null, usable: true,
                       note: null }],
            matchup: { out: 3.0, in: 0.5, tag: '克制' },
          });
          judge.emptyFlagAfter = panel.dataset.empty || '';
          judge.selfShownAfter = selfSec
            ? getComputedStyle(selfSec).display !== 'none' : null;
          judge.nameAfter = document.getElementById('meName').textContent;
          judge.pctAfter = document.getElementById('mePct').textContent;

          // 最后测遮挡：服务端报错 -> 文案要说清楚，不能满屏破折号
          render({
            error: '游戏窗口被其他窗口遮挡。请点一下游戏窗口把它切到前台',
            covered: true, standalone: true,
          });
          judge.coveredFlag = panel.dataset.empty || '';
          judge.coveredBox = (document.getElementById('noDataBox') || {})
            .textContent || '';
          judge.coveredState =
            document.getElementById('sysState').textContent;

          // ---- 折叠状态一致性（2026-11-21）
          //
          // 背景：折叠态有两份存储（localStorage + overlay.json），
          // 原生侧 enter_embedded 恢复折叠态时**直接改 DOM**，
          // 没更新前端模块级 `folded` 变量 -> 用户点按钮"没反应"。
          //
          // 现在统一走 setFolded()，这里验证四个消费者一致：
          //   变量 folded / .overlay.folded / 按钮文案 / localStorage
          setFolded(true);
          const ov = document.getElementById('overlay');
          const btnF = document.getElementById('btnFold');
          judge.foldOn = {
            cls: ov.classList.contains('folded'),
            btn: btnF.textContent,
            ls: (() => { try {
              return localStorage.getItem('overlay.folded');
            } catch (e) { return null; } })(),
          };
          setFolded(false);
          judge.foldOff = {
            cls: ov.classList.contains('folded'),
            btn: btnF.textContent,
            ls: (() => { try {
              return localStorage.getItem('overlay.folded');
            } catch (e) { return null; } })(),
          };

          // ---- 抽屉状态行
          setFolded(true);
          const ds = document.getElementById('dstate');
          judge.dstateFold = {
            text: ds ? ds.textContent : '',
            folded: ds ? ds.dataset.folded : '',
          };
          setFolded(false);
          judge.dstateUnfold = {
            text: ds ? ds.textContent : '',
            folded: ds ? ds.dataset.folded : '',
          };

          // ---- 穿透后状态行要立刻同步（2026-11-21）
          //
          // 实测踩过：refreshStatus() 原来在 `await callNative` 之后调，
          // 原生侧有几十毫秒延迟，期间状态行还是旧值 ——
          // 用户看到"点了但状态没变"。
          //
          // 现在 paint() 之后立刻 refreshStatus()，同步读 aria-pressed。
          const bp = document.getElementById('btnPass');
          if (bp.getAttribute('aria-pressed') === 'true') bp.click();
          const passBefore = (ds || {}).dataset
            ? ds.dataset.pass : '';
          bp.click();
          judge.passSync = {
            aria: bp.getAttribute('aria-pressed'),
            text: ds ? ds.textContent : '',
            pass: ds ? ds.dataset.pass : '',
            before: passBefore,
          };

          // ★ skills 里是 null 时不能炸（2026-11-21 实机）
          //   服务端"还没识别到技能栏"会返回 [null,null,null,null]，
          //   原代码 s.dmg 直接 TypeError，整个 render() 中断，
          //   表现为技能区/结论区/替补区全不渲染。
          try {
            render({
              mode: 'idle', seq: 9003,
              system: { detectors: { bar: true, skillbar: false,
                                     countdown: false } },
              me: { name: '银月狼王', hp_pct: 87, types: [],
                    hp_abs: 102, hp_max: 317, energy: null, buffs: [] },
              foe: { name: '火羽', hp_pct: 80, types: [] },
              skills: [null, null, null, null],
              matchup: null, verdict: null,
            });
            judge.nullSkillsOk = true;
            judge.nullSkillsState =
              document.getElementById('sysState').textContent;
            judge.nullSkillsVerdict =
              document.getElementById('vOrder').textContent;
            judge.nullSkillsCards =
              document.querySelectorAll('.skill').length;
          } catch (err) {
            judge.nullSkillsOk = false;
            judge.nullSkillsErr = String(err);
          }
          return judge;
        })()
        """)
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


def test_resilience(port):
    print("\n[6] 韧性（数据源断开再恢复）")
    import tempfile

    iso = os.path.join(tempfile.gettempdir(), "roco_iso")
    os.makedirs(iso, exist_ok=True)
    # 清掉上次残留的数据，否则初始状态不是"有数据"
    for f in ("live_state.json", "live_state.json.tmp"):
        try:
            os.remove(os.path.join(iso, f))
        except OSError:
            pass

    # 起一个用独立日志目录的服务。
    # 端口要真的空着 —— 之前写 port+10 撞上了别的进程，
    # 连到旧服务导致断言全错（旧服务读的是机器人正在写的日志）。
    iso_port = None
    for p in range(port + 20, port + 60):
        if port_free(p):
            iso_port = p
            break
    if iso_port is None:
        ok(False, "找不到空闲端口")
        return

    env = dict(os.environ)
    env["ROCO_LOGS"] = iso
    # 关掉独立感知：它会读真实屏幕，把测试的假数据源盖掉
    # （实测 /api/state 超时、状态显示"战斗中"而不是测试期望的值）
    env["ROCO_NO_STANDALONE"] = "1"
    srv = subprocess.Popen(
        [sys.executable, os.path.join(SRC, "ui_server.py"),
         "--port", str(iso_port)],
        cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=0x08000000 | 0x00000008)
    if not wait_health(iso_port, timeout=12):
        ok(False, "独立服务启动（端口 %d）" % iso_port)
        try:
            srv.terminate()
        except Exception:
            pass
        return

    # 确认它真的读的是独立目录
    import json as _json
    probe = {
        "t": time.time(), "seq": 1, "mode": "battle", "turn": 1,
        "me": {"name": "探针", "types": ["龙"], "hp_pct": 100.0,
               "hp_abs": 490, "hp_max": 490, "energy": 6,
               "energy_max": 10, "buffs": [], "trait": None},
        "foe": {"name": "对手", "types": ["水"], "hp_pct": 45.2,
                "hp_range": [349, 419], "def_range": [178, 231],
                "nature": None, "nature_conf": None, "known_skills": []},
        "skills": [], "matchup": None, "verdict": None,
        "system": {"fps_ms": 4.2,
                   "detectors": {"bar": True, "skillbar": True,
                                 "countdown": True},
                   "conflicts": 0, "gate": None},
    }
    pth = os.path.join(iso, "live_state.json")
    with open(pth + ".tmp", "w", encoding="utf-8") as f:
        _json.dump(probe, f, ensure_ascii=False)
    os.replace(pth + ".tmp", pth)
    time.sleep(0.3)
    try:
        got = get(iso_port, "/api/state")
        if not got or got.get("me", {}).get("name") != "探针":
            ok(False, "独立服务读到的是别的数据（端口被旧服务占用）",
               "me=%s" % (got.get("me", {}).get("name") if got else None))
            srv.terminate()
            return
    except Exception as e:
        ok(False, "独立服务状态检查", str(e)[:60])
        srv.terminate()
        return
    ok(True, "独立服务隔离正确", "端口 %d" % iso_port)

    tmp = os.path.join(tempfile.gettempdir(), "roco_resilience.json")
    try:
        os.remove(tmp)
    except OSError:
        pass
    script = os.path.join(tempfile.gettempdir(), "roco_res_worker.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(RESILIENCE_WORKER)

    wp = subprocess.Popen(
        [sys.executable, script, str(iso_port), iso, tmp],
        cwd=ROOT, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=0x08000000)

    end = time.time() + 45
    while time.time() < end:
        if os.path.exists(tmp):
            break
        time.sleep(0.4)

    for proc in (wp, srv):
        try:
            proc.terminate()
        except Exception:
            pass

    if not os.path.exists(tmp):
        ok(False, "韧性测试超时")
        return

    try:
        with open(tmp, encoding="utf-8") as f:
            r = _json.load(f)
    except Exception as e:
        ok(False, "韧性结果解析", str(e)[:60])
        return

    if "error" in r:
        ok(False, "韧性测试", r["error"][:100])
        return

    a = r.get("a_initial", {})
    b = r.get("b_changed", {})
    c = r.get("c_removed", {})
    d = r.get("d_restored", {})

    ok(b.get("pct") == "60", "数据变化后跟上", "pct=%s" % b.get("pct"))
    ok(b.get("turn") == "T5", "回合号跟上", "turn=%s" % b.get("turn"))
    ok(c.get("pct") == "—", "数据源消失后清空", "pct=%s" % c.get("pct"))
    # 数据源消失时，前端应该明确显示"没数据"。
    #
    # 两种可能的文案：
    #   "机器人未启动"  —— renderEmpty() 分支（/api/state 返回 null）
    #   "状态未知"      —— render() 分支（有 state 但 mode 字段缺失）
    # 两者都表示"现在没有可用的战斗数据"，不该是"未连接"那种误导性措辞。
    ok(c.get("state") in ("机器人未启动", "状态未知"),
       "显示无数据（不是「未连接」）", "state=%s" % c.get("state"))
    ok(d.get("pct") == "30", "数据源恢复后跟上", "pct=%s" % d.get("pct"))
    ok(int(d.get("skills") or 0) == 2, "技能数正确", "skills=%s" % d.get("skills"))


    # ---- 空帧判定（2026-11-21）
    #
    # 独立模式下服务在跑、屏幕能抓，但游戏黑屏/加载/不在战斗时，
    # 每个字段各自兜底成 '—'，面板会变成 12 个破折号 + 4 个「未知」。
    # 用户看到的第一反应是"这东西坏了"，而不是"现在不在战斗"。
    #
    # 判定要**保守**：只有血条没有名字是很常见的状态（刚进战斗还没
    # 识别出身份），那种情况必须照常渲染，否则用户会以为血条也坏了。
    e = r.get("e_empty") or {}
    if not isinstance(e, dict) or not e:
        ok(False, "空帧测试有结果", repr(e)[:70])
        return

    ok(e.get("allEmpty") is True, "全空 -> 判为空帧")
    ok(e.get("hpOnly") is False,
       "只有血条 -> 不算空帧（血条是真数据）")
    ok(e.get("nameOnly") is False, "只有名字 -> 不算空帧")
    ok(e.get("skillOnly") is False, "有技能伤害 -> 不算空帧")
    ok(e.get("skillNa") is True, "技能全是占位 -> 算空帧")
    ok(e.get("foeHp") is False, "对面有血条 -> 不算空帧")
    ok(e.get("hpNoise") is True,
       "血量 163（越界）-> 算空帧（误读）")
    ok(e.get("hpOnlyNoName") is False,
       "血量 8.6 + bar=true -> 不算空帧（有读数就显示）")
    ok(e.get("errCovered") is True,
       "服务端 error -> 算空帧（不然满屏未知）")

    ok(e.get("emptyFlag") == "1", "空帧时 panel 打上 data-empty",
       str(e.get("emptyFlag")))
    box = e.get("boxText") or ""
    ok("没有对战画面" in box or "等待画面" in box,
       "空态文案说清原因", box[:40])
    ok(e.get("selfHidden") is True, "我方区块被折叠",
       str(e.get("selfHidden")))

    ok(e.get("coveredFlag") == "1", "遮挡时 panel 打上 data-empty",
       str(e.get("coveredFlag")))
    cbox = e.get("coveredBox") or ""
    ok("遮挡" in cbox, "遮挡文案说清原因", cbox[:40])
    ok(e.get("coveredState") == "读取失败", "状态栏显示「读取失败」",
       str(e.get("coveredState")))

    # ---- 折叠状态一致性（2026-11-21）
    fo = e.get("foldOn") or {}
    ff = e.get("foldOff") or {}
    ok(fo.get("cls") is True, "setFolded(true) -> overlay.folded",
       str(fo.get("cls")))
    ok(fo.get("btn") == "展开", "setFolded(true) -> 按钮写「展开」",
       str(fo.get("btn")))
    ok(fo.get("ls") == "1", "setFolded(true) -> localStorage=1",
       str(fo.get("ls")))
    ok(ff.get("cls") is False, "setFolded(false) -> 去掉 folded",
       str(ff.get("cls")))
    ok(ff.get("btn") == "折叠", "setFolded(false) -> 按钮写「折叠」",
       str(ff.get("btn")))
    ok(ff.get("ls") == "0", "setFolded(false) -> localStorage=0",
       str(ff.get("ls")))

    # ---- 抽屉状态行
    df = e.get("dstateFold") or {}
    du = e.get("dstateUnfold") or {}
    ok("折叠中" in (df.get("text") or ""), "折叠时状态行说「折叠中」",
       (df.get("text") or "")[:30])
    ok(df.get("folded") == "1", "状态行 data-folded=1",
       str(df.get("folded")))
    ok("展开" in (du.get("text") or ""), "展开时状态行说「展开」",
       (du.get("text") or "")[:30])
    ok(du.get("folded") == "0", "状态行 data-folded=0",
       str(du.get("folded")))

    # ---- 穿透后状态行立刻同步（不等原生异步返回）
    ps = e.get("passSync") or {}
    ok(ps.get("aria") == "true", "点击后 aria-pressed=true",
       str(ps.get("aria")))
    ok(ps.get("pass") == "1", "状态行 data-pass=1（立刻，不等原生）",
       str(ps.get("pass")))
    ok("穿透开启" in (ps.get("text") or ""),
       "状态行文案立刻说「穿透开启」", (ps.get("text") or "")[:30])
    ok(e.get("stateText") not in ("状态未知",),
       "状态栏不是「状态未知」", str(e.get("stateText")))

    ok(e.get("emptyFlagAfter") == "", "数据回来后 data-empty 被移除",
       str(e.get("emptyFlagAfter")))
    ok(e.get("selfShownAfter") is True, "我方区块恢复显示",
       str(e.get("selfShownAfter")))
    ok(e.get("nameAfter") == "音速犬", "名字恢复", str(e.get("nameAfter")))
    ok(e.get("pctAfter") == "78", "血量恢复", str(e.get("pctAfter")))

    # skills = [null,null,null,null] 不能让 render 中断
    ok(e.get("nullSkillsOk") is True, "null 技能槽不抛异常",
       str(e.get("nullSkillsErr"))[:60])
    ok(e.get("nullSkillsState") not in ("渲染出错",),
       "null 技能槽后状态栏正常", str(e.get("nullSkillsState")))
    ok(int(e.get("nullSkillsCards") or 0) == 4,
       "null 技能槽渲染成 4 张占位卡", "cards=%s" % e.get("nullSkillsCards"))
    ok(e.get("nullSkillsVerdict") == "数据不足",
       "结论区正常渲染", str(e.get("nullSkillsVerdict")))


# ============================================================ 3b4. fit 状态同步
def test_fit_state_sync():
    """pin size 之后 state["css_h"] 必须同步成新高度。

    背景（2026-11-21 21:20:50 实机）
    -------------------------------
    日志：
        pin size (360x620 css) -> rect=720x1240
        fit_window(force=False) in, rect=720x1240
        fit: py=132 js={"e":"1","vh":620}
        （没有后续 SetWindowPos，窗口停在 1240 = 620 CSS）

    原因：pin size 用 SetWindowPos 把窗口设成 620，但没更新
    `state["css_h"]`（还是之前测出的 134）。随后 fit_window 拿
    `css_h=134` 和刚测的 132 比较，差 2 <= 4 判定"不用改"直接 return。

    表现：空态面板只有 132 高，窗口却是 620，下面一大片空白。

    这里锁住"源码里 pin size 之后确实写了 state"，属于静态检查 ——
    真正跑 SetWindowPos 需要完整窗口环境，成本太高。
    """
    print("\n[3b4] fit 状态同步（静态检查）")
    try:
        with open(os.path.join(SRC, "overlay.py"), encoding="utf-8") as f:
            src = f.read()
    except OSError as e:
        ok(False, "读 overlay.py", str(e)[:60])
        return

    # pin size 那段日志之后必须紧跟 state 赋值
    idx = src.find('pin size (%dx%d css, mul=%.2f) -> rect=%s')
    if idx < 0:
        ok(False, "找到 pin size 日志语句")
        return
    ok(True, "找到 pin size 日志语句")

    tail = src[idx:idx + 1200]
    ok('state["css_h"] = args.height' in tail,
       "pin size 后同步 state['css_h']",
       "" if 'state["css_h"] = args.height' in tail else "缺失")
    ok('state["css_w"] = args.width' in tail,
       "pin size 后同步 state['css_w']",
       "" if 'state["css_w"] = args.width' in tail else "缺失")


# ============================================================ 3c. 对比度
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
def test_overlay_contrast():
    """悬浮窗 `style.css` 的对比度回归。

    为什么单独测（2026-11-21）
    -------------------------
    上面的 `test_contrast` 只测 `console.css`（控制台），**从没测过
    悬浮窗的 `style.css`** —— 而悬浮窗才是用户一直盯着看的那个。

    实测发现两个主题的 `--ink-3` 在半透明下都不达 WCAG AA：

        深色  #7A776F  最坏 2.80（需 4.5）
        浅色  #8E8A82  最坏 2.48（需 4.5）

    它承担的正是 `.card-sub` / 空态提示这些**解释性文字** ——
    用户最需要看清"为什么没数据"的地方，恰恰最看不清。

    为什么门槛按"最坏情况"算
    ------------------------
    悬浮窗半透明浮在游戏画面上。整窗 alpha（`SetLayeredWindowAttributes`）
    会让**文字和背景一起**变淡，所以纯色背景的对比度不够，必须按
    "叠在典型游戏底色上"算。这里取三种：亮石砖 / 暗阴影 / 中性灰。
    """
    print("\n[3c2] 悬浮窗对比度（WCAG AA，含半透明最坏情况）")
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

    def over(fg, d, a):
        return tuple(round(a * f + (1 - a) * x) for f, x in zip(fg, d))

    try:
        with open(os.path.join(UI, "style.css"), encoding="utf-8") as f:
            css = f.read()
    except OSError as e:
        ok(False, "读 style.css", str(e)[:60])
        return

    # 三种典型游戏底色 + 用户常用的窗口 alpha 200/255
    DESKTOPS = [(216, 212, 204), (42, 42, 46), (128, 128, 128)]
    ALPHA = 200 / 255.0

    def tokens_of(block_start):
        i = css.find(block_start)
        if i < 0:
            return {}
        j = css.find("}", i)
        out = {}
        for m in re.finditer(r"--([\w-]+):\s*#([0-9A-Fa-f]{6})",
                             css[i:j]):
            h = m.group(2)
            out[m.group(1)] = tuple(int(h[k:k + 2], 16) for k in (0, 2, 4))
        return out

    light = tokens_of(":root {")
    dark = tokens_of("body.theme-dark {")
    ok(bool(light) and bool(dark), "解析到两个主题的 token",
       "light=%d dark=%d" % (len(light), len(dark)))

    def worst_ratio(fg, bg):
        w = ratio(fg, bg)
        for d in DESKTOPS:
            w = min(w, ratio(over(fg, d, ALPHA), over(bg, d, ALPHA)))
        return w

    # (token, 门槛)。--kill 是图形用途（血条填充），门槛 3.0；
    # --kill-text 是文字用途，门槛 4.5。
    CHECKS = (
        ("ink-0", 4.5), ("ink-2", 4.5), ("ink-3", 4.5),
        ("accent", 4.5), ("win", 4.5), ("warn", 4.5),
        ("kill", 3.0), ("kill-text", 4.5),
    )

    for theme_name, toks, bgkey in (
            ("深色", dark, (31, 30, 29)),
            ("浅色", light, (250, 249, 245))):
        bg = toks.get("bg") or bgkey
        for name, need in CHECKS:
            if name not in toks:
                ok(False, "%s --%s 存在" % (theme_name, name))
                continue
            w = worst_ratio(toks[name], bg)
            ok(w >= need, "%s --%s >= %.1f" % (theme_name, name, need),
               "最坏 %.2f" % w)

    # 语义检查：--kill 必须只用于 background（图形），不能用于 color（文字）
    #
    # 这是 2026-11-21 的教训：同一个值无法同时满足图形 3.0 和文字 4.5，
    # 所以拆成两个 token。如果以后有人把 color 改回 var(--kill)，
    # 文字对比度会静默跌回 2.70。
    for m in re.finditer(r"([^\n{}]+)\{([^}]*)\}", css):
        sel, body = m.group(1), m.group(2)
        if "var(--kill)" not in body:
            continue
        ok("background" in body and "color:" not in body,
           "--kill 只用于 background（%s）" % sel.strip()[:28],
           body.strip()[:50])


# ============================================================ 3b. 日志尾推断
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
    ap.add_argument("--no-dom", action="store_true",
                    help="跳过需要渲染的 DOM 测试")
    args = ap.parse_args()

    print("=" * 66)
    print("前端自检")
    print("=" * 66)

    port = 8818
    if not port_free(port):
        print("端口 %d 被占用，换一个" % port)
        for p in range(8820, 8840):
            if port_free(p):
                port = p
                break

    srv = subprocess.Popen(
        [sys.executable, os.path.join(SRC, "ui_server.py"), "--port", str(port)],
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
        test_fit_state_sync()
        test_contrast()
        test_overlay_contrast()
        test_phase_infer()
        test_label(port)
        test_window_state()
        if not args.no_dom:
            test_dom(port)
            test_resilience(port)
    finally:
        try:
            srv.terminate()
        except Exception:
            pass

    print()
    print("=" * 66)
    if FAILS:
        print("%d 项失败 / %d 项通过" % (len(FAILS), PASSES))
        for f in FAILS:
            print("  - %s" % f)
        return 1
    print("全部通过（%d 项）" % PASSES)
    return 0


if __name__ == "__main__":
    sys.exit(main())
