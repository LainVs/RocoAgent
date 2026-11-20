"""compose_test.py —— 把悬浮窗面板合成到真实游戏截图上，测最差情况对比度。

为什么需要
----------
在深色背景上做设计等于自嗨。真实战场是浅灰石地面（亮度 ~190）、橙红建筑、
绿色植物。半透明深色面板压上去会**变亮**，白字对比度断崖式下降。

这个脚本回答两个问题：
  1. 面板底色 alpha 降到多少，文字开始不可读？
  2. 文字描边（text-shadow halo）能救回多少？

做法
----
不依赖浏览器截图：用 PIL 把面板等效色块画上去。色值从 CSS 真实渲染值取
（见 PALETTE），改 CSS 时同步改这里。文字对比度按 WCAG 公式算。

关键：对比度要对「文字紧邻的局部背景」算，而不是整块面板。
文字描边会形成一层暗色光晕，局部背景 = blend(黑, 面板色, halo_alpha)。

用法
----
    python ui/compose_test.py              # 全部背景 × 全部透明度
    python ui/compose_test.py --bg turn1   # 只测最亮那张
    python ui/compose_test.py --save       # 存对照图到 logs/ui_compose/
"""
from __future__ import annotations

import argparse
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, "logs")
OUT = os.path.join(LOGS, "ui_compose")

BACKGROUNDS = [
    ("turn1.jpg",             "最亮：浅灰石地面 + 橙红建筑"),
    ("shot_game.jpg",         "亮：白天广场"),
    ("shot_now2.png",         "中：室内偏亮"),
    ("result_hit_232891.png", "结算页（若缺则跳过）"),
    ("result_hit_232922.png", "结算页"),
    ("shot_now.png",          "最暗：夜景"),
]

# 面板配色 —— 与 style.css 一一对应
PANEL_RGB = (9, 11, 15)

# 文字层级：(名字, 不透明度, 用途)
INKS = [
    ("ink0", 1.00, "精灵名 / 百分比 / 伤害数字"),
    ("ink1", 0.82, "技能名"),
    ("ink2", 0.62, "血量绝对值 / 推测区间"),
    ("ink3", 0.42, "序号 / 单位 / 极次要"),
]

ACCENT = (255, 107, 74)

# 描边强度：text-shadow 0 1px 2px rgba(0,0,0,0.9) 的等效局部压暗
HALO = 0.62          # 局部背景被压到原来的 38%

ALPHAS = [0.20, 0.35, 0.50, 0.68, 0.84, 1.00]

PANEL_W, PANEL_H = 320, 560


# ---------------------------------------------------------------- 对比度
def _lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rel_lum(rgb):
    r, g, b = (_lin(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    a, b = rel_lum(fg), rel_lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def blend(fg, bg, a):
    return tuple(int(round(fg[i] * a + bg[i] * (1 - a))) for i in range(3))


def text_on(ink_alpha, panel_on, halo):
    """返回 (文字实际色, 文字紧邻的局部背景色)。"""
    fg = blend((242, 244, 247), panel_on, ink_alpha)
    local_bg = blend((0, 0, 0), panel_on, halo)
    return fg, local_bg


# ---------------------------------------------------------------- 取样
def sample_background(path):
    """返回 (亮, 暗) 两个代表色 —— 取极值，不取平均。"""
    img = Image.open(path).convert("RGB")
    w, h = img.size
    # 面板会放在右侧偏中
    crop = img.crop((int(w * 0.52), int(h * 0.10), int(w * 0.92), int(h * 0.90)))
    arr = np.asarray(crop).reshape(-1, 3).astype(np.float32)
    lum = arr @ np.array([0.2126, 0.7152, 0.0722])
    order = np.argsort(lum)
    dark = arr[order[max(0, int(len(order) * 0.05))]]
    light = arr[order[min(len(order) - 1, int(len(order) * 0.95))]]
    return tuple(int(v) for v in light), tuple(int(v) for v in dark)


# ---------------------------------------------------------------- 渲染
def _font(size, bold=False):
    for name in ("msyhbd.ttc" if bold else "msyh.ttc",
                 "simhei.ttf", "Deng.ttf"):
        p = os.path.join("C:\\Windows\\Fonts", name)
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def draw_panel(canvas, x, y, worst_bg, alpha):
    """画一块面板；返回 {ink 名: 最低对比度}。"""
    d = ImageDraw.Draw(canvas, "RGBA")
    panel_on = blend(PANEL_RGB, worst_bg, alpha)

    d.rounded_rectangle([x, y, x + PANEL_W, y + PANEL_H], 14,
                        fill=(*PANEL_RGB, int(alpha * 255)),
                        outline=(255, 255, 255, 26), width=1)

    f_big = _font(22, True)
    f_name = _font(16, True)
    f_mid = _font(13)
    f_small = _font(11)

    rows = [
        (14,  "寂灭骨龙",      f_name,  "ink0"),
        (40,  "龙",            f_small, "ink2"),
        (62,  "382 / 490",     f_small, "ink2"),
        (88,  "水灵",          f_name,  "ink0"),
        (114, "血量 349~419",  f_small, "ink2"),
        (134, "性格 固执 62%", f_small, "ink3"),
        (170, "隼鳞",          f_mid,   "ink1"),
        (200, "龙之怒",        f_mid,   "ink1"),
        (230, "碎岩",          f_mid,   "ink1"),
        (260, "龙舞",          f_mid,   "ink3"),
        (300, "2 回合击杀",    f_small, "ink2"),
    ]

    worst = {k: 99.0 for k, _, _ in INKS}

    for dy, text, font, key in rows:
        a = dict((k, v) for k, v, _ in INKS)[key]
        fg, bg = text_on(a, panel_on, HALO)
        d.text((x + 14, y + dy), text, font=font, fill=(*fg, 255),
               stroke_width=1, stroke_fill=(0, 0, 0, 160))
        worst[key] = min(worst[key], contrast(fg, bg))

    for dy, text in ((14, "78%"), (88, "45%")):
        fg, bg = text_on(1.0, panel_on, HALO)
        d.text((x + PANEL_W - 52, y + dy), text, font=f_big, fill=(*fg, 255),
               stroke_width=1, stroke_fill=(0, 0, 0, 160))
        worst["ink0"] = min(worst["ink0"], contrast(fg, bg))

    for i, frac in enumerate((0.58, 0.44, 0.22)):
        by = y + 186 + i * 30
        d.rectangle([x + 14, by, x + PANEL_W - 60, by + 6], fill=(255, 255, 255, 14))
        d.rectangle([x + 14, by, x + 14 + int((PANEL_W - 74) * frac), by + 6],
                    fill=(*ACCENT, 255))

    return worst, panel_on


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bg", help="只测这个背景（文件名前缀）")
    ap.add_argument("--save", action="store_true", help="保存对照图")
    args = ap.parse_args()

    if args.save:
        os.makedirs(OUT, exist_ok=True)

    print("=" * 76)
    print("悬浮窗面板 x 真实游戏画面 —— 文字对比度实测（含 1px 描边）")
    print("=" * 76)
    print("判据：主信息 >= 4.5:1 (WCAG AA)   次要 >= 3.0:1   极次要 >= 2.2:1")
    print()

    table = {}      # alpha -> {ink: [worst across bgs]}

    for name, desc in BACKGROUNDS:
        path = os.path.join(LOGS, name)
        if not os.path.exists(path):
            continue
        if args.bg and not name.startswith(args.bg):
            continue

        light, _dark = sample_background(path)
        print("%-24s [%s]" % (name, desc))
        print("  最亮背景 = %s" % (light,))
        print("  %-7s %-16s %-8s %-8s %-8s %-8s" % (
            "alpha", "面板实际色", "ink0", "ink1", "ink2", "ink3"))
        print("  " + "-" * 64)

        for a in ALPHAS:
            tmp = Image.new("RGB", (PANEL_W + 40, PANEL_H + 40), (0, 0, 0))
            worst, panel_on = draw_panel(tmp, 20, 20, light, a)

            for k, v in worst.items():
                table.setdefault(a, {}).setdefault(k, []).append(v)

            print("  %-7.2f %-16s %-8.2f %-8.2f %-8.2f %-8.2f" % (
                a, str(panel_on), worst["ink0"], worst["ink1"],
                worst["ink2"], worst["ink3"]))

            if args.save:
                base = Image.open(path).convert("RGB")
                bw, bh = base.size
                canvas = base.copy()
                draw_panel(canvas, int(bw * 0.60), int(bh * 0.12), light, a)
                tag = "%s_%03d.png" % (os.path.splitext(name)[0], int(a * 100))
                canvas.save(os.path.join(OUT, tag))
        print()

    print("=" * 76)
    print("跨全部背景的最低值")
    print("=" * 76)
    print("  %-7s %-8s %-8s %-8s %-8s" % ("alpha", "ink0", "ink1", "ink2", "ink3"))
    print("  " + "-" * 44)
    for a in sorted(table):
        row = [min(table[a].get(k, [99])) for k in ("ink0", "ink1", "ink2", "ink3")]
        print("  %-7.2f %-8.2f %-8.2f %-8.2f %-8.2f" % (a, *row))

    print()
    print("结论口径：")
    print("  ink0 是必须时刻看清的（血量百分比 / 伤害数字），任何 alpha 都要 >= 4.5")
    print("  ink3 只放序号和单位，>= 2.2 就够")
    print("  20%% 是「我要隐身」的场景，允许 ink2/ink3 掉到 3.0 附近")


if __name__ == "__main__":
    main()
