# -*- coding: utf-8 -*-
"""eval_det.py —— 用人工真值评估各通道（这是唯一可信的精度报告）

真值来自 logs/annot/labels.jsonl（人工看图读的，不是 OCR）。
评估三条通道：
    px    像素填充比例（matdet.measure_fill）
    dg    字形数字（digitmat.read_bar_digits）
    fuse  融合（digitmat.fuse）

三个关键指标（比"准确率"更有用）
--------------------------------
    覆盖      报出值的比例（None 不算错，算没覆盖）
    ≤1pt      报出的值里误差 ≤1 个百分点的比例
    错报      报出了值但误差 > 2pt —— 这是最危险的，机器人会照着做

负样本（pip 行 / 场景）单独统计"是否报出了值"：报出即为误报。

用法
    python scripts\\eval_det.py
    python scripts\\eval_det.py --by-barh
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import matdet      # noqa: E402
import digitmat    # noqa: E402

ANNOT = os.path.join(ROOT, "logs", "annot")


def to_client(img):
    if img is None:
        return None
    if img.shape[:2] == (1671, 2586):
        return img[58:58 + 1600, 13:13 + 2560]
    return img


def load():
    idx = json.load(open(os.path.join(ANNOT, "index.json"), encoding="utf-8"))
    lab = {}
    with open(os.path.join(ANNOT, "labels.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                d = json.loads(line)
                lab[d["idx"]] = d
    return idx, lab


def pct(a, b):
    return 100.0 * a / b if b else 0.0


def summarize(name, rows):
    """rows: list of dict(err=float|None, reported=bool, truth=...)"""
    n = len(rows)
    rep = [r for r in rows if r["reported"]]
    errs = [abs(r["err"]) for r in rep if r["err"] is not None]
    ok1 = sum(1 for e in errs if e <= 1.0)
    ok2 = sum(1 for e in errs if e <= 2.0)
    bad = sum(1 for e in errs if e > 2.0)
    med = statistics.median(errs) if errs else None
    print("  %-6s 覆盖 %5.1f%% (%d/%d)   中位误差 %s   ≤1pt %s   ≤2pt %s   错报 %d"
          % (name, pct(len(rep), n), len(rep), n,
             ("%.2f" % med) if med is not None else "-",
             "%d/%d" % (ok1, len(errs)) if errs else "-",
             "%d/%d" % (ok2, len(errs)) if errs else "-", bad))
    return {"coverage": len(rep), "n": n, "median": med,
            "ok1": ok1, "ok2": ok2, "bad": bad, "n_err": len(errs)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--by-barh", action="store_true")
    ap.add_argument("--dump", action="store_true", help="逐行打印真值与各通道读数")
    ap.add_argument("--strict", action="store_true",
                    help="只接受'完整包住数字'的候选（fail-closed）")
    a = ap.parse_args()

    idx, lab = load()
    cache = {}
    pos = {"px": [], "dg": [], "fuse": []}
    neg = {"px": [], "dg": [], "fuse": []}
    text_ok = text_tot = 0
    buckets = {}

    for it in idx:
        i = it["idx"]
        lb = lab.get(i)
        if lb is None:
            continue
        f = os.path.join(ROOT, it["file"])
        if f not in cache:
            cache[f] = to_client(cv2.imread(f))
        img = cache[f]
        if img is None:
            continue
        hud = matdet.locate_hud(img, strict=a.strict)
        box = hud["%s_bar" % it["side"]]
        px = hud["%s_ratio" % it["side"]]
        if box is None:
            # 没定位到：正样本算"没覆盖"，负样本算"正确地没报"
            if lb["kind"] == "hp" and lb.get("value") is not None:
                for k in pos:
                    pos[k].append({"reported": False, "err": None})
            else:
                for k in neg:
                    neg[k].append({"reported": False, "err": None})
            continue
        dg, txt, _conf = digitmat.read_bar_digits(
            img, box, it["side"], prior=px)
        fu, _src, _agree = digitmat.fuse(px, dg)
        truth = lb.get("value")
        if a.dump:
            print("  %02d %-5s h=%2d truth=%-14s px=%-7s dg=%s"
                  % (i, lb["kind"], it["bar_h"],
                     (lb.get("text") or "-")
                     + ("" if truth is None else "=%.3f" % truth),
                     ("%.3f" % px) if px is not None else "-",
                     txt or "-"))

        if lb["kind"] == "hp" and truth is not None:
            for name, v in (("px", px), ("dg", dg), ("fuse", fu)):
                rep = v is not None
                e = (abs(v - truth) * 100.0) if rep else None
                pos[name].append({"reported": rep, "err": e,
                                  "bar_h": it["bar_h"]})
                if name == "px":
                    b = "%d-%d" % (it["bar_h"] // 4 * 4, it["bar_h"] // 4 * 4 + 3)
                    buckets.setdefault(b, []).append(e)
            if dg is not None:
                text_tot += 1
                want = (lb.get("text") or "").replace(" ", "")
                got = (txt or "").replace(" ", "")
                # 我方是 cur/max，敌方是 NN% ；比较"数字部分"
                if want and got:
                    wn = "".join(ch for ch in want if ch.isdigit())
                    gn = "".join(ch for ch in got if ch.isdigit())
                    if wn == gn:
                        text_ok += 1
        else:
            for name, v in (("px", px), ("dg", dg), ("fuse", fu)):
                neg[name].append({"reported": v is not None, "err": None})

    print("=" * 78)
    print("真值样本：%d 条（人工读数，非 OCR）" % len(lab))
    print("\n[血条帧] 值精度")
    res_pos = {k: summarize(k, v) for k, v in pos.items()}
    print("\n[负样本] 不该报值的帧（pip 行 / 场景）")
    for k in ("px", "dg", "fuse"):
        v = neg[k]
        if not v:
            continue
        rep = sum(1 for r in v if r["reported"])
        print("  %-6s 误报 %5.1f%% (%d/%d)" % (k, pct(rep, len(v)), rep, len(v)))
    print("\n[字形通道] 数字串完全一致：%d/%d (%.1f%%)"
          % (text_ok, text_tot, pct(text_ok, text_tot)))

    if a.by_barh:
        print("\n[按血条高分箱] 像素通道误差")
        for b in sorted(buckets, key=lambda s: int(s.split("-")[0])):
            es = [e for e in buckets[b] if e is not None]
            if not es:
                print("  %-8s n=0" % b)
                continue
            print("  %-8s n=%2d  中位 %.2f  最大 %.2f"
                  % (b, len(es), statistics.median(es), max(es)))

    out = os.path.join(ANNOT, "eval_report.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"pos": res_pos, "neg": {k: len(v) for k, v in neg.items()},
                   "text_ok": text_ok, "text_total": text_tot},
                  fh, ensure_ascii=False, indent=1)
    print("\n-> %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
