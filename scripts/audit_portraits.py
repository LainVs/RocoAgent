# -*- coding: utf-8 -*-
"""audit_portraits.py —— 检查头像库的标注是否正确。

为什么需要
----------
头像库是我们识别对面的地基，但它的标注有几个容易出错的地方：

1. **文件名不等于种族名**。`wolf.png` 是恶魔狼、`音速犬#4.png` 是音速犬的
   第 4 个样本。映射关系在 `portrait` 表的 `species_id` 里，文件名只是键。
2. **同一个 species_id 挂了一堆模板**，其中可能有标错的
   （把 A 精灵的头像标成了 B）。
3. **有些文件的 species_id 已经失效**（数据库清过、精灵被合并）。
4. **模板本身可能是脏图**（换人动画中途、被 UI 挡住、纯背景）。

这个脚本做 4 件事：

    --stats     看整体分布（每个 side/source 各多少、每只精灵几张）
    --orphans   找失效/缺文件/缺 species_id 的记录
    --outliers  找"和同族模板不像、反而更像别的族"的**疑似标错**
    --sheet     生成一张拼图，每格标注「文件名 -> 种族名 -> 相似度」

跑法：
    python scripts\\audit_portraits.py --stats
    python scripts\\audit_portraits.py --orphans
    python scripts\\audit_portraits.py --outliers
    python scripts\\audit_portraits.py --sheet
"""
from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import cv2                      # noqa: E402
import paths                    # noqa: E402
import portrait_db as P         # noqa: E402

SIDES = ("foe", "self")


# ------------------------------------------------------------------ 基础
def _conn():
    import sqlite3
    db = sqlite3.connect(str(paths.DB))
    db.row_factory = sqlite3.Row
    return db


def _dir(side):
    return os.path.join(ROOT, "assets",
                        "portraits_foe" if side == "foe" else "portraits")


def load_items(side="foe"):
    """读所有标注，返回 [(file, species_id, species_name, gray_img)]。

    读不到的（文件缺失 / 解码失败）单独计数。
    """
    db = _conn()
    rows = list(db.execute(
        "SELECT p.file, p.species_id, s.name AS sname "
        "FROM portrait p LEFT JOIN species s ON s.id = p.species_id "
        "WHERE p.side = ?", (side,)))
    db.close()

    base = _dir(side)
    out, missing = [], []
    for r in rows:
        path = os.path.join(base, r["file"])
        if not os.path.exists(path):
            missing.append((r["file"], r["species_id"], r["sname"]))
            continue
        buf = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR) if buf.size else None
        if img is None:
            missing.append((r["file"], r["species_id"], r["sname"]))
            continue
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
        out.append((r["file"], r["species_id"], r["sname"], g))
    return out, missing


def sim(a, b):
    """两张头像的相似度（NCC，-1~1）。"""
    bb = cv2.resize(b, (a.shape[1], a.shape[0])).astype(np.float32)
    try:
        return float(cv2.matchTemplate(a, bb, cv2.TM_CCOEFF_NORMED).max())
    except Exception:
        return -1.0


# ------------------------------------------------------------------ 脏文件
def cmd_junk(side="foe"):
    """找目录里**不是头像**的图片。

    为什么需要（2026-11-21 实测）
    ---------------------------
    `assets/portraits_foe/` 里混进了 80 个 **210x55 的名字条**截图
    （能直接看到「蜘蛛侠」「恶魔红钻」这些文字）—— 是早期
    `robot.py` 存未知头像时顺手存的 `_unknown_xxx_name.png`。

    好消息：它们**没有登记在 portrait 表里**，所以 `load_library()`
    不会加载，不影响匹配。但会污染统计、也会让人以为头像库很大。

    判据：
      · 宽高比 > 1.5 或 < 0.5（头像基本是方的）
      · 文件名以 `_name` 结尾
      · 表里没有对应记录
    """
    print("=== 目录里的脏文件（不是头像）===")
    db = _conn()
    total = 0
    for s in ([side] if side else SIDES):
        base = _dir(s)
        if not os.path.isdir(base):
            continue
        reg = {r["file"] for r in db.execute(
            "SELECT file FROM portrait WHERE side=?", (s,))}
        junk, no_rec, ok = [], [], 0
        for f in sorted(os.listdir(base)):
            if not f.lower().endswith(".png"):
                continue
            path = os.path.join(base, f)
            buf = np.fromfile(path, dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR) if buf.size else None
            if img is None:
                junk.append((f, "解码失败", ""))
                continue
            h, w = img.shape[:2]
            ar = w / max(1, h)
            reason = None
            if f.endswith("_name.png"):
                reason = "名字条"
            elif ar > 1.5 or ar < 0.5:
                reason = "比例 %.2f" % ar
            if reason:
                junk.append((f, reason, "%dx%d" % (w, h)))
            elif f not in reg:
                no_rec.append(f)
            else:
                ok += 1

        print("\n[%s] 目录 %s" % (s, os.path.basename(base)))
        print("   已登记且正常: %d" % ok)
        print("   脏文件: %d" % len(junk))
        for f, reason, size in junk[:12]:
            print("      %-28s %-10s %s" % (f, reason, size))
        if len(junk) > 12:
            print("      ... 还有 %d 个" % (len(junk) - 12))
        if no_rec:
            print("   **未登记**的文件: %d（可能是漏标）" % len(no_rec))
            for f in no_rec[:10]:
                print("      %s" % f)
            if len(no_rec) > 10:
                print("      ... 还有 %d 个" % (len(no_rec) - 10))
        total += len(junk)
    db.close()
    print("\n脏文件合计 %d（不影响匹配，因为没登记在表里；可安全删除）" % total)
    return 0


# ------------------------------------------------------------------ 统计
def cmd_stats():
    print("=== 头像库统计 ===")
    for side in SIDES:
        items, missing = load_items(side)
        db = _conn()
        rows = list(db.execute(
            "SELECT source, COUNT(*) n FROM portrait WHERE side=? "
            "GROUP BY source ORDER BY n DESC", (side,)))
        total = sum(r["n"] for r in rows)
        print("\n[%s]  总记录 %d，可读 %d，缺文件 %d"
              % (side, total, len(items), len(missing)))
        for r in rows:
            print("   source=%-8s %d" % (r["source"], r["n"]))

        # 每只精灵几张
        per = Counter(sid for _f, sid, _n, _g in items)
        names = {sid: nm for _f, sid, nm, _g in items}
        print("   覆盖精灵 %d 只" % len(per))
        top = per.most_common(6)
        if top:
            print("   样本最多的几只：%s"
                  % "、".join("%s(%d)" % (names.get(s, s), n) for s, n in top))
        only1 = sum(1 for _s, n in per.items() if n == 1)
        print("   只有 1 张模板的精灵：%d 只（占 %.0f%%）"
              % (only1, 100.0 * only1 / max(1, len(per))))
        db.close()
    return 0


# ------------------------------------------------------------------ 孤儿
def cmd_orphans():
    """找失效记录：文件缺失 / species_id 为空 / species_id 查不到。"""
    print("=== 失效记录 ===")
    bad = 0
    for side in SIDES:
        items, missing = load_items(side)
        print("\n[%s]" % side)
        for f, sid, nm in missing:
            print("   缺文件      %-24s species_id=%s" % (f, sid))
            bad += 1
        db = _conn()
        rows = list(db.execute(
            "SELECT p.file, p.species_id, s.id AS ok FROM portrait p "
            "LEFT JOIN species s ON s.id = p.species_id "
            "WHERE p.side=? AND (p.species_id IS NULL OR s.id IS NULL)",
            (side,)))
        db.close()
        for r in rows:
            print("   坏 species  %-24s species_id=%s"
                  % (r["file"], r["species_id"]))
            bad += 1
        if not missing and not rows:
            print("   无")
    print("\n合计 %d 条需要修" % bad)
    if bad:
        print("修法：重新标注后调 portrait_db.add_portrait()，"
              "或直接从 portrait 表删掉这条。")
    return 0


# ------------------------------------------------------------------ 疑似标错
def cmd_outliers(min_sim=0.55, ratio=0.08):
    """找疑似标错的模板。

    判据：这张图和**同族其他模板**的相似度，明显低于它和**别族**的最高相似度。
    换句话说：它更像另一只精灵，却被标成了这一只。

    单张模板的精灵跳过（没有同族样本可比）。
    """
    print("=== 疑似标错（更像别族）===")
    print("判据：同族最高相似度 < 异族最高相似度 - %.2f，且同族 < %.2f"
          % (ratio, min_sim))
    total_bad = 0
    for side in SIDES:
        items, _m = load_items(side)
        if not items:
            continue
        # 只比同 side
        per = defaultdict(list)
        for i, (f, sid, nm, g) in enumerate(items):
            per[sid].append(i)

        print("\n[%s] 样本 %d" % (side, len(items)))
        found = 0
        for i, (f, sid, nm, g) in enumerate(items):
            mates = [j for j in per[sid] if j != i]
            if not mates:
                continue
            same = max(sim(g, items[j][3]) for j in mates)
            if same >= min_sim:
                continue
            # 找异族最高
            best_other = (-9.0, None, None)
            for j, (f2, sid2, nm2, g2) in enumerate(items):
                if sid2 == sid:
                    continue
                s = sim(g, g2)
                if s > best_other[0]:
                    best_other = (s, sid2, nm2)
            if best_other[0] - same >= ratio:
                found += 1
                print("   %-22s 标为 %-10s 同族最高 %.2f | "
                      "更像 %-10s (%.2f)"
                      % (f, nm or sid, same, best_other[2] or best_other[1],
                         best_other[0]))
        print("   疑似 %d 条" % found)
        total_bad += found
    print("\n合计 %d 条疑似标错" % total_bad)
    if total_bad:
        print("注意：形态差异大的同族精灵（如卡瓦重 4 形态）也会被列出来，")
        print("      要人工看图确认，不要无脑改。")
    return 0


# ------------------------------------------------------------------ 拼图
def cmd_sheet(side="foe", limit=200):
    """生成一张带标注的拼图，人工快速过一遍。"""
    items, _m = load_items(side)
    if not items:
        print("没有样本")
        return 1
    items = items[:limit]

    cell, cols = 96, 10
    rows = (len(items) + cols - 1) // cols
    sheet = np.full((rows * cell, cols * cell, 3), 28, dtype=np.uint8)

    per = defaultdict(list)
    for i, (f, sid, nm, g) in enumerate(items):
        per[sid].append(i)

    for i, (f, sid, nm, g) in enumerate(items):
        r, c = divmod(i, cols)
        big = cv2.resize(g, (cell - 6, cell - 18))
        big = cv2.cvtColor(big.astype(np.uint8), cv2.COLOR_GRAY2BGR)
        sheet[r * cell + 2:r * cell + cell - 16,
              c * cell + 3:c * cell + cell - 3] = big
        # 同族最高相似度（判断这张像不像自己族）
        mates = [j for j in per[sid] if j != i]
        s = max((sim(g, items[j][3]) for j in mates), default=-1.0)
        color = (80, 220, 80) if s >= 0.75 else (80, 180, 255) if s >= 0.55 \
            else (80, 80, 240)
        label = "%s %.2f" % ((nm or "?")[:5], s) if mates else (nm or "?")[:8]
        cv2.putText(sheet, label, (c * cell + 3, r * cell + cell - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.34, color, 1)

    out = os.path.join(ROOT, "logs", "probe", "portrait_sheet_%s.png" % side)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cv2.imwrite(out, sheet)
    print("已生成 %s（%d 个模板，%d 行）" % (out, len(items), rows))
    print("颜色：绿=和同族很像(>=0.75)  黄=一般(0.55~)  红=不像(<-0.55)")
    print("红的那些要重点看：可能是标错，也可能只是形态差异大。")
    return 0


# ------------------------------------------------------------------ main
def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if "--stats" in argv:
        return cmd_stats()
    if "--orphans" in argv:
        return cmd_orphans()
    if "--outliers" in argv:
        return cmd_outliers()
    if "--junk" in argv:
        side = "self" if "--self" in argv else "foe"
        return cmd_junk(side)
    if "--sheet" in argv:
        side = "self" if "--self" in argv else "foe"
        return cmd_sheet(side)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
