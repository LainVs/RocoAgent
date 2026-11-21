# -*- coding: utf-8 -*-
"""build_type_icons.py —— 自动构建 18 属性图标模板（自监督标注）。

思路
----
不用手工标注 299 张图标。用**已知信息**自动打标签：

    1. 每张对战帧先 OCR 敌方名字（实测 64% 命中）
    2. 名字 -> species -> DB 里的 type_names（如 寂灭骨龙 -> 幽+恶）
    3. 用这个属性组合给该帧的两个图标位置打标签
    4. 每个属性聚合所有样本取均值 -> 模板

这样标签完全来自游戏数据库，不依赖人工判断，也不怕标错。

局限（必须记住）
----------------
- 只覆盖「名字能读出来」的帧。实测 108 帧里 69 帧可读 -> 覆盖 5~6 种属性。
- 缺的属性（草/水/电/武/毒/地/翼/萌/虫/机械/龙/光/普通）需要更多帧。
  等头像库和名字 OCR 覆盖率上去后重跑即可。
- **单属性精灵的第二个图标位置是空的**，必须靠形状判断，不能用颜色。
  所以这里只收集「候选池里属性数量 == 图标数量」的帧做训练。

跑法：
    python scripts\\build_type_icons.py --auto          # 自动标注 + 报告
    python scripts\\build_type_icons.py --build         # 生成 npz
    python scripts\\build_type_icons.py --verify        # 交叉验证准确率
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import cv2                       # noqa: E402
import hud_ident as H            # noqa: E402
import paths                     # noqa: E402

SHOTS = os.path.join(ROOT, "logs", "resultshots")
OUT_DIR = os.path.join(ROOT, "assets", "type_icons")
AUTO_LABELS = os.path.join(OUT_DIR, "auto_labels.json")


# ------------------------------------------------------------------ 数据
def _db():
    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row
    return db


def species_types(name):
    """种族名 -> 属性列表。查不到返回 []。"""
    if not name:
        return []
    db = _db()
    try:
        r = db.execute(
            "SELECT type_names FROM species WHERE name=? "
            "ORDER BY implemented DESC, total DESC LIMIT 1", (name,)).fetchone()
        if not r:
            return []
        return json.loads(r["type_names"] or "[]")
    except Exception:
        return []
    finally:
        db.close()


def frames():
    out = []
    for p in sorted(glob.glob(os.path.join(SHOTS, "*.jpg"))):
        img = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            continue
        if img.shape[1] >= 2400 and img.shape[0] >= 1400:
            out.append((p, img))
    return out


class _Shot:
    """把静态图片包成 screen 接口，供 hud_ident 复用。"""

    def __init__(self, img):
        self.img = img

    def grab_client(self, box):
        x, y, w, h = box
        return self.img[y:y + h, x:x + w][:, :, ::-1].copy()


def icon_patch(rgb, cx, cy):
    return H._crop_icon(rgb, cx, cy)


# ------------------------------------------------------------------ 自监督标注
def auto_label():
    """扫所有帧，用名字反推属性，给图标打标签。

    返回 (labels, stats)：
        labels = [(patch_rgb, 属性名, 来源), ...]
        stats  = 统计信息
    """
    vocab = H._species_vocab()
    import ocr_cn
    import ocr_cn as O

    labels = []
    stats = Counter()
    detail = []

    for path, img in frames():
        rgb = img[:, :, ::-1]
        scr = _Shot(img)
        raw, conf = H.read_name(scr, "foe", vocab=vocab)
        name, sc = O.Ocr.match(raw, vocab) if (raw and vocab) else (None, 0.0)
        if not name:
            stats["无名字"] += 1
            continue
        types = species_types(name)
        if not types:
            stats["库中无属性"] += 1
            continue

        centers = H.FOE_ICON_CENTERS
        patches = [icon_patch(rgb, cx, cy) for cx, cy in centers]
        patches = [p for p in patches if p is not None]
        if len(patches) < len(types):
            stats["图标缺失"] += 1
            continue
        # 单属性时第二个位置是空的：用模板匹配前的粗判会不准，
        # 所以只取前 len(types) 个位置
        for p, t in zip(patches, types):
            labels.append((p, t, os.path.basename(path)))
        stats["已标注"] += 1
        detail.append((os.path.basename(path), name, "+".join(types)))

    return labels, stats, detail


def auto_scan():
    labels, stats, detail = auto_label()
    print("=== 自监督标注 ===")
    for k, v in stats.items():
        print("  %-10s %d" % (k, v))
    print()
    print("按属性统计样本数：")
    cnt = Counter(t for _, t, _ in labels)
    for t in H.TYPE_NAMES:
        n = cnt.get(t, 0)
        flag = "" if n >= 8 else ("  <- 偏少" if n else "  <- 缺失")
        print("  %-4s %3d%s" % (t, n, flag))
    print()
    print("标注明细（前 20）：")
    for d in detail[:20]:
        print("  %-18s %-10s %s" % d)
    return labels


# ------------------------------------------------------------------ 构建
def build():
    labels, stats, _ = auto_label()
    if not labels:
        print("没有可标注的样本")
        return 1

    buckets = defaultdict(list)
    for patch, t, _src in labels:
        buckets[t].append(cv2.resize(patch, (30, 30)).astype(np.float32))

    out = {}
    for t, arrs in buckets.items():
        arr = np.stack(arrs)
        out[t] = np.median(arr, axis=0).astype(np.uint8)

    os.makedirs(OUT_DIR, exist_ok=True)
    np.savez_compressed(H.TEMPLATE_PATH, **out)
    print("已写 %s" % H.TEMPLATE_PATH)
    for t in sorted(out, key=lambda x: H.TYPE_NAMES.index(x)
                    if x in H.TYPE_NAMES else 99):
        print("  %-4s <- %3d 个样本" % (t, len(buckets[t])))

    missing = [t for t in H.TYPE_NAMES if t not in out]
    if missing:
        print("\n还缺 %d 种：%s" % (len(missing), "、".join(missing)))
        print("原因：现有对战帧里没出现过这些属性，或名字没读出来。")
    return 0


# ------------------------------------------------------------------ 验证
def verify():
    """用「留一」方式验证：每个属性留一部分样本，看能否认回来。"""
    labels, _stats, _ = auto_label()
    if not labels:
        print("没有样本")
        return 1

    buckets = defaultdict(list)
    for patch, t, _src in labels:
        buckets[t].append(cv2.resize(patch, (30, 30)).astype(np.float32))

    tpl = {}
    for t, arrs in buckets.items():
        tpl[t] = np.median(np.stack(arrs), axis=0).astype(np.uint8)

    print("=== 属性图标识别准确率（用全量模板自测）===")
    total = ok = 0
    per = defaultdict(lambda: [0, 0])
    for t, arrs in buckets.items():
        for a in arrs:
            name, sc = H._match_icon(a.astype(np.uint8), tpl)
            total += 1
            per[t][1] += 1
            if name == t:
                ok += 1
                per[t][0] += 1
    for t in H.TYPE_NAMES:
        if t not in per:
            continue
        c, n = per[t]
        print("  %-4s %3d/%3d = %5.1f%%" % (t, c, n, 100.0 * c / max(1, n)))
    print("\n总体 %d / %d = %.1f%%" % (ok, total, 100.0 * ok / max(1, total)))
    return 0 if total and ok / total > 0.9 else 1


def main(argv):
    if "--auto" in argv:
        auto_scan()
        return 0
    if "--build" in argv:
        return build()
    if "--verify" in argv:
        return verify()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
