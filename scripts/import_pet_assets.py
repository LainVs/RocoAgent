# -*- coding: utf-8 -*-
"""把外部项目里的精灵图形资源导入本项目（供对战窗口 / 阵容卡显示用）。

来源
----
`洛克PVP助手` 的 `app/wwwroot/` 下有三份现成资源，都按**图鉴 id** 命名，
和本项目 `species.id` 是同一套编号（已核对：冰钻布鲁斯 = 3259 存在）：

    roco-pet-avatars-id/<id>.png          128x128 方图带 alpha（头像一个）
    simulation-space-assets/pets/art/<id>.webp  1024x1024 官方立绘带 alpha
    simulation-space-assets/battle-background.png  战斗背景 960x560

为什么不全量照搬
----------------
立绘一张 250 KB，957 张 246 MB —— 直接入库太重。对战窗口里最多显示到
~260px 高，所以：

    · 头像照搬（128px 本来就小，17 MB）
    · 立绘**裁掉透明边**后缩到最长边 420px，转 webp q80（实测 ~10 MB）

裁透明边很关键：官方立绘四周大量留白，不裁的话放进去就是一个小点。

用法
----
    & $py scripts/import_pet_assets.py
    & $py scripts/import_pet_assets.py --src "D:/别的路径/wwwroot"
    & $py scripts/import_pet_assets.py --dry        # 只统计不写文件

源目录不存在时**直接跳过并说明**（换机器没有那个项目也能正常跑，
只是没有图）。
"""
import argparse
import io
import os
import sqlite3
import sys
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import paths  # noqa: E402

DEFAULT_SRC = (r"C:\Users\Lenovo\Downloads\洛克PVP助手\洛克PVP助手"
               r"\app\wwwroot")

AVATAR_OUT = os.path.join(ROOT, "assets", "pet_avatars")
ART_OUT = os.path.join(ROOT, "assets", "pet_art")
BG_OUT = os.path.join(ROOT, "assets", "battle")

ART_MAX = 420          # 立绘最长边（px）
WEBP_Q = 80
ALPHA_T = 8            # 低于这个 alpha 视为透明（裁边阈）


def species_ids(db_path):
    db = sqlite3.connect(db_path)
    try:
        rows = db.execute("SELECT id, name FROM species "
                          "WHERE name IS NOT NULL AND id > 0").fetchall()
    finally:
        db.close()
    return {int(r[0]): r[1] for r in rows}


def read_img(path):
    im = cv2.imdecode(np.fromfile(path, dtype=np.uint8),
                      cv2.IMREAD_UNCHANGED)
    return im


def trim_alpha(im, pad=6):
    """按 alpha 通道裁掉四周边框，留 pad 像素。没有 alpha 就原样返回。"""
    if im.ndim != 3 or im.shape[2] != 4:
        return im
    a = im[:, :, 3]
    ys, xs = np.nonzero(a > ALPHA_T)
    if len(xs) == 0:
        return im
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    h, w = a.shape
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(w, x1 + pad)
    y1 = min(h, y1 + pad)
    return im[y0:y1, x0:x1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC,
                    help="洛克PVP助手 的 wwwroot 目录")
    ap.add_argument("--dry", action="store_true", help="只统计不写文件")
    args = ap.parse_args()

    av_src = os.path.join(args.src, "roco-pet-avatars-id")
    art_src = os.path.join(args.src, "simulation-space-assets", "pets", "art")
    bg_src = os.path.join(args.src, "simulation-space-assets",
                          "battle-background.png")

    ids = species_ids(str(paths.DB))
    print("图鉴里有 %d 个 id" % len(ids))
    if not os.path.isdir(av_src) and not os.path.isdir(art_src):
        print("跳过：源目录不存在（换机器没有那个项目也能跑，只是没有图）")
        print("  找的是: %s" % av_src)
        return 0

    for d in (AVATAR_OUT, ART_OUT, BG_OUT):
        if not args.dry:
            os.makedirs(d, exist_ok=True)

    # ---------------- 头像 ----------------
    n_av = 0
    bytes_av = 0
    if os.path.isdir(av_src):
        have = os.listdir(av_src)
        for fn in have:
            stem = os.path.splitext(fn)[0]
            if not stem.isdigit() or int(stem) not in ids:
                continue
            src = os.path.join(av_src, fn)
            dst = os.path.join(AVATAR_OUT, "%s.png" % stem)
            bytes_av += os.path.getsize(src)
            n_av += 1
            if not args.dry and not os.path.exists(dst):
                with io.open(src, "rb") as fr, io.open(dst, "wb") as fw:
                    fw.write(fr.read())
    print("头像: 复制 %d 个（%.1f MB）" % (n_av, bytes_av / 1048576.0))

    # ---------------- 立绘 ----------------
    n_art = 0
    bytes_art = 0
    if os.path.isdir(art_src):
        for fn in os.listdir(art_src):
            stem = os.path.splitext(fn)[0]
            if not stem.isdigit() or int(stem) not in ids:
                continue
            dst = os.path.join(ART_OUT, "%s.webp" % stem)
            if args.dry:
                bytes_art += os.path.getsize(os.path.join(art_src, fn)) // 4
                n_art += 1
                continue
            if os.path.exists(dst):
                bytes_art += os.path.getsize(dst)
                n_art += 1
                continue
            im = read_img(os.path.join(art_src, fn))
            if im is None:
                continue
            im = trim_alpha(im)
            h, w = im.shape[:2]
            scale = ART_MAX / float(max(h, w))
            if scale < 1.0:
                im = cv2.resize(im, (max(1, int(w * scale)),
                                     max(1, int(h * scale))),
                                interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".webp", im,
                                   [cv2.IMWRITE_WEBP_QUALITY, WEBP_Q])
            if not ok:
                ok, buf = cv2.imencode(".png", im)
                dst = dst[:-5] + ".png"
            if ok:
                buf.tofile(dst)
                bytes_art += os.path.getsize(dst)
                n_art += 1
    print("立绘: 生成 %d 个（%.1f MB，最长边 %dpx）"
          % (n_art, bytes_art / 1048576.0, ART_MAX))

    # ---------------- 战斗背景 ----------------
    if os.path.exists(bg_src):
        dst = os.path.join(BG_OUT, "bg.png")
        if not args.dry and not os.path.exists(dst):
            with io.open(bg_src, "rb") as fr, io.open(dst, "wb") as fw:
                fw.write(fr.read())
        print("战斗背景: %s（%d KB）"
              % (os.path.basename(dst), os.path.getsize(bg_src) // 1024))

    if not args.dry:
        note = os.path.join(ROOT, "assets", "pet_art", "VERSION.md")
        with io.open(note, "w", encoding="utf-8") as fh:
            fh.write(
                "# 精灵图形资源（第三方）\n\n"
                "- 来源：洛克PVP助手 `app/wwwroot/`\n"
                "  - `roco-pet-avatars-id/<id>.png` -> `assets/pet_avatars/`\n"
                "  - `simulation-space-assets/pets/art/<id>.webp` -> "
                "`assets/pet_art/`（裁透明边 + 最长边 %dpx + webp q%d）\n"
                "  - `simulation-space-assets/battle-background.png` -> "
                "`assets/battle/bg.png`\n"
                "- 生成时间：%s\n"
                "- 只有**图片**是从那边取的，不含任何代码；本项目代码仍全部"
                "自己实现。\n"
                "- 该资源属于《洛克王国》官方美术，仅用于本地识别/调试展示。\n"
                % (ART_MAX, WEBP_Q,
                   time.strftime("%Y-%m-%d %H:%M:%S")))
        print("说明写入 %s" % note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
