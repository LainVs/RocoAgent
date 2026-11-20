# -*- coding: utf-8 -*-
"""import_roco_cal.py —— 从 roco-cal 源码目录导入资产到 data/external/。

用途：上游更新后重新同步，保证 data/external/ 可复现。

用法
----
    # 从已有的解压目录导入
    python scripts\\import_roco_cal.py --from "D:\\GameData\\洛克王国世界\\roco-cal-latest"

    # 或先下载再导入
    python scripts\\import_roco_cal.py --download
"""
from __future__ import annotations

import glob
import io
import json
import os
import shutil
import sys

ROOT = r"D:\Projects\RocoAgent"
DST = os.path.join(ROOT, "data", "external", "roco-cal")

PROXY = "http://127.0.0.1:7897"
TARBALL_API = "https://api.github.com/repos/180sans/roco-cal/tarball/main"

# 只拷这些，跳过 images（中文名 + 体积大）
WANT = {
    "src-tauri/resources/image-classifier/all_classes.onnx": "models/all_classes.onnx",
    "src-tauri/resources/image-classifier/all_classes.classes.json": "models/classes.json",
    "src-tauri/resources/ocr-models/number/model.onnx": "models/number.onnx",
    "src-tauri/resources/ocr-models/number/charset.txt": "models/charset.txt",
}


def download(dest_dir):
    import ssl
    import tarfile
    import urllib.request

    print("下载 %s ..." % TARBALL_API)
    op = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}),
        urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
    req = urllib.request.Request(TARBALL_API, headers={"User-Agent": "curl"})
    tgz = os.path.join(dest_dir, "roco-cal.tar.gz")
    with op.open(req, timeout=600) as r, open(tgz, "wb") as f:
        f.write(r.read())
    print("  %.1f MB" % (os.path.getsize(tgz) / 1024 / 1024))

    out = os.path.join(dest_dir, "roco-cal-latest")
    if os.path.isdir(out):
        shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out, exist_ok=True)

    n = 0
    with tarfile.open(tgz, "r:gz") as t:
        for m in t.getmembers():
            parts = m.name.split("/", 1)
            if len(parts) < 2 or not parts[1] or not m.isfile():
                continue
            rel = parts[1]
            # 只要资源目录里的数据，跳过图片
            if not rel.startswith("src-tauri/resources/"):
                continue
            if "/images" in rel:
                continue
            try:
                m.name = rel
                t.extract(m, out, filter="data")
                n += 1
            except Exception:
                pass
    print("  解压 %d 个文件 -> %s" % (n, out))
    return out


def copy_assets(src_root):
    os.makedirs(os.path.join(DST, "models"), exist_ok=True)
    os.makedirs(os.path.join(DST, "pets"), exist_ok=True)
    os.makedirs(os.path.join(DST, "traits"), exist_ok=True)
    os.makedirs(os.path.join(DST, "skills"), exist_ok=True)
    os.makedirs(os.path.join(DST, "engine"), exist_ok=True)

    done = 0
    for rel, dst_rel in WANT.items():
        s = os.path.join(src_root, rel)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(DST, dst_rel))
            done += 1
        else:
            print("  !! 缺少 %s" % rel)

    base = os.path.join(src_root, "src-tauri", "resources", "rocodatebase")
    # 精灵 / 特性 / 技能 / 引擎
    for sub, dst_sub in (("data/pets_w_skill_json", "pets"),
                         ("data/pets_trait_json", "traits"),
                         ("data/skills_database", "skills"),
                         ("core", "engine")):
        s = os.path.join(base, sub)
        if not os.path.isdir(s):
            print("  !! 缺少目录 %s" % sub)
            continue
        d = os.path.join(DST, dst_sub)
        for root, _dirs, files in os.walk(s):
            rel = os.path.relpath(root, s)
            target = d if rel == "." else os.path.join(d, rel)
            os.makedirs(target, exist_ok=True)
            for f in files:
                if f.endswith((".py", ".json")):
                    shutil.copy2(os.path.join(root, f),
                                 os.path.join(target, f))
                    done += 1
    # battle_effects 放进 traits 子目录
    be = os.path.join(base, "data", "battle_effects")
    if os.path.isdir(be):
        tgt = os.path.join(DST, "traits", "_battle_effects")
        os.makedirs(tgt, exist_ok=True)
        for f in os.listdir(be):
            if f.endswith(".json"):
                shutil.copy2(os.path.join(be, f), os.path.join(tgt, f))
                done += 1
    return done


def report():
    print("\n=== 导入结果 ===")
    for sub in ("models", "pets", "traits", "skills", "engine"):
        d = os.path.join(DST, sub)
        if not os.path.isdir(d):
            print("  %-10s 缺失" % sub)
            continue
        n = len(glob.glob(os.path.join(d, "**", "*"), recursive=True))
        n = len([p for p in glob.glob(os.path.join(d, "**", "*"),
                                      recursive=True) if os.path.isfile(p)])
        size = sum(os.path.getsize(p) for p in
                   glob.glob(os.path.join(d, "**", "*"), recursive=True)
                   if os.path.isfile(p))
        print("  %-10s %4d 个文件  %.1f MB" % (sub, n, size / 1024 / 1024))

    cls = os.path.join(DST, "models", "classes.json")
    if os.path.exists(cls):
        n = len(json.load(io.open(cls, encoding="utf-8")))
        print("  识别类别: %d" % n)


def main():
    a = sys.argv
    if "--download" in a:
        src = download(os.path.join(ROOT, "data", "external"))
    elif "--from" in a:
        src = a[a.index("--from") + 1]
    else:
        print(__doc__)
        return 2
    if not os.path.isdir(src):
        print("源目录不存在:", src)
        return 2
    n = copy_assets(src)
    print("拷贝 %d 个文件" % n)
    report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
