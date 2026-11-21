# -*- coding: utf-8 -*-
"""ocr_batch.py —— 头像名字条的批量 OCR 预填。

为什么需要
----------
1122 张待标注头像，一张张手标不现实。名字条本身就是文字图片，
OCR 能直接读出名字，人工只需要确认或修正。

为什么用 easyocr 而不是项目里的 ocr_cn
--------------------------------------
ocr_cn 依赖 `rapidocr_onnxruntime`，**本机没装**（ModuleNotFoundError），
所以它一直返回空数组。easyocr 已装且准确率 0.91~0.98。
（D:\\Tools\\winocr.ps1 也能用，但 PowerShell 5.1 的管道会把中文
  输出成坏字节，绕文件写也解决不了，所以不用它。）

性能（实测，CPU）
-----------------
    3x 放大   6476 ms/张
    2x 放大   1979 ms/张
    1x 原图   1287 ms/张   <- 准确率不变，选这个
    1122 张约 24 分钟

用法
----
    python src/ocr_batch.py              # 跑完所有待标注
    python src/ocr_batch.py --limit 100  # 只跑 100 张
    python src/ocr_batch.py --status     # 看进度

结果写 logs/ocr_names.json：
    {"_unknown_011149": {"text": "卡拉波斯", "conf": 0.98}, ...}
控制台读这个文件预填输入框。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

import paths  # noqa: E402

OUT = os.path.join(str(paths.LOGS), "ocr_names.json")
DIRS = {"self": "portraits", "foe": "portraits_foe"}

_reader = None


def _engine():
    """惰性加载 easyocr。import 就要 40 秒，只在真要跑时付这个代价。"""
    global _reader
    if _reader is not None:
        return _reader
    try:
        import easyocr
    except ImportError:
        return None
    print("  加载 easyocr 模型（约 50 秒）…", flush=True)
    t = time.time()
    _reader = easyocr.Reader(["ch_sim", "en"], gpu=False, verbose=False)
    print("  就绪，用时 %.0f 秒" % (time.time() - t), flush=True)
    return _reader


def load_result():
    try:
        with open(OUT, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_result(d):
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=0)
    os.replace(tmp, OUT)


def pending(side):
    d = os.path.join(ROOT, "assets", DIRS[side])
    try:
        return sorted([f for f in os.listdir(d)
                       if f.startswith("_unknown_") and f.endswith(".png")
                       and "_name" not in f], reverse=True)
    except OSError:
        return []


def _clean_name(txt):
    """清洗 OCR 结果。返回干净的名字或 None。

    实测 127 个未匹配项里，大量是**数字/血量/耗时被误读**：
        '07417'  '0/366'  '4.2ms'  '55%'  '852/52'
    名字条截图有时会把旁边的血量数字一起框进来。
    这些不是精灵名，必须在 OCR 阶段就丢掉，不能污染标注候选。
    """
    import re
    if not txt:
        return None
    t = str(txt).replace(" ", "").strip()
    # 含数字、斜杠、百分号、小数点、括号的，一律不是名字
    if re.search(r"[0-9/%.()\[\]{}<>:：,，。·\-_=+*&^$#@!?~`'\"|\\]", t):
        return None
    # 纯符号
    if not re.search(r"[\u4e00-\u9fff]", t):
        return None
    # 长度：精灵名 2~8 个字（含括号变体会长一些，放宽到 14）
    if len(t) < 2 or len(t) > 14:
        return None
    return t


def ocr_one(reader, path):
    """识别一张名字条。返回 (text, conf) 或 (None, 0)。"""
    import cv2
    img = cv2.imread(path)
    if img is None:
        return None, 0.0
    h, w = img.shape[:2]
    # 去掉右侧性别图标（约占 28%）
    left = img[:, :int(w * 0.72)]
    try:
        res = reader.readtext(left, detail=1)
    except Exception:
        return None, 0.0
    if not res:
        return None, 0.0
    # 取置信度最高的那段**通过清洗的**
    cands = []
    for _box, txt, conf in res:
        clean = _clean_name(txt)
        if clean:
            cands.append((clean, float(conf)))
    if not cands:
        return None, 0.0
    best = max(cands, key=lambda x: x[1])
    return best[0], best[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--side", default="foe", choices=["foe", "self", "both"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--reclean", action="store_true",
                    help="只对已有结果重新清洗（不重跑 OCR）")
    args = ap.parse_args()

    done = load_result()

    if args.reclean:
        n = 0
        for stem, v in done.items():
            old = v.get("text")
            new = _clean_name(old)
            if new != old:
                v["text"] = new
                n += 1
        save_result(done)
        ok = sum(1 for v in done.values() if v.get("text"))
        print("重清洗完成：%d 条被修正，%d/%d 有效"
              % (n, ok, len(done)))
        return 0

    if args.status:
        total = 0
        for side in ("foe", "self"):
            p = pending(side)
            total += len(p)
            n = sum(1 for f in p if f[:-4] in done)
            print("  %-5s %4d 张待标注，已 OCR %d 张" % (side, len(p), n))
        print("  合计 %d 张待标注" % total)
        return 0

    sides = ["foe", "self"] if args.side == "both" else [args.side]
    todo = []
    for side in sides:
        for f in pending(side):
            stem = f[:-4]
            if stem in done:
                continue
            namefile = os.path.join(ROOT, "assets", DIRS[side],
                                    stem + "_name.png")
            if os.path.exists(namefile):
                todo.append((side, stem, namefile))

    if args.limit:
        todo = todo[:args.limit]

    if not todo:
        print("没有需要 OCR 的（都有结果了，或者没有名字条截图）")
        return 0

    print("待 OCR：%d 张" % len(todo))
    reader = _engine()
    if reader is None:
        print("easyocr 不可用：python -m pip install easyocr")
        return 2

    t0 = time.time()
    ok = 0
    for i, (side, stem, path) in enumerate(todo, 1):
        txt, conf = ocr_one(reader, path)
        done[stem] = {"text": txt, "conf": round(conf, 3), "side": side}
        if txt:
            ok += 1
        if i % 10 == 0 or i == len(todo):
            el = time.time() - t0
            eta = el / i * (len(todo) - i)
            print("  %4d/%d  识别成功 %d  已用 %.0fs  剩余约 %.0fs"
                  % (i, len(todo), ok, el, eta), flush=True)
            save_result(done)

    save_result(done)
    print()
    print("完成：%d 张，识别出 %d 张（%.0f%%）"
          % (len(todo), ok, 100.0 * ok / max(1, len(todo))))
    print("结果：%s" % OUT)
    print("打开控制台「头像标注」页，输入框会预填 OCR 结果")
    return 0


if __name__ == "__main__":
    sys.exit(main())
