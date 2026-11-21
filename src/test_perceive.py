# -*- coding: utf-8 -*-
"""感知层重构的等价性测试。

背景
----
把 Perc.observe() 从「5 次 grab_s」改成「1 次 grab_frame + 5 次切片」，
又把技能表查询改成按 my_name 缓存。这类改动最怕**悄悄改变语义**：
血条偏 2px、技能栏错位、槽位少一个，日志上都只是数字变了一点点，
不会报错，但会直接影响出招。

做法
----
把改动前的 perceive.py 从 git 里取出来存成临时模块，用**同一块屏幕**
分别跑旧/新 observe()，逐字段比对。合成就够了，不需要重放录像。

    python src/test_perceive.py            # 实时屏幕 A/B（需要游戏在跑）
    python src/test_perceive.py --no-live  # 只跑合成帧（不需要游戏）
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, r"D:\Tools")

import config            # noqa: E402
import digitread as dr   # noqa: E402
import winio             # noqa: E402
import perceive as NEW   # noqa: E402

OLD_REF = "ui-m38"        # 重构前的最后一个 tag，perceive.py 的基线

lines = []


def P(*a):
    s = " ".join(str(x) for x in a)
    lines.append(s)
    print(s)


def load_old_perceive():
    """从 git 取出基线版 perceive.py，作为独立模块加载。"""
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "perceive_old.py")
    src = subprocess.check_output(
        ["git", "show", "%s:src/perceive.py" % OLD_REF],
        cwd=ROOT)
    with open(path, "wb") as f:
        f.write(src)
    spec = importlib.util.spec_from_file_location("perceive_old", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["perceive_old"] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeScreen:
    """用一整帧画面冒充 Screen，验证切片坐标是否和 grab_s 一致。

    默认就是本机的真实参数（2560x1600 客户区、边框偏移 13/58、缩放 1.0），
    也可以从真机 Screen 抄一份（见 capture_live）。
    """

    BASE_CLIENT_W, BASE_CLIENT_H = 2560, 1600
    BASE_OFF_X, BASE_OFF_Y = 13, 58

    def __init__(self, frame_bgra, client_off=(13, 58),
                 client_size=(2560, 1600), rect=(0, 0, 2586, 1671),
                 scale=(1.0, 1.0)):
        self.frame = frame_bgra
        self.client_off = client_off
        self.client_size = client_size
        self.rect = rect
        self._scale = scale

    @classmethod
    def capture_live(cls, scr):
        """抓一帧真机画面冻住，让新旧两版跑在**同一帧**上。

        直接对着屏幕各跑一次是不严谨的：两次抓屏之间游戏在动，
        npills 会变，看起来像重构改坏了。
        """
        frame, scale = scr.grab_frame()
        if frame is None:
            return None
        return cls(frame, client_off=getattr(scr, "client_off", (13, 58)),
                   client_size=getattr(scr, "client_size", (2560, 1600)),
                   rect=scr.rect or (0, 0, 2586, 1671), scale=scale)

    def scale(self):
        return self._scale

    def _cut(self, px, py, pw, ph):
        return self.frame[py:py + ph, px:px + pw]

    def grab_s(self, box):
        # 复刻 winio.grab_s：sbox() 减 BASE_OFF 换算成客户区像素，
        # _grab_at_client() 再加 client_off 落到窗口坐标，_fit() 缩回基准尺寸。
        sx, sy = self._scale
        ox, oy = self.client_off
        x, y, w, h = box
        px = ox + int(round((x - self.BASE_OFF_X) * sx))
        py = oy + int(round((y - self.BASE_OFF_Y) * sy))
        pw = max(1, int(round(w * sx)))
        ph = max(1, int(round(h * sy)))
        sub = self._cut(px, py, pw, ph)
        rgb = cv2.cvtColor(sub, cv2.COLOR_BGRA2RGB)
        if abs(sx - 1.0) >= 0.004 or abs(sy - 1.0) >= 0.004:
            rgb = cv2.resize(rgb, (max(1, int(w)), max(1, int(h))),
                             interpolation=cv2.INTER_AREA)
        return rgb

    def grab_frame(self):
        return self.frame, self._scale

    def crop_frame(self, frame, box, scale=None, client=False):
        sx, sy = scale or self._scale
        ox, oy = self.client_off
        x, y, w, h = box
        if client:
            px, py = ox + int(round(x * sx)), oy + int(round(y * sy))
        else:
            px = ox + int(round((x - self.BASE_OFF_X) * sx))
            py = oy + int(round((y - self.BASE_OFF_Y) * sy))
        pw = max(1, int(round(w * sx)))
        ph = max(1, int(round(h * sy)))
        if py < 0 or px < 0 or py + ph > frame.shape[0] \
                or px + pw > frame.shape[1]:
            return None
        sub = frame[py:py + ph, px:px + pw]
        rgb = cv2.cvtColor(sub, cv2.COLOR_BGRA2RGB)
        if abs(sx - 1.0) >= 0.004 or abs(sy - 1.0) >= 0.004:
            rgb = cv2.resize(rgb, (int(w), int(h)),
                             interpolation=cv2.INTER_AREA)
        return rgb


class NoFrameScreen:
    """只有 grab_s（老接口）的 Screen：模拟外部注入的测试替身。

    Perc.observe() 必须自动退回逐区域 grab_s，输出要和整帧切片**完全一致**。
    """

    def __init__(self, inner):
        self.inner = inner

    def grab_s(self, box):
        return self.inner.grab_s(box)


def synthetic_frame():
    """造一整帧（窗口尺寸）：血条 + 技能栏 + 倒计时空白。

    帧是**整窗**尺寸（2586x1671，含 13px 边框 + 58px 标题栏），
    因为 grab_frame() 抓的是整窗，和 grab_s 的窗口坐标口径一致。
    """
    f = np.zeros((1671, 2586, 4), np.uint8)
    f[:, :, 3] = 255

    # MY_BAR: 绿色满条。_read_bar 看的是填充比例，画满即可
    mx, my, mw, mh = 166, 170, 244, 20
    f[my:my + mh, mx:mx + mw] = (60, 210, 80, 255)

    # FOE_BAR: 左半绿右半灰底板 -> 约 50%，且 ui=True
    fx, fy, fw, fh = 2251, 170, 245, 20
    f[fy:fy + fh, fx:fx + fw] = (95, 95, 95, 255)
    f[fy:fy + fh, fx:fx + fw // 2] = (60, 210, 80, 255)

    # SKILL_BOX: 贴真实抓到的技能栏（171/114/45/102）
    bar = cv2.imread(os.path.join(config.LOG_DIR, "bar_partial_093057.png"))
    if bar is not None:
        x0, y0, x1, y1 = config.SKILL_BOX
        h = min(bar.shape[0], f.shape[0] - y0)
        w = min(bar.shape[1], f.shape[1] - x0)
        f[y0:y0 + h, x0:x0 + w, :3] = bar[:h, :w]
    return f


def cmp_state(tag, a, b, keys):
    """逐字段比对。

    `recovered=True` 的槽位是**故意**和旧版不一样的（7 兜底），
    单独由 expect_recovered() 校验，这里只核对它确实只是把 '?' 换成了 '7'。
    """
    bad = []
    for k in keys:
        if a.get(k) != b.get(k):
            bad.append("%s old=%r new=%r" % (k, a.get(k), b.get(k)))
    sa, sb = a.get("skills") or [], b.get("skills") or []
    if len(sa) != len(sb):
        bad.append("skills len %d != %d" % (len(sa), len(sb)))
    else:
        for i, (x, y) in enumerate(zip(sa, sb)):
            recovered = bool(y and y.get("recovered"))
            rejected = bool(y and y.get("rejected"))
            check = ("kind", "color", "plate", "lit", "skill_name", "skill_kind")
            if not recovered and not rejected:
                check = check + ("text", "value", "score")
            for kk in check:
                xv = None if x is None else x.get(kk)
                yv = None if y is None else y.get(kk)
                if xv != yv:
                    bad.append("skill%d.%s old=%r new=%r" % (i + 1, kk, xv, yv))
            if recovered:
                xt = (x or {}).get("text") or ""
                yt = (y or {}).get("text") or ""
                if yt != xt.replace("?", "7"):
                    bad.append("skill%d 兜底结果不对 old=%r new=%r"
                               % (i + 1, xt, yt))
            if rejected:
                # 位数闸门：旧版会给出一个 4 位以上的"数字"（切分错误），
                # 新版应当降级成读不出。
                xt = (x or {}).get("text") or ""
                if len(xt) <= 3:
                    bad.append("skill%d 不该被位数闸门拦下 old=%r" % (i + 1, xt))
                if (y or {}).get("text") != "?":
                    bad.append("skill%d 被拦后 text 应为 '?' 实际 %r"
                               % (i + 1, (y or {}).get("text")))
    if bad:
        P("  [FAIL] %s" % tag)
        for x in bad[:12]:
            P("         " + x)
        return False
    P("  [OK]   %s" % tag)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-live", action="store_true")
    args = ap.parse_args()

    old = load_old_perceive()
    keys = ("battle", "my_ui", "foe_ui", "my_pct", "my_color",
            "foe_pct", "foe_color", "bar", "npills", "countdown",
            "energy", "myturn")

    ok_all = True

    # ---- 1. 合成帧：技能栏 + 血条都在（等价于"轮到我方"）----
    P("== 合成帧（血条满 + 技能栏 171/114/45/102）==")
    fs = FakeScreen(synthetic_frame())
    # 覆盖三条技能表分支：
    #   音速犬   team_db 里配了 moves，且槽位 1 的 power=0（应强制成 dash）
    #   银月狼王 team_db 里配了 moves，四个都有伤害（不该动）
    #   虚无缥缈 不在 team_db，走"变化技能耗能集合"推断
    #   None      连名字都没有，必须 no-op
    for name in ("音速犬", "银月狼王", "虚无缥缈", None):
        a = old.Perc(fs)
        b = NEW.Perc(fs)
        a.my_name = b.my_name = name
        st_old = a.observe()
        st_new = b.observe()
        ok_all &= cmp_state("合成帧 my_name=%s" % name, st_old, st_new, keys)
        P("     读到: %s"
          % [(s or {}).get("text") for s in st_new["skills"]])
    # 明确验证 dash 纠正真的生效（音速犬 槽位 1 power=0）
    b = NEW.Perc(fs)
    b.my_name = "音速犬"
    st = b.observe()
    ok_all &= (st["skills"][0] or {}).get("kind") == "dash"
    P("  音速犬 槽位1 被判 dash：%s"
      % ((st["skills"][0] or {}).get("kind")))
    P("     bar=%s npills=%s battle=%s"
      % (st["bar"], st["npills"], st["battle"]))

    # ---- 1b. 老接口 Screen（没有 grab_frame）：必须自动回退且结果一致 ----
    P("")
    P("== 回退路径（Screen 只有 grab_s）==")
    a = old.Perc(fs)
    b = NEW.Perc(NoFrameScreen(fs))
    a.my_name = b.my_name = "音速犬"
    ok_all &= cmp_state("回退路径", a.observe(), b.observe(), keys)

    # ---- 1c. 数字兜底：细斜杠的 7 必须补回来，别的数字不许动 ----
    P("")
    P("== 7 兜底（真实失败帧，人工核对过真值）==")
    tpl = dr.load_templates()
    cases = (
        ("bar_partial_012307.png", ["427", "133", "142", "114"]),
        ("bar_partial_093057.png", ["171", "114", "45", "102"]),
    )
    for fname, want in cases:
        path = os.path.join(config.LOG_DIR, fname)
        if not os.path.exists(path):
            P("  跳过 %s（截图不在）" % fname)
            continue
        img = cv2.imread(path)
        rgb = img[:, :, ::-1]
        h, w = rgb.shape[:2]
        pills = dr.find_pills(rgb, (0, 0, w, h))
        slots = NEW._slots(rgb, (0, 0, w, h), pills)
        got = []
        plain = []
        for s in slots:
            if not s:
                got.append(None)
                plain.append(None)
                continue
            got.append(NEW.read_skill_with_fallback(rgb, s, tpl)["text"])
            plain.append(dr.read_skill(rgb, s, tpl)["text"])
        good = got == want
        ok_all &= good
        P("  %s %-26s 兜底前 %s -> 兜底后 %s"
          % ("[OK]  " if good else "[FAIL]", fname, plain, got))

    # ---- 3. 110 帧旧语料：兜底不能破坏原本 100% 的读数 ----
    P("")
    P("== 110 帧旧语料回归（logs/digits/frames，被 gitignore，可能不在）==")
    corpus = os.path.join(config.LOG_DIR, "digits", "frames")
    if not os.path.isdir(corpus):
        P("  跳过（语料不在本机）")
    else:
        import glob
        frames = sorted(glob.glob(os.path.join(corpus, "*.png")))
        q = 0
        seen = 0
        glyphs = 0
        sevens_hit = sevens = other_fp = 0
        for p in frames:
            img = cv2.imread(p)
            if img is None:
                continue
            rgb = img[:, :, ::-1]
            h, w = rgb.shape[:2]
            pills = dr.find_pills(rgb, (0, 0, w, h))
            slots = NEW._slots(rgb, (0, 0, w, h), pills)
            vis, _ = NEW._is_skillbar(rgb, (0, 0, w, h), pills, slots)
            if not vis:
                continue
            seen += 1
            for s in slots:
                if not s:
                    continue
                r = NEW.read_skill_with_fallback(rgb, s, tpl)
                q += r["text"].count("?")
                # 统计 7 判据的查全/查准（这批语料的真值见 README：
                # --/425/70/100 与 --/212/70/100，所以只有 70 里那个 7）
                gl = dr.extract_glyphs(rgb, s)
                raw = dr.read_skill(rgb, s, tpl)["text"]
                if raw.count("?") or len(gl) != len(raw):
                    continue
                for g, ch in zip(gl, raw):
                    if not ch.isdigit():
                        continue
                    glyphs += 1
                    flag = NEW._looks_like_seven(g["glyph"])
                    if ch == "7":
                        sevens += 1
                        sevens_hit += 1 if flag else 0
                    elif flag:
                        other_fp += 1
        good = (q == 0 and other_fp == 0 and sevens_hit == sevens)
        ok_all &= good
        # 这批语料的真值是 --/425/70/100 和 --/212/70/100，
        # README 记录当时 110 帧里 108 帧可见、0 个 '?'。
        P("  %s 可见 %d 帧，'?' 共 %d 个（期望 0）"
          % ("[OK]  " if q == 0 else "[FAIL]", seen, q))
        P("  %s 7 判据：真实 7 认出 %d/%d，非 7 误判 %d/%d"
          % ("[OK]  " if (other_fp == 0 and sevens_hit == sevens) else "[FAIL]",
             sevens_hit, sevens, other_fp, glyphs - sevens))

    # ---- 1d. 位数闸门：4 位以上的读数是切分错误，必须降级成读不出 ----
    P("")
    P("== 位数闸门（4 位以上 = 多切进一个字形）==")
    path = os.path.join(config.LOG_DIR, "bar_partial_054346.png")
    if not os.path.exists(path):
        P("  跳过（截图不在）")
    else:
        img = cv2.imread(path)
        rgb = img[:, :, ::-1]
        h, w = rgb.shape[:2]
        pills = dr.find_pills(rgb, (0, 0, w, h))
        slots = NEW._slots(rgb, (0, 0, w, h), pills)
        got = []
        for s in slots:
            if not s:
                got.append(None)
                continue
            got.append(NEW.read_skill_with_fallback(rgb, s, tpl)["text"])
        # 这一帧 slot 4 是 9 个字形（卡面图案被当成字），旧版会算成 10**7
        good = got[3] == "?"
        ok_all &= good
        P("  %s %-26s 读出 %s（slot4 期望 '?'）"
          % ("[OK]  " if good else "[FAIL]", os.path.basename(path), got))

    # ---- 2. 实时屏幕：冻结一帧真机画面，新旧两版跑同一帧 ----
    if not args.no_live:
        g = winio.find_game()
        if not g:
            P("== 实时屏幕：游戏未运行，跳过 ==")
        else:
            P("== 实时屏幕（冻结一帧，真实分辨率/缩放）==")
            frozen = FakeScreen.capture_live(winio.Screen())
            if frozen is None:
                P("  抓屏失败，跳过")
            else:
                P("     客户区 %s  缩放 %s"
                  % (frozen.client_size, tuple(round(v, 3) for v in frozen._scale)))
                a2 = old.Perc(frozen)
                b2 = NEW.Perc(frozen)
                for name in ("音速犬", None):
                    a2.my_name = b2.my_name = name
                    ok_all &= cmp_state("live my_name=%s" % name,
                                        a2.observe(), b2.observe(), keys)

    P("")
    P("结果：%s" % ("ALL OK" if ok_all else "有差异"))
    with open(os.path.join(tempfile.gettempdir(), "test_perceive.txt"),
              "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
