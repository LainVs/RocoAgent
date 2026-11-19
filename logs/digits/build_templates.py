"""build_templates.py - 从已知截图里自动建字形模板 + 拼贴图

跑法：
    python <ROCO_TOOLS>\digitread.py build

字形来源有三种，全部来自同一套艺术字：
  1. 伤害胶囊里的绿色数字（已知数值，来自 manifest.json）
  2. 伤害胶囊里的红色数字
  3. 技能卡左上角「★N」徽标里的白色数字（同一字体，用来补 1/2/3）
外加 bar.png 的两处高清字形（175 / 27），手动给了框。

输出：
  templates.npz   模板（每个数字若干代表字形）
  montage.png     聚类拼贴图，供人眼标注
  <帧号>_<序号>.png  每个技能的胶囊裁图
  glyphs/         单字裁图
  report.json     统计
"""

from __future__ import annotations

import glob
import json
import os
import sys as _sys

import cv2
import numpy as np
from PIL import Image, ImageDraw

import digitread as D

try:                       # 交互式控制台用系统编码，重定向时转 UTF-8
    if not _sys.stdout.isatty():
        _sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# bar.png 是同一字体 2 倍放大的截图，手动给了框
BAR_MANUAL = [
    ("bar.png", (410, 298, 512, 342), "175"),
    ("bar.png", (383, 1018, 452, 1072), "27"),
]


# ---------------------------------------------------------------- 取样

def white_gate(sub):
    r, g, b = D.channels(sub)
    return (r > 170) & (g > 170) & (b > 150) & (np.abs(r - g) < 26) & \
           (np.abs(g - b) < 30)


def star_digits(im, box):
    """从「★N」徽标里切出白色数字。box 是整个徽标外接框。"""
    x0, y0, x1, y1 = box
    sub = im[y0:y1, x0:x1]
    a = D.alpha(sub, (255, 255, 255)) * white_gate(sub)
    out = []
    for (bx, by, bw, bh, ba) in D.segment(a.astype(np.float32)):
        g, ar = D.norm_glyph(a.astype(np.float32), (bx, by, bw, bh))
        if g is None:
            continue
        out.append({"glyph": g, "aspect": ar, "x": bx,
                    "raw": a[by:by + bh, bx:bx + bw].copy()})
    out.sort(key=lambda d: d["x"])
    return out


def find_star_badges(im, region=D.BAR):
    """找「★N」徽标（有黄色星星的纯色胶囊）。"""
    x0, y0, x1, y1 = region
    x1, y1 = min(x1, im.shape[1]), min(y1, im.shape[0])
    sub = im[y0:y1, x0:x1]
    d = np.abs(sub.astype(np.int16) - np.array(D.BG, np.int16)).max(axis=2)
    m = (d <= 8).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if not (30 <= w <= 220 and 16 <= h <= 90):
            continue
        if area < 0.32 * w * h:
            continue
        reg = sub[y:y + h, x:x + w]
        if int(D.gate_yellow(reg).sum()) < 40:
            continue
        out.append((x + x0, y + y0, x + x0 + w, y + y0 + h))
    out.sort(key=lambda b: b[1])
    return out


def load_manifest():
    if os.path.exists(D.MANIFEST):
        return json.load(open(D.MANIFEST, encoding="utf-8"))
    return {"frames": []}


# ---------------------------------------------------------------- 拼接

def add(samples, glyph, aspect, label, src, raw=None, kind="pill"):
    samples.append({"glyph": glyph, "aspect": aspect, "label": int(label),
                    "src": src, "raw": raw, "kind": kind})


def collect(man, verbose=True):
    samples = []

    for item in man["frames"]:
        p = item["path"]
        if not os.path.exists(p):
            if verbose:
                print("  skip missing", p)
            continue
        im = D.load(p)
        stem = os.path.splitext(os.path.basename(p))[0]
        slots = D.skill_slots(im)

        # --- 伤害胶囊
        for i, pill in enumerate(slots):
            want = item["values"][i]
            if pill is None or pill["kind"] != "num":
                continue
            gs = D.extract_glyphs(im, pill)
            s = "" if want is None else str(want)
            if len(gs) != len(s):
                if verbose:
                    print("  [warn] %s skill%d want=%s glyphs=%d"
                          % (stem, i + 1, s, len(gs)))
                continue
            for g, ch in zip(gs, s):
                add(samples, g["glyph"], g["aspect"], ch,
                    "%s_s%d" % (stem, i + 1), g.get("raw"))

        # --- 星标徽标（白色数字）
        stars = item.get("stars")
        badges = find_star_badges(im)
        if stars and len(badges) == 4:
            for i, b in enumerate(badges):
                gs = star_digits(im, b)
                s = str(stars[i])
                if len(gs) != len(s):
                    if verbose:
                        print("  [warn] %s star%d want=%s glyphs=%d"
                              % (stem, i + 1, s, len(gs)))
                    continue
                for g, ch in zip(gs, s):
                    add(samples, g["glyph"], g["aspect"], ch,
                        "%s_star%d" % (stem, i + 1), g.get("raw"),
                        kind="star")
        elif stars and verbose:
            print("  [warn] %s star badges found=%d" % (stem, len(badges)))

    # --- bar.png 手动框
    for name, box, text in BAR_MANUAL:
        p = os.path.join(D.LOG, name)
        if not os.path.exists(p):
            continue
        im = D.load(p)
        x0, y0, x1, y1 = box
        sub = im[y0:y1, x0:x1]
        m = D.text_mask(sub)
        gs = []
        for (bx, by, bw, bh, ba) in D.segment(m, min_h=20, max_h=52,
                                              min_w=8, max_w=44):
            g, ar = D.norm_glyph(m, (bx, by, bw, bh))
            if g is not None:
                gs.append({"glyph": g, "aspect": ar, "x": bx,
                           "raw": m[by:by + bh, bx:bx + bw].copy()})
        gs.sort(key=lambda d: d["x"])
        if len(gs) != len(text):
            if verbose:
                print("  [warn] %s manual want=%s glyphs=%d"
                      % (name, text, len(gs)))
            continue
        for g, ch in zip(gs, text):
            add(samples, g["glyph"], g["aspect"], ch,
                "%s_%s" % (name, text), g.get("raw"), kind="bar")

    return samples


# ---------------------------------------------------------------- 聚类

def build_protos(samples, max_per_digit=6, rot_deg=(8, -8), outlier=0.70,
                 floor=0.55):
    """每个数字留若干代表字形，并补几份轻微旋转版（星标里的数字是歪的）。

    原型不是挑「最典型」的，而是挑「最不重复」的（k-center）：
    同一张卡的倾斜角、不同卡的倾斜角都要有代表，否则换个角度就认不出。
    """
    protos, labels, aspects = [], [], []
    dropped = 0
    for lab in sorted({s["label"] for s in samples}):
        grp = [s for s in samples if s["label"] == lab]
        G = np.stack([s["glyph"] for s in grp]).reshape(len(grp), -1)
        mu = G.mean(axis=0)
        score = np.array([float(np.corrcoef(g, mu)[0, 1]) if G.shape[1] > 1
                          else 1.0 for g in G])
        if len(grp) >= 5:
            keep = score >= outlier
            if keep.sum() >= 3:
                dropped += int((~keep).sum())
                grp = [s for i, s in enumerate(grp) if keep[i]]
                score = score[keep]
        if not grp:
            continue
        # 第 1 个取最典型的，之后每次取「与已选原型最不像」的样本
        chosen = [int(np.argmax(score))]
        while len(chosen) < min(max_per_digit, len(grp)):
            best_i, best_sim = -1, 2.0
            for i in range(len(grp)):
                if i in chosen:
                    continue
                if score[i] < floor:      # 太怪的不要
                    continue
                sim = max(float(np.corrcoef(grp[i]["glyph"].reshape(-1),
                                            grp[j]["glyph"].reshape(-1))[0, 1])
                          for j in chosen)
                if sim < best_sim:
                    best_i, best_sim = i, sim
            if best_i < 0:
                break
            chosen.append(best_i)
        for j in chosen:
            g = grp[j]["glyph"]
            protos.append(g)
            labels.append(lab)
            aspects.append(grp[j]["aspect"])
            for deg in rot_deg:
                m = cv2.getRotationMatrix2D((D.GW / 2.0 - 0.5,
                                             D.GH / 2.0 - 0.5), deg, 1.0)
                r = cv2.warpAffine(g, m, (D.GW, D.GH),
                                   flags=cv2.INTER_LINEAR, borderValue=0)
                protos.append(np.clip(r, 0, 1))
                labels.append(lab)
                aspects.append(grp[j]["aspect"])
    if dropped:
        print("  dropped %d outlier glyphs" % dropped)
    return (np.stack(protos).astype(np.float32),
            np.array(labels, np.int32),
            np.array(aspects, np.float32))


def augment_nine(protos, labels, aspects):
    """没有真实 9，用 6 旋转 180 度合成一个，并标记为 synthetic。"""
    idx = np.where(labels == 6)[0]
    if len(idx) == 0:
        return protos, labels, aspects, 0
    src = protos[idx[0]][::-1, ::-1].copy()
    protos = np.concatenate([protos, src[None]], axis=0)
    labels = np.concatenate([labels, np.array([9], np.int32)])
    aspects = np.concatenate([aspects, aspects[idx[0]][None]])
    return protos, labels, aspects, 1


def cluster_stats(samples):
    """按标签统计，并做一次无监督聚类看能否分开。"""
    by_lab = {}
    for s in samples:
        by_lab.setdefault(s["label"], []).append(s["glyph"])
    lines = []
    for lab in sorted(by_lab):
        G = np.stack(by_lab[lab]).reshape(len(by_lab[lab]), -1)
        mu = G.mean(axis=0)
        cors = [float(np.corrcoef(g, mu)[0, 1]) if G.shape[1] > 1 else 1.0
                for g in G]
        lines.append((lab, len(by_lab[lab]), float(np.mean(cors)),
                      float(np.min(cors))))
    return lines


# ---------------------------------------------------------------- 拼贴

def montage(samples, path):
    """每个数字一横排，用原始分辨率字形，方便人眼标注。"""
    labs = sorted({s["label"] for s in samples})
    CW, CH = 86, 92
    cols = 8
    W = cols * CW + 130
    H = len(labs) * CH + 62
    img = Image.new("RGB", (W, H), (252, 252, 250))
    dr = ImageDraw.Draw(img)
    dr.text((12, 8), "glyph montage  (row = digit label)", fill=(15, 15, 15))
    dr.text((12, 26), "green/red digits: damage pill    white digits: star badge"
            "    right of each cell: aspect w/h",
            fill=(95, 95, 95))
    for r, lab in enumerate(labs):
        grp = [s for s in samples if s["label"] == lab]
        y = 58 + r * CH
        if r % 2 == 0:
            dr.rectangle([0, y - 4, W, y + CH - 8], fill=(244, 246, 248))
        dr.text((12, y + 24), "digit %d" % lab, fill=(0, 0, 0))
        dr.text((12, y + 42), "n = %d" % len(grp), fill=(110, 110, 110))
        for c, s in enumerate(grp[:cols]):
            x = 130 + c * CW
            raw = s.get("raw")
            if raw is None:
                raw = s["glyph"]
            a = (np.clip(raw, 0, 1) * 255).astype(np.uint8)
            h, w = a.shape
            k = min(50.0 / max(1, h), 40.0 / max(1, w))
            tile = Image.fromarray(a).resize(
                (max(1, int(w * k)), max(1, int(h * k))), Image.LANCZOS)
            img.paste(tile.convert("RGB"), (x + 10, y + 4))
            dr.rectangle([x + 6, y, x + 6 + CW - 14, y + CH - 16],
                         outline=(206, 210, 214))
            dr.text((x + 8, y + CH - 26), "%s" % s["src"][-14:],
                    fill=(120, 120, 120))
            dr.text((x + 8, y + CH - 14), "%.2f" % s["aspect"],
                    fill=(80, 130, 190))
    img.save(path)
    return img.size


# ---------------------------------------------------------------- main

def crossval(verbose=True):
    """真正的留帧验证：用其余帧建模板，去读被留出的那一帧。

    这才对应实战（模板来自过去，读的是新画面）。
    """
    man = load_manifest()
    frames = [f for f in man["frames"] if os.path.exists(f["path"])]
    if len(frames) < 2:
        print("need >=2 frames for crossval")
        return
    total = exact = 0
    rank_ok = rank_n = 0
    for held in frames:
        rest = [f for f in frames if f is not held]
        sub_man = {"frames": rest}
        samples = collect(sub_man, verbose=False)
        # bar.png 是可复用的官方 UI 字形，不算被留出的帧
        samples += _bar_samples()
        protos, labels, aspects = build_protos(samples)
        protos, labels, aspects, _ = augment_nine(protos, labels, aspects)
        tpl = {"protos": protos, "labels": labels, "aspect": aspects}
        res = D.read_image(held["path"], tpl)
        got = [s["value"] if s["kind"] == "num" else None
               for s in res["skills"]]
        want = held["values"]
        stem = os.path.basename(held["path"])
        if verbose:
            print("  %s" % stem)
            print("    truth :", want)
            print("    read  :", got)
            print("    texts :", [s["text"] for s in res["skills"]])
            print("    order :", res["order"])
        for a, b in zip(got, want):
            total += 1
            exact += (a == b)
        def key(v):
            return -1 if v is None else v
        want_order = sorted(range(1, 5), key=lambda i: key(want[i - 1]),
                            reverse=True)
        rank_n += 1
        rank_ok += (res["order"] == want_order)
        if res["order"] != want_order:
            print("    RANK WRONG, want", want_order)
    print("  numbers exact: %d/%d = %.1f%%" % (exact, total,
                                                100.0 * exact / total))
    print("  ranking exact: %d/%d = %.1f%%" % (rank_ok, rank_n,
                                                100.0 * rank_ok / rank_n))


def _bar_samples():
    out = []
    for name, box, text in BAR_MANUAL:
        p = os.path.join(D.LOG, name)
        if not os.path.exists(p):
            continue
        im = D.load(p)
        x0, y0, x1, y1 = box
        sub = im[y0:y1, x0:x1]
        m = D.text_mask(sub)
        gs = []
        for (bx, by, bw, bh, ba) in D.segment(m, min_h=20, max_h=52,
                                              min_w=8, max_w=44):
            g, ar = D.norm_glyph(m, (bx, by, bw, bh))
            if g is not None:
                gs.append({"glyph": g, "aspect": ar, "x": bx,
                           "raw": m[by:by + bh, bx:bx + bw].copy()})
        gs.sort(key=lambda d: d["x"])
        if len(gs) != len(text):
            continue
        for g, ch in zip(gs, text):
            add(out, g["glyph"], g["aspect"], ch, "%s_%s" % (name, text),
                g.get("raw"), kind="bar")
    return out


def auto_expand(seed_tpl, harvested, min_vote=0.92):
    """用法则外的帧扩样本：聚类 + 种子模板投票，两票都一致才收。

    这不是给人看的标注，而是「同一个字形的所有出现都长得一样」这个
    前提的自举。聚类和模板是两个独立信号，互相验证。
    """
    import cluster_glyphs as CG

    items, nf, bad = CG.gather_from(harvested)
    if not items:
        return [], 0, 0
    cents, members = CG.cluster(items)
    cents, members = CG.merge(cents, members, items)

    out, accepted, rejected = [], 0, 0
    for mem in members:
        if len(mem) < 3:
            rejected += 1
            continue
        votes = {}
        for k in mem:
            lab, sc = D.classify(items[k]["glyph"], items[k]["aspect"], seed_tpl)
            votes[lab] = votes.get(lab, 0) + 1
        lab, cnt = max(votes.items(), key=lambda kv: kv[1])
        frac = cnt / float(len(mem))
        if frac < min_vote:
            rejected += 1
            continue
        accepted += 1
        for k in mem:
            it = items[k]
            add(out, it["glyph"], it["aspect"], lab,
                "%s_s%d.%d" % (it["frame"][:9], it["skill"], it["pos"]),
                it["raw"], kind="auto")
    return out, accepted, rejected


def main():
    os.makedirs(D.DIGITS, exist_ok=True)
    os.makedirs(os.path.join(D.DIGITS, "glyphs"), exist_ok=True)
    man = load_manifest()
    if not man["frames"]:
        print("manifest empty:", D.MANIFEST)
        return 2

    print("collecting glyphs ...")
    samples = collect(man)
    print("total glyphs:", len(samples))
    if not samples:
        print("nothing collected")
        return 1

    # 裁图：每个技能的胶囊
    def crop_frame(p, outdir):
        im = D.load(p)
        stem = os.path.splitext(os.path.basename(p))[0]
        n = 0
        for i, pill in enumerate(D.skill_slots(im)):
            if pill is None:
                continue
            x0, y0, x1, y1 = pill["box"]
            pad = 6
            crop = im[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad]
            Image.fromarray(crop).resize(
                (crop.shape[1] * 4, crop.shape[0] * 4), Image.LANCZOS
            ).save(os.path.join(outdir, "%s_%d.png" % (stem, i + 1)))
            for j, g in enumerate(D.extract_glyphs(im, pill)):
                gg = (g["glyph"] * 255).astype(np.uint8)
                Image.fromarray(gg).resize((D.GW * 6, D.GH * 6), Image.NEAREST
                                           ).save(os.path.join(
                    D.DIGITS, "glyphs",
                    "%s_%d_%d.png" % (stem, i + 1, j + 1)))
            n += 1
        return n

    for item in man["frames"]:
        if os.path.exists(item["path"]):
            crop_frame(item["path"], D.DIGITS)

    # watch 抓到的战斗帧：裁图 + 用聚类自举扩充字形样本
    man_paths = {os.path.normcase(f["path"]) for f in man["frames"]}
    harvested = [p for p in sorted(glob.glob(
        os.path.join(D.DIGITS, "frames", "*.png")))
        if os.path.normcase(p) not in man_paths]
    crops_dir = os.path.join(D.DIGITS, "crops")
    os.makedirs(crops_dir, exist_ok=True)
    extra = 0
    for p in harvested:
        extra += crop_frame(p, crops_dir)
    if harvested:
        print("harvested frames: %d (pills cropped: %d)"
              % (len(harvested), extra))

    if harvested:
        sp, sl, sa = build_protos(samples)
        seed = {"protos": sp, "labels": sl, "aspect": sa}
        more, acc, rej = auto_expand(seed, harvested)
        print("auto-labeled: %d glyphs from %d clusters (rejected %d)"
              % (len(more), acc, rej))
        samples += more

    protos, labels, aspects = build_protos(samples)
    protos, labels, aspects, n9 = augment_nine(protos, labels, aspects)
    np.savez_compressed(D.TEMPLATES, protos=protos, labels=labels,
                        aspect=aspects)
    print("templates:", protos.shape, "digits:", sorted(set(labels.tolist())),
          "synthetic 9:", n9)

    size = montage(samples, os.path.join(D.DIGITS, "montage.png"))
    print("montage:", size)

    stats = cluster_stats(samples)
    report = {
        "n_glyphs": len(samples),
        "n_templates": int(len(labels)),
        "digits_present": sorted(set(labels.tolist())),
        "synthetic_9": n9,
        "per_digit": [{"digit": d, "n": n, "mean_corr": round(m, 3),
                       "min_corr": round(mn, 3)} for d, n, m, mn in stats],
        "sources": sorted({s["src"] for s in samples}),
    }
    json.dump(report, open(os.path.join(D.DIGITS, "report.json"), "w",
                           encoding="utf-8"), ensure_ascii=False, indent=2)
    for d, n, m, mn in stats:
        print("  digit %d: n=%-2d mean_corr=%.3f min_corr=%.3f" % (d, n, m, mn))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
