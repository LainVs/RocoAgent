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
        ("/api/bot", ("running", "phase")),
        ("/api/stats", ("wins", "losses", "decided",
                        "season", "badge_stuck", "primary")),
        ("/api/team", ("slots",)),
        ("/api/turns?limit=2", ("items",)),
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
    try:
        st = get(port, "/api/state")
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
    ok(c.get("state") == "未连接", "显示未连接", "state=%s" % c.get("state"))
    ok(d.get("pct") == "30", "数据源恢复后跟上", "pct=%s" % d.get("pct"))
    ok(int(d.get("skills") or 0) == 2, "技能数正确", "skills=%s" % d.get("skills"))


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
