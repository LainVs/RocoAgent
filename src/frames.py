# -*- coding: utf-8 -*-
"""frames.py - 高频抓帧 + 画面差异分桶（分辨率无关）。

用途
----
1. 把一局完整流程拆成"阶段"：大厅 -> VS -> 战斗 -> 结算 -> 大厅
2. 每个阶段只保留代表帧，避免存几十万张图
3. 给后续的状态识别提供真值样本

两个手段
--------
* **差异筛选**：相邻帧的平均绝对差 < DIFF_EPS 就丢掉（画面没变）
* **分桶去重**：同一阶段的帧按 8x8 灰度指纹聚类，同桶只留一张

用法
----
    python frames.py --seconds 120            # 抓 2 分钟
    python frames.py --seconds 600 --tag s5   # 打标签
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

import cv2
import numpy as np

import paths
import screen_state as S

OUT_DIR = str(paths.LOGS / "stages")
DIFF_EPS = 1.2          # 平均像素差小于这个就算"没变"
FP_DIST = 6.0           # 指纹距离小于这个就算同一张


def fingerprint(img, n=10):
    """n x n 灰度指纹，用于去重。"""
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    return cv2.resize(g, (n, n), interpolation=cv2.INTER_AREA).astype(np.float32)


def fp_dist(a, b):
    return float(np.abs(a - b).mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--fps", type=float, default=4.0)
    ap.add_argument("--tag", default="")
    ap.add_argument("--diff", type=float, default=DIFF_EPS)
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    import robot
    sc = robot.Screen()
    sc.refresh()
    if sc.rect is None:
        print("找不到游戏窗口")
        return 1

    print("客户区 %s  抓 %0.0f 秒 @ %.1f fps" % (sc.client_size, args.seconds, args.fps))
    interval = 1.0 / max(0.5, args.fps)
    deadline = time.time() + args.seconds

    prev = None
    buckets = {}            # state -> [(fp, path, detail)]
    n_grab = n_skip = n_save = 0
    t0 = time.time()

    while time.time() < deadline:
        img = sc.grab_full()
        if img is None:
            time.sleep(0.05)
            continue
        n_grab += 1

        if prev is not None:
            d = float(np.abs(img.astype(np.int16) - prev.astype(np.int16)).mean())
            if d < args.diff:
                n_skip += 1
                time.sleep(interval)
                continue
        prev = img

        state, detail = S.classify(img)
        fp = fingerprint(img)
        lst = buckets.setdefault(state, [])
        dup = any(fp_dist(fp, o[0]) < FP_DIST for o in lst)
        if not dup:
            ts = time.strftime("%H%M%S") + "_%03d" % int((time.time() % 1) * 1000)
            name = "%s%s_%s.png" % (state, ("_" + args.tag) if args.tag else "", ts)
            p = os.path.join(OUT_DIR, name)
            cv2.imwrite(p, img[:, :, ::-1])
            lst.append((fp, name, detail))
            n_save += 1

        time.sleep(interval)

    meta = {
        "tag": args.tag,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "client": list(sc.client_size or ()),
        "seconds": round(time.time() - t0, 1),
        "grabbed": n_grab,
        "skipped_same": n_skip,
        "saved": n_save,
        "states": {k: len(v) for k, v in buckets.items()},
        "detail": {k: [o[2] for o in v] for k, v in buckets.items()},
    }
    mp = os.path.join(OUT_DIR, "meta_%s.json" % (args.tag or "run"))
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("\n抓帧 %d，相同丢弃 %d，保存 %d" % (n_grab, n_skip, n_save))
    for k, v in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        print("  %-10s %d" % (k, len(v)))
    print("元数据 ->", mp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
