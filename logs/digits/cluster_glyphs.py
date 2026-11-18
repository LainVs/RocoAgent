"""cluster_glyphs.py - 把抓到的所有技能栏帧里的字形做无监督聚类

不依赖真值：只用「形状相似」分组，再看每组是不是同一个数字。
输出：
  cluster_montage.png  每个聚类一行，10 个样例
  clusters.json        聚类 -> 样例数 / 建议标签 / 成员来源

跑法： python cluster_glyphs.py
"""

from __future__ import annotations

import glob
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, r"D:\Tools")
import digitread as D

OUT = D.DIGITS
THRESH = 0.86          # 归入已有聚类的最低相关度
MERGE = 0.90           # 聚类之间合并的阈值


def gather(paths=None):
    """从所有可用帧里收集字形（默认 manifest + harvested）。"""
    if paths is None:
        paths = sorted(glob.glob(os.path.join(OUT, "frames", "*.png")))
        man = json.load(open(D.MANIFEST, encoding="utf-8")) if \
            os.path.exists(D.MANIFEST) else {"frames": []}
        for item in man["frames"]:
            if os.path.exists(item["path"]) and item["path"] not in paths:
                paths.append(item["path"])
    return gather_from(paths)


def gather_from(paths):
    items, bad = [], 0
    for p in paths:
        try:
            im = D.load(p)
        except Exception:
            bad += 1
            continue
        slots = D.skill_slots(im)
        good = [s for s in slots if s is not None]
        if len(good) < 3:
            bad += 1
            continue
        for i, pill in enumerate(slots):
            if pill is None or pill["kind"] != "num":
                continue
            for j, g in enumerate(D.extract_glyphs(im, pill)):
                items.append({"glyph": g["glyph"], "aspect": g["aspect"],
                              "raw": g["raw"],
                              "frame": os.path.basename(p),
                              "skill": i + 1, "pos": j})
    return items, len(paths), bad


def _unit(v):
    v = v.reshape(-1).astype(np.float32)
    v = v - v.mean()
    return v / (np.linalg.norm(v) + 1e-6)


def cluster(items):
    """贪心：与聚类质心相关度够高就并入，否则新建。质心按单位向量存。"""
    cents, members = [], []
    for idx, it in enumerate(items):
        vn = _unit(it["glyph"])
        best, best_c = -1, -1.0
        for ci, c in enumerate(cents):
            cc = float(c @ vn)
            if cc > best_c:
                best, best_c = ci, cc
        if best >= 0 and best_c >= THRESH:
            members[best].append(idx)
            m = np.stack([_unit(items[k]["glyph"]) for k in members[best]])
            cents[best] = _unit(m.mean(axis=0))
        else:
            cents.append(vn)
            members.append([idx])
    return cents, members


def merge(cents, members, items):
    """把彼此很像的聚类合并掉。"""
    changed = True
    while changed:
        changed = False
        n = len(cents)
        for a in range(n):
            for b in range(a + 1, n):
                c = float(cents[a] @ cents[b])
                if c >= MERGE:
                    members[a] = members[a] + members[b]
                    m = np.stack([_unit(items[k]["glyph"]) for k in members[a]])
                    cents[a] = _unit(m.mean(axis=0))
                    del cents[b], members[b]
                    changed = True
                    break
            if changed:
                break
    return cents, members


def suggest(items, members, cents, tpl):
    """用现有模板给每个聚类一个建议标签（多数投票）。"""
    out = []
    for ci, mem in enumerate(members):
        votes = {}
        for k in mem:
            lab, sc = D.classify(items[k]["glyph"], items[k]["aspect"], tpl)
            votes[lab] = votes.get(lab, 0) + 1
        best = max(votes.items(), key=lambda kv: kv[1])
        out.append({"cluster": ci, "n": len(mem), "label": int(best[0]),
                    "vote_frac": round(best[1] / float(len(mem)), 2),
                    "votes": {int(k): v for k, v in votes.items()}})
    return out


def render(items, members, info, path, per_row=12):
    order = sorted(range(len(members)), key=lambda c: -len(members[c]))
    CW, CH = 76, 88
    W = per_row * CW + 150
    H = len(order) * CH + 60
    img = Image.new("RGB", (W, H), (250, 250, 248))
    dr = ImageDraw.Draw(img)
    dr.text((10, 8), "unsupervised glyph clusters  (no labels used)", fill=(10, 10, 10))
    dr.text((10, 26), "left = cluster id / size / suggested digit", fill=(100, 100, 100))
    for r, ci in enumerate(order):
        y = 54 + r * CH
        if r % 2 == 0:
            dr.rectangle([0, y - 4, W, y + CH - 8], fill=(243, 246, 250))
        it = info[ci]
        dr.text((8, y + 18), "#%d" % ci, fill=(0, 0, 0))
        dr.text((8, y + 36), "n=%d" % it["n"], fill=(90, 90, 90))
        dr.text((8, y + 54), "sugg %d" % it["label"], fill=(20, 90, 160))
        for c, k in enumerate(members[ci][:per_row]):
            x = 150 + c * CW
            a = (np.clip(items[k]["raw"], 0, 1) * 255).astype(np.uint8)
            h, w = a.shape
            k2 = min(46.0 / max(1, h), 38.0 / max(1, w))
            tile = Image.fromarray(a).resize(
                (max(1, int(w * k2)), max(1, int(h * k2))), Image.LANCZOS)
            img.paste(tile.convert("RGB"), (x + 8, y + 2))
            dr.text((x + 2, y + CH - 24), items[k]["frame"][:9], fill=(130, 130, 130))
            dr.text((x + 2, y + CH - 12), "s%d.%d" % (items[k]["skill"],
                                                      items[k]["pos"]),
                    fill=(80, 130, 190))
    img.save(path)
    return img.size


def main():
    items, nframes, bad = gather()
    print("frames used: %d (skipped %d), glyphs: %d" % (nframes - bad, bad,
                                                       len(items)))
    cents, members = cluster(items)
    cents, members = merge(cents, members, items)
    print("clusters after merge:", len(members))

    tpl = D.load_templates()
    info = suggest(items, members, cents, tpl) if tpl else [
        {"cluster": i, "n": len(m), "label": -1, "vote_frac": 0,
         "votes": {}} for i, m in enumerate(members)]

    order = sorted(range(len(members)), key=lambda c: -len(members[c]))
    info_sorted = []
    for r, ci in enumerate(order):
        it = dict(info[ci])
        it["rank"] = r
        info_sorted.append(it)

    size = render(items, members, info, os.path.join(OUT, "cluster_montage.png"))
    print("montage:", size)

    json.dump({"n_frames": nframes - bad, "n_glyphs": len(items),
               "clusters": info_sorted},
              open(os.path.join(OUT, "clusters.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    for it in info_sorted:
        print("  #%-2d n=%-4d sugg=%d (%.0f%%) votes=%s"
              % (it["cluster"], it["n"], it["label"],
                 100 * it["vote_frac"], it["votes"]))


if __name__ == "__main__":
    main()
