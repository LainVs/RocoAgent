# -*- coding: utf-8 -*-
"""raw_get.py —— 从 raw.githubusercontent.com 下载文件（带重试）。

踩过的坑（2026-11-21）：
    raw.githubusercontent.com 走代理时偶发
    "SSL: UNEXPECTED_EOF_WHILE_READING"，同一文件重试就能成功。
    所以必须逐文件重试，不能一次失败就放弃。

用法：
    python scripts\\raw_get.py ColinHong10/NRC_AI data\\external\\nrc-ai README.md ROADMAP.md
"""
from __future__ import annotations

import os
import sys
import time
import urllib.request

PROXY = "http://127.0.0.1:7897"
BASE = "https://raw.githubusercontent.com/%s/main/%s"


def get(url, retries=6):
    last = None
    for i in range(retries):
        try:
            op = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
            req = urllib.request.Request(url, headers={"User-Agent": "rocoagent"})
            with op.open(req, timeout=180) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(2.0 * (i + 1))
    raise last


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if len(sys.argv) < 4:
        print(__doc__)
        return 1

    repo, outdir = sys.argv[1], sys.argv[2]
    files = sys.argv[3:]
    ok = bad = 0
    for f in files:
        dest = os.path.join(outdir, *f.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            data = get(BASE % (repo, f))
            with open(dest, "wb") as fh:
                fh.write(data)
            print("  + %-44s %7.1f KB" % (f, len(data) / 1024.0))
            ok += 1
        except Exception as e:
            print("  !! %-44s %s" % (f, e))
            bad += 1
    print()
    print("完成: %d 成功, %d 失败" % (ok, bad))
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
