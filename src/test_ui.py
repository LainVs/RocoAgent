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

# 共享层（2026-11-21，见 docs/PLAN_TEST_SPLIT.md）
#
# 三个会话都在改这个文件，而 ROOT/SRC/UI + ok/port_free/wait_health/
# get/get_raw 拆分成 test_ui.py + test_console.py 后两边都要用。
# 抽到 test_ui_common.py 保证只有一个来源。
#
# ★ 不要 `from test_ui_common import PASSES`：
#   int 不可变，导入拿到的是**快照**。`ok()` 内部 `global PASSES` 改的是
#   test_ui_common 里的那个，本地永远是 0。要读计数用 `summary()`。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_ui_common as C  # noqa: E402,F401
from test_ui_common import (  # noqa: E402
    ROOT, SRC, UI,
    get, get_raw, ok, port_free, summary, wait_health,
)


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

        # ★ 轮询等待代替固定 sleep（2026-11-21 修 flaky）
        #
        # 原来每步 `time.sleep(1.8)` / `2.0` 然后立刻 snap()。
        # 前端轮询 400ms，但渲染 + fit_window + IPC 加起来有时超过 2 秒，
        # 固定 sleep 撞上就失败，而且失败时带着旧状态往下跑，后续断言全串。
        #
        # 实测失败样例（约 1/10 概率）：
        #     数据源恢复后跟上   pct=100   <- 期望 30
        #     技能数正确        skills=0  <- 期望 2
        def wait_until(cond, timeout=8.0, step=0.25):
            end = time.time() + timeout
            last = None
            while time.time() < end:
                last = snap()
                try:
                    if cond(last):
                        return last
                except Exception:
                    pass
                time.sleep(step)
            return last

        result["a_initial"] = snap()

        write_state(5, 60.0, 3)
        result["b_changed"] = wait_until(
            lambda s: s.get("pct") == "60" and s.get("turn") == "T5")

        live = os.path.join(iso, "live_state.json")
        try:
            os.remove(live)
        except OSError:
            pass
        result["c_removed"] = wait_until(
            lambda s: s.get("pct") == "—")

        write_state(9, 30.0, 2)
        result["d_restored"] = wait_until(
            lambda s: (s.get("pct") == "30"
                       and int(s.get("skills") or 0) == 2))

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

          // ---- Alt+A 透明度档位（2026-11-21）
          //
          // 实测 bug：ALPHAS 原来是 [100,78,55,35,20]（百分比），
          // 但 slider.min = ALPHA_MIN = 180。浏览器把 value 夹到 180，
          // 于是"想降到 78% 实际停在 71%"，再按也回不来 —— 热键完全失效。
          //
          // 现在 ALPHA_STEPS 用 0~255 口径，全部 >= ALPHA_MIN。
          (function () {
            const s = document.getElementById('alpha');
            s.value = 255;
            s.dispatchEvent(new Event('input'));
            const seen = [];
            for (let i = 0; i < 5; i++) {
              document.dispatchEvent(new KeyboardEvent('keydown', {
                key: 'a', altKey: true, bubbles: true,
              }));
              seen.push(+s.value);
            }
            judge.alphaSteps = seen;
            // 注意：不能写 `ALPHA_MIN` —— 它是 app.js 里的模块级 const，
            // evaluate_js 的独立作用域访问不到（会 ReferenceError 或 undefined）。
            // slider.min 是 DOM 属性，一定能读到，值就是 ALPHA_MIN。
            judge.alphaMin = +document.getElementById('alpha').min;

            let minSeen = 999;
            for (let i = 0; i < 8; i++) {
              document.dispatchEvent(new KeyboardEvent('keydown', {
                key: 'a', altKey: true, bubbles: true,
              }));
              minSeen = Math.min(minSeen, +s.value);
            }
            judge.alphaMinSeen = minSeen;

            let stuck = 0;
            for (let i = 1; i < seen.length; i++) {
              if (seen[i] === seen[i - 1]) stuck += 1;
            }
            judge.alphaStuck = stuck;

            s.value = 230;
            s.dispatchEvent(new Event('input'));
          })();

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

    # ---- Alt+A 透明度档位（2026-11-21）
    #
    # 实测 bug：档位写百分比 [100,78,55,35,20]，但 slider.min=180，
    # 浏览器夹住 -> 按 Alt+A 永远停在 71%，热键完全失效。
    steps = e.get("alphaSteps") or []
    amin = e.get("alphaMin")
    ok(len(steps) == 5, "Alt+A 连按 5 次都有值", str(steps))
    ok(amin is not None, "读到 slider.min（= ALPHA_MIN）", str(amin))
    if steps and amin is not None:
        ok(all(v >= amin for v in steps),
           "所有档位 >= ALPHA_MIN",
           "min(%s) vs %s" % (min(steps), amin))
        ok(e.get("alphaStuck") == 0,
           "每次按键都有变化（不卡住）",
           "stuck=%s" % e.get("alphaStuck"))
        ok(e.get("alphaMinSeen", 0) >= amin,
           "连按 8 次最低值不低于下限",
           "minSeen=%s vs %s" % (e.get("alphaMinSeen"), amin))
    else:
        ok(False, "Alt+A 档位数据完整",
           "steps=%s amin=%s" % (steps, amin))

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
        # 注意：--accent 现在是 `var(--accent-strong)`，不是 hex，
        # 由下面的「accent 三态 × 两主题」单独校验六个具体 token。
        ("win", 4.5), ("warn", 4.5),
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

    # ---- accent 三态 × 两主题（2026-11-21）
    #
    # 背景：`setAccent()` 原来把色值**硬编码在 JS 里**，通过内联样式
    # `root.style.setProperty('--accent', '#FF6B4A')` 覆盖。
    # 内联样式优先级高于任何 CSS 规则，所以 style.css 里的 token 永远不生效。
    #
    # 实测：两个主题的 --accent 都是 #FF6B4A，而它在浅色底上只有 2.23:1；
    # `even` 的 #7FD3E8 更是 1.47 —— 而"互不克制"是最常见的对局状态。
    #
    # 现在色值回到 CSS（两主题 × 三态 = 六个 token），JS 只切 data-accent。
    for theme_name, toks, bgkey in (
            ("深色", dark, (31, 30, 29)),
            ("浅色", light, (250, 249, 245))):
        bg = toks.get("bg") or bgkey
        for state in ("strong", "even", "weak"):
            key = "accent-" + state
            if key not in toks:
                ok(False, "%s --%s 存在" % (theme_name, key))
                continue
            w = worst_ratio(toks[key], bg)
            ok(w >= 4.5, "%s --%s >= 4.5" % (theme_name, key),
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

        # 悬浮窗侧（2026-11-21 拆分）
        #
        # 控制台那 5 组已搬到 src/test_console.py（80 项）：
        #     test_api / test_static / test_contract
        #     test_contrast / test_phase_infer
        # 那边单独跑：python src\test_console.py
        #
        # 拆分验证：test_ui.py --no-dom 49 项 + test_console.py 80 项
        #           = 129 == 拆分前的 --no-dom 项数
        test_fit_state_sync()
        test_overlay_contrast()
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

    return summary()


if __name__ == "__main__":
    sys.exit(main())
