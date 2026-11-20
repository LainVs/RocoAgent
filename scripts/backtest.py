# -*- coding: utf-8 -*-
"""backtest.py —— 用现有帧回测材质检测器（离线，不需要游戏/板子）

用法
----
    python scripts\\backtest.py --set frames --limit 40 --ocr 15 --save 8
    python scripts\\backtest.py --set shot   --limit 40 --ocr 15
    python scripts\\backtest.py --set all    --limit 60 --ocr 20

真值从哪来
----------
画面自己印着答案，这是本项目最大的便利：
    我方血条里是 "412/412"（绝对值）→ 比例 = cur/max
    敌方血条里是 "87%"             → 比例 = n/100
所以用 OCR 读这一小块就得到独立真值，不需要人工标注。

窗口帧（2586x1671，含 58px 标题栏 + 13px 边框）先裁成客户区 2560x1600，
否则检测器会把标题栏当成一条"深色长条"。运行时 robot 用的是 grab_client，
本来就没有标题栏，这一步只是让回测和线上对齐。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import matdet  # noqa: E402

CLIENT_CROP = (13, 58, 2560, 1600)   # 窗口帧 -> 客户区（58 = 标题栏，13 = 边框）


def to_client(img):
    """把带标题栏的窗口帧裁成客户区（只在尺寸完全匹配时动手）。"""
    if img is None:
        return None
    h, w = img.shape[:2]
    if (w, h) != (2586, 1671):
        return img
    x, y, cw, ch = CLIENT_CROP
    return img[y:y + ch, x:x + cw]


def load_set(name, limit):
    pats = {
        "frames": [os.path.join(ROOT, "logs", "frames", "*.png")],
        "shot": [os.path.join(ROOT, "logs", "shot", "*.jpg")],
        "resultshots": [os.path.join(ROOT, "logs", "resultshots", "*.jpg")],
        "stages": [os.path.join(ROOT, "logs", "stages", "*.png")],
        "result_hit": [os.path.join(ROOT, "logs", "result_hit_*.png")],
    }
    if name == "all":
        files = []
        for v in pats.values():
            files += sorted(glob.glob(v[0]))
    else:
        files = sorted(glob.glob(pats[name][0]))
    step = max(1, len(files) // limit) if limit and len(files) > limit else 1
    return files[::step][:limit]


# ---------------------------------------------------------------- OCR 真值

_OCR = None


def ocr_engine():
    global _OCR
    if _OCR is None:
        import ocr_cn
        _OCR = ocr_cn.Ocr()
    return _OCR


def ocr_ratio(img, box, side):
    """兼容旧调用：读血条印刷数字 -> 比例（真值）。"""
    return matdet.read_hp_text(img, box, side)


def ocr_ratio(img, box, side):
    """兼容旧调用：读血条印刷数字 -> 比例（真值）。"""
    return matdet.read_hp_text(img, box, side)


# ---------------------------------------------------------------- 画图

def annotate(img, hud, cards, path, title=""):
    vis = img.copy()
    for key, col in (("my_bar", (0, 255, 0)), ("foe_bar", (0, 255, 0))):
        b = hud.get(key)
        if b:
            cv2.rectangle(vis, (b["x"], b["y"]),
                          (b["x"] + b["w"], b["y"] + b["h"]), col, 2)
    for b in cards.get("nameplates", []):
        cv2.rectangle(vis, (b["x"], b["y"]),
                      (b["x"] + b["w"], b["y"] + b["h"]), (0, 255, 255), 1)
    for c in cards.get("cards", []):
        cv2.rectangle(vis, (c["x"], c["y"]),
                      (c["x"] + c["w"], c["y"] + c["h"]), (255, 128, 0), 1)
        cap = c.get("capsule")
        if cap:
            cv2.rectangle(vis, (cap["x"], cap["y"]),
                          (cap["x"] + cap["w"], cap["y"] + cap["h"]),
                          (0, 128, 255), 1)
    lines = [title]
    for key in ("my_ratio", "foe_ratio", "dy"):
        lines.append("%s=%s" % (key, hud.get(key)))
    y = 18
    for t in lines:
        if not t:
            continue
        cv2.putText(vis, t, (16, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(vis, t, (16, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (0, 255, 0), 1, cv2.LINE_AA)
        y += 20
    if vis.shape[1] > 1400:
        s = 1400.0 / vis.shape[1]
        vis = cv2.resize(vis, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    cv2.imwrite(path, vis)


# ---------------------------------------------------------------- 主流程

def run(args):
    files = load_set(args.set, args.limit)
    if not files:
        print("no frames for set=%s" % args.set)
        return 2
    outdir = os.path.join(ROOT, "logs", "det2")
    os.makedirs(outdir, exist_ok=True)

    n = 0
    n_my = n_foe = n_pair = 0
    err_my, err_foe = [], []
    dts, dt_cards = [], []
    n_cards4 = n_cards_try = 0
    ocr_used = 0
    n_ocr_my = n_ocr_foe = 0
    rows = []

    for i, f in enumerate(files):
        img = to_client(cv2.imread(f))
        if img is None:
            continue
        n += 1
        t0 = time.perf_counter()
        hud = matdet.locate_hud(img)
        dts.append((time.perf_counter() - t0) * 1000.0)
        t1 = time.perf_counter()
        cards = matdet.locate_cards(img)
        dt_cards.append((time.perf_counter() - t1) * 1000.0)

        if hud["my_bar"]:
            n_my += 1
        if hud["foe_bar"]:
            n_foe += 1
        if hud["my_bar"] and hud["foe_bar"]:
            n_pair += 1
        if hud["my_bar"] or hud["foe_bar"]:
            n_cards_try += 1
            if len(cards["cards"]) == 4:
                n_cards4 += 1

        row = {"f": os.path.basename(f), "size": list(img.shape[1::-1]),
               "my_bar": hud["my_bar"] is not None,
               "foe_bar": hud["foe_bar"] is not None,
               "dy": hud["dy"], "my_ratio": hud["my_ratio"],
               "foe_ratio": hud["foe_ratio"], "cards": len(cards["cards"])}

        if ocr_used < args.ocr and hud["my_bar"]:
            tm, text_m = ocr_ratio(img, hud["my_bar"], "my")
            tf, text_f = (ocr_ratio(img, hud["foe_bar"], "foe")
                          if hud["foe_bar"] else (None, ""))
            ocr_used += 1
            if tm is not None:
                n_ocr_my += 1
            if tf is not None:
                n_ocr_foe += 1
            row.update({"ocr_my": text_m, "ocr_foe": text_f,
                        "truth_my": tm, "truth_foe": tf})
            if tm is not None and hud["my_ratio"] is not None:
                e = abs(hud["my_ratio"] - tm) * 100.0
                err_my.append(e)
                row["err_my"] = round(e, 2)
            if tf is not None and hud["foe_ratio"] is not None:
                e = abs(hud["foe_ratio"] - tf) * 100.0
                err_foe.append(e)
                row["err_foe"] = round(e, 2)

        rows.append(row)

        if i < args.save:
            annotate(img, hud, cards,
                     os.path.join(outdir, "%s_%02d_%s.png"
                                  % (args.set, i, os.path.basename(f).rsplit(".", 1)[0])),
                     title="%s %dx%d" % (os.path.basename(f)[:22],
                                         img.shape[1], img.shape[0]))

    def pct(a, b):
        return 100.0 * a / b if b else 0.0

    def stat(v):
        if not v:
            return "-"
        a = np.array(v, dtype=np.float32)
        return "mean %.2f  p50 %.2f  p95 %.2f" % (a.mean(),
                                                 np.percentile(a, 50),
                                                 np.percentile(a, 95))

    print("=" * 72)
    print("set=%s  frames=%d" % (args.set, n))
    print("  我方底板命中   %5.1f%%  (%d/%d)" % (pct(n_my, n), n_my, n))
    print("  敌方底板命中   %5.1f%%  (%d/%d)" % (pct(n_foe, n), n_foe, n))
    print("  成对(双底板)   %5.1f%%  (%d/%d)" % (pct(n_pair, n), n_pair, n))
    if n_cards_try:
        print("  4 张卡齐全    %5.1f%%  (%d/%d)" % (pct(n_cards4, n_cards_try),
                                                   n_cards4, n_cards_try))
    print("  locate_hud     %s ms" % stat(dts))
    print("  locate_cards   %s ms" % stat(dt_cards))
    if err_my:
        print("  我方比例误差   %s  (n=%d)" % (stat(err_my), len(err_my)))
    if err_foe:
        print("  敌方比例误差   %s  (n=%d)" % (stat(err_foe), len(err_foe)))
    if ocr_used:
        print("  印刷数字解析   我方 %d/%d   敌方 %d/%d"
              % (n_ocr_my, ocr_used, n_ocr_foe, ocr_used))
    print("-" * 72)
    for r in rows:
        if r.get("truth_my") is not None or r.get("truth_foe") is not None:
            print("  %-34s bars=%s/%s dy=%s  mine %.3f vs %s(%s)  foe %.3f vs %s(%s)"
                  % (r["f"][-26:], int(r["my_bar"]), int(r["foe_bar"]), r["dy"],
                     r["my_ratio"] if r["my_ratio"] is not None else -1,
                     r.get("truth_my"), (r.get("ocr_my") or "")[:14],
                     r["foe_ratio"] if r["foe_ratio"] is not None else -1,
                     r.get("truth_foe"), (r.get("ocr_foe") or "")[:14]))
    with open(os.path.join(outdir, "report_%s.json" % args.set), "w",
              encoding="utf-8") as fh:
        json.dump({"set": args.set, "rows": rows}, fh, ensure_ascii=False, indent=1)
    print("annotated -> %s" % outdir)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="frames",
                    choices=["frames", "shot", "resultshots", "stages",
                             "result_hit", "all"])
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--ocr", type=int, default=12,
                    help="对前 N 帧做 OCR 真值（每帧约 0.3~0.6s）")
    ap.add_argument("--save", type=int, default=6, help="保存前 N 张标注图")
    a = ap.parse_args()
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
