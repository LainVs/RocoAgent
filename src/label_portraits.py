# -*- coding: utf-8 -*-
"""把机器人存的 _unknown_*.png 自动标成"精灵名.png"。

机器人每认不出一只就存两张图：
    _unknown_HHMMSS.png       头像（模板，56x60）
    _unknown_HHMMSS_name.png  左上角 HUD 的名字条（460x170，含名字/等级/血量）
名字条上印着名字，而精灵名是一张封闭词表（1015 个），所以 OCR 出来再
做最近邻匹配，准确率远高于自由识别。这样头像库就能自己长起来。

用法：
    python label_portraits.py --dry      # 只打印识别结果，不改文件
    python label_portraits.py            # 真的改名 + 写库
    python label_portraits.py --limit 20
"""
from __future__ import annotations

import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2          # noqa: E402
import numpy as np  # noqa: E402

import ocr_cn       # noqa: E402
import portrait_db as P   # noqa: E402

MIN_SCORE = 0.85
_LIB = [P.SELF_DIR]


def load(path):
    buf = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def read_name(path, ocr=None):
    """名字条 -> (精灵名, 置信度, 原始文本)。"""
    img = load(path)
    if img is None:
        return None, 0.0, ""
    if ocr is None:
        ocr = ocr_cn.Ocr()
    lines = ocr.read_region(img[:, :, ::-1])     # BGR -> RGB
    texts = [t for t, _c in lines]
    vocab = _vocab()
    best, best_s = None, 0.0
    for t in texts:
        # 血量行（26/393）、等级行（60级）不参与匹配
        if re.search(r"\d{2,}\s*/\s*\d{2,}", t) or re.fullmatch(r"\d+\s*级", t.strip()):
            continue
        name, s = ocr_cn.Ocr.match(t, vocab)
        if name and s > best_s:
            best, best_s = name, s
    return best, best_s, " | ".join(texts)


_vocab_cache = None


def _vocab():
    global _vocab_cache
    if _vocab_cache is None:
        import sqlite3
        db = sqlite3.connect(P.DB_PATH)
        _vocab_cache = [r[0] for r in
                        db.execute("SELECT DISTINCT name FROM species WHERE name IS NOT NULL")]
        db.close()
    return _vocab_cache


def _duplicate_of(bust, threshold=0.90):
    """这张头像是不是库里已经有了（返回已有文件名或 None）。"""
    import portraits
    items = portraits.load_library(_LIB[0])
    if not items:
        return None
    img = load(bust)
    if img is None:
        return None
    name, sc = portraits.identify(img[:, :, ::-1], items)
    return name if name and sc >= threshold else None


def main():
    dry = "--dry" in sys.argv
    side = "foe" if "--side" in sys.argv and sys.argv[sys.argv.index("--side") + 1] == "foe" else "self"
    lib_dir = P.FOE_DIR if side == "foe" else P.SELF_DIR
    _LIB[0] = lib_dir
    print("=== 标注 %s 库：%s ===" % (side, lib_dir))
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    os.makedirs(lib_dir, exist_ok=True)
    names = sorted(glob.glob(os.path.join(lib_dir, "_unknown_*_name.png")))
    if limit:
        names = names[:limit]
    ocr = ocr_cn.Ocr()
    ok = bad = 0
    for strip in names:
        stem = os.path.basename(strip)[:-len("_name.png")]
        bust = os.path.join(lib_dir, stem + ".png")
        if not os.path.exists(bust):
            continue
        # 先拿头像去比库：已经认识的直接丢掉，不用再 OCR（省 4/5 的时间）
        dup = _duplicate_of(bust)
        if dup:
            print("%-22s = %s (dup, skipped)" % (stem, dup))
            if not dry:
                try:
                    os.remove(bust); os.remove(strip)
                except OSError:
                    pass
            continue
        name, score, raw = read_name(strip, ocr)
        if not name or score < MIN_SCORE:
            bad += 1
            print("%-22s ?  (%.2f) %s" % (stem, score, raw[:60]))
            continue
        ok += 1
        print("%-22s -> %s (%.2f)" % (stem, name, score))
        if dry:
            continue
        # 同名已有模板就加编号，一张精灵可以有多张头像
        dst = os.path.join(lib_dir, name + ".png")
        n = 2
        while os.path.exists(dst):
            dst = os.path.join(lib_dir, "%s#%d.png" % (name, n))
            n += 1
        try:
            os.rename(bust, dst)
            sid = P.species_of(os.path.basename(dst), side)
            P.add_portrait(os.path.basename(dst), sid, "普通", side, "hud", score)
            os.remove(strip)
        except OSError as e:
            print("  rename failed: %s" % e)
    print("\nlabeled %d, skipped %d" % (ok, bad))


if __name__ == "__main__":
    main()
