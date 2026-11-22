# -*- coding: utf-8 -*-
"""bench_avatar_ident.py —— 头像通道的离线评测（单一事实来源）。

直接调用 `src/avatar_ident.py` 的银行与打分，不复制实现，保证
「文档里的数字」和「线上代码」是同一条路径。

评测集
------
`assets/portraits_foe/*.png` 里 162 张**已标注**的实机 HUD 裁图
（97 张普通 + 65 张 `#变体`）。真值来自 `portrait_db.species_of`。

口径
----
1. 属性约束用**真值属性**（假设属性图标 100% 读对）——这是上限；
   线上第二个属性常被挡，池子更大、精度会更低。
2. ONNX 的输入按线上 `hud_crop()` 补 10px 镜像边（104x96）。直接喂紧框
   会让头像相对偏大，实测掉 10+ 个百分点。

用法
----
    & $py scripts\\bench_avatar_ident.py
    & $py scripts\\bench_avatar_ident.py --no-onnx
    & $py scripts\\bench_avatar_ident.py --json logs/avatar_ident.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import avatar_ident as A          # noqa: E402
import foe_ident                  # noqa: E402
import portrait_db as P           # noqa: E402

FOE_DIR = os.path.join(ROOT, "assets", "portraits_foe")
PAD = 10


def imread_c(path):
    try:
        buf = np.fromfile(path, dtype=np.uint8)
        if buf.size == 0:
            return None
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except (OSError, ValueError):
        return None


def load_labelled():
    out = []
    for f in sorted(glob.glob(os.path.join(FOE_DIR, "*.png"))):
        stem = os.path.basename(f)
        if stem.startswith("_") or "_name" in stem or stem.startswith("sid"):
            continue
        sid = P.species_of(stem, "foe")
        img = imread_c(f)
        if sid and img is not None:
            out.append({"file": stem, "sid": int(sid),
                        "truth": P.species_name(sid),
                        "variant": "#" in stem, "img": img})
    return out


def onnx_top(img, topk=5):
    try:
        import portrait_onnx as PO
        if not PO.available():
            return []
        pad = cv2.copyMakeBorder(img, PAD, PAD, PAD, PAD, cv2.BORDER_REFLECT_101)
        return [(A.normalize(n), float(c)) for n, c in
                (PO.predict(pad, topk=topk) or [])]
    except Exception:
        return []


def evaluate(use_onnx=True):
    info = A.bank_info("foe")
    if not info.get("available"):
        raise SystemExit("avatar bank unavailable")
    print("avatar bank: %d ids, %d views, dim=%d, %d MB"
          % (info["n_ids"], info["n_views"], info["dim"],
             os.path.getsize(info["path"]) // 1048576
             if os.path.exists(info["path"]) else -1))
    have = set(int(s) for s in A.bank("foe")["ids"])
    files = load_labelled()
    print("labelled HUD crops: %d (identifiable %d)"
          % (len(files), sum(1 for f in files if f["sid"] in have)))

    tcache = {}

    def pool_of(name):
        tt = tcache.get(name)
        if tt is None:
            tt = foe_ident.types_of(name) or []
            tcache[name] = tt
        return foe_ident.candidates_by_types(tt) if tt else None

    rows = []
    t0 = time.time()
    for i, item in enumerate(files):
        truth = item["truth"]
        pool = pool_of(truth)
        av = A.identify(item["img"], side="foe", candidates=pool,
                        is_rgb=False, topk=3)
        on = []
        if use_onnx:
            on = [(n, c) for n, c in onnx_top(item["img"])
                  if not pool or n in {A.normalize(x) for x in pool}]
        rows.append({
            "truth": truth,
            "variant": item["variant"],
            "identifiable": item["sid"] in have,
            "pool": len(pool) if pool else 0,
            "av": av[0]["name"] if av else None,
            "av_score": av[0]["score"] if av else None,
            "av_conf": av[0]["conf"] if av else "none",
            "av_ok": bool(av) and A.normalize(av[0]["name"]) == A.normalize(truth),
            "on": on[0][0] if on else None,
            "on_ok": bool(on) and on[0][0] == A.normalize(truth),
        })
        if (i + 1) % 40 == 0:
            print("  ... %d/%d" % (i + 1, len(files)))

    dt = time.time() - t0
    print("evaluated in %.1fs (%.0f ms/query)"
          % (dt, 1000.0 * dt / max(1, len(files))))
    return rows, use_onnx


def report(rows, use_onnx):
    subsets = [
        ("all", lambda r: True),
        ("plain", lambda r: not r["variant"]),
        ("#variant", lambda r: r["variant"]),
        ("identifiable", lambda r: r["identifiable"]),
        ("identifiable/plain", lambda r: r["identifiable"] and not r["variant"]),
    ]
    print("\n%-20s %4s %6s %9s %9s %9s %8s"
          % ("subset", "n", "pool", "avatar", "high", "onnx", "union"))
    for label, pred in subsets:
        rs = [r for r in rows if pred(r)]
        n = len(rs)
        if not n:
            continue
        av = sum(r["av_ok"] for r in rs)
        hi = sum(r["av_ok"] for r in rs if r["av_conf"] == "high")
        on = sum(r["on_ok"] for r in rs) if use_onnx else None
        un = sum(r["av_ok"] or r["on_ok"] for r in rs) if use_onnx else None
        print("%-20s %4d %6.1f %8.1f%% %8.1f%% %9s %8s"
              % (label, n, np.mean([r["pool"] for r in rs]),
                 100.0 * av / n, 100.0 * hi / n,
                 "-" if not use_onnx else "%.1f%%" % (100.0 * on / n),
                 "-" if not use_onnx else "%.1f%%" % (100.0 * un / n)))
    # 精确率：只说 high 的那些，错了几个
    hi = [r for r in rows if r["av_conf"] == "high"]
    if hi:
        print("\nhigh-confidence votes: %d, precision %.1f%%"
              % (len(hi), 100.0 * sum(r["av_ok"] for r in hi) / len(hi)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-onnx", action="store_true")
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    rows, use_onnx = evaluate(use_onnx=not args.no_onnx)
    report(rows, use_onnx)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=1)
        print("wrote", args.json)


if __name__ == "__main__":
    main()
