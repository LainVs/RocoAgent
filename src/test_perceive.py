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
    """用一整帧合成画面冒充 Screen，验证切片坐标是否和 grab_s 一致。"""

    BASE_CLIENT_W, BASE_CLIENT_H = 2560, 1600
    BASE_OFF_X, BASE_OFF_Y = 13, 58

    def __init__(self, frame_bgra):
        self.frame = frame_bgra
        self.client_off = (13, 58)
        self.client_size = (2560, 1600)
        self.rect = (0, 0, 2586, 1671)
    def scale(self):
        return 1.0, 1.0

    def _cut(self, px, py, pw, ph):
        return self.frame[py:py + ph, px:px + pw]

    def grab_s(self, box):
        # 复刻 winio：sbox 减 BASE_OFF，然后切 -> RGB
        x, y, w, h = box
        px = (x - self.BASE_OFF_X)
        py = (y - self.BASE_OFF_Y)
        sub = self._cut(px, py, w, h)
        return cv2.cvtColor(sub, cv2.COLOR_BGRA2RGB)

    def grab_frame(self):
        return self.frame, (1.0, 1.0)

    def crop_frame(self, frame, box, scale=None, client=False):
        sx, sy = scale or (1.0, 1.0)
        x, y, w, h = box
        if client:
            px, py = int(x * sx), int(y * sy)
        else:
            px = int(round((x - self.BASE_OFF_X) * sx))
            py = int(round((y - self.BASE_OFF_Y) * sy))
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
    """造一整帧：血条 + 技能栏（用真实抓到的 bar_partial）+ 倒计时空白。"""
    f = np.zeros((1600, 2560, 4), np.uint8)
    f[:, :, 3] = 255

    # MY_BAR: 绿色满条。_read_bar 看的是填充比例，画满即可
    mx, my, mw, mh = 166, 170, 244, 20
    f[my:my + mh, mx:mx + mw] = (60, 210, 80, 255)

    # FOE_BAR: 右半边空 -> 约 50%，但要留下灰色底板让 ui=True
    fx, fy, fw, fh = 2251, 170, 245, 20
    f[fy:fy + fh, fx:fx + fw] = (95, 95, 95, 255)
    f[fy:fy + fh, fx + fw // 2:fx + fw] = (60, 210, 80, 255)

    # SKILL_BOX: 贴真实抓到的技能栏（171/114/45/102）
    bar = cv2.imread(os.path.join(config.LOG_DIR, "bar_partial_093057.png"))
    if bar is not None:
        x0, y0, x1, y1 = config.SKILL_BOX
        px = x0 - 13
        py = y0 - 58
        h = min(bar.shape[0], 1600 - py)
        w = min(bar.shape[1], 2560 - px)
        f[py:py + h, px:px + w, :3] = bar[:h, :w]
    return f


def cmp_state(tag, a, b, keys):
    bad = []
    for k in keys:
        if a.get(k) != b.get(k):
            bad.append("%s old=%r new=%r" % (k, a.get(k), b.get(k)))
    sa, sb = a.get("skills") or [], b.get("skills") or []
    if len(sa) != len(sb):
        bad.append("skills len %d != %d" % (len(sa), len(sb)))
    else:
        for i, (x, y) in enumerate(zip(sa, sb)):
            for kk in ("text", "value", "kind", "color", "score", "plate",
                       "lit", "skill_name", "skill_kind"):
                xv = None if x is None else x.get(kk)
                yv = None if y is None else y.get(kk)
                if xv != yv:
                    bad.append("skill%d.%s old=%r new=%r" % (i + 1, kk, xv, yv))
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

    # ---- 2. 实时屏幕：大厅态（bar=False，覆盖另一条分支）----
    if not args.no_live:
        g = winio.find_game()
        if not g:
            P("== 实时屏幕：游戏未运行，跳过 ==")
        else:
            P("== 实时屏幕（真实分辨率/缩放）==")
            scr = winio.Screen()
            a2 = old.Perc(scr)
            b2 = NEW.Perc(scr)
            for i in range(3):
                st_o = a2.observe()
                st_n = b2.observe()
                # 两次抓屏之间画面可能变（动效），只比稳定字段
                ok_all &= cmp_state("live #%d" % (i + 1), st_o, st_n, keys)

    P("")
    P("结果：%s" % ("ALL OK" if ok_all else "有差异"))
    with open(os.path.join(tempfile.gettempdir(), "test_perceive.txt"),
              "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
