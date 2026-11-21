# -*- coding: utf-8 -*-
"""gh_fetch.py —— 走代理从 GitHub 拉取指定文件/目录。

为什么不用 git clone
-------------------
实测（2026-11-21）：
    git clone --depth 1   -> 超时 / early EOF
    codeload tar.gz       -> 代理下 "EOF 0 bytes"
代理对 github.com 的正文稳定，但对 codeload 不稳。

本脚本走 api.github.com 的 contents 接口逐文件下载，
每个文件独立重试，断了只重下那一个。

用法：
    python scripts\\gh_fetch.py <owner/repo> <本地目录> [路径1 路径2 ...]
    python scripts\\gh_fetch.py ColinHong10/NRC_AI data/external/nrc-ai src data
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

PROXY = "http://127.0.0.1:7897"
API = "https://api.github.com"

# 只下这些扩展名（数据 + 源码 + 文档）
KEEP_EXT = {".py", ".json", ".md", ".txt", ".html", ".js", ".css",
            ".db", ".sqlite", ".sqlite3", ".yaml", ".yml", ".toml",
            ".csv", ".xlsx", ".png", ".jpg"}
# 跳过的目录
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}


def _req(url, retries=4, raw=False):
    last = None
    for i in range(retries):
        try:
            headers = {"User-Agent": "rocoagent-fetch"}
            if not raw:
                headers["Accept"] = "application/vnd.github+json"
            req = urllib.request.Request(url, headers=headers)
            op = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
            with op.open(req, timeout=180) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def list_dir(repo, path=""):
    url = "%s/repos/%s/contents/%s" % (API, repo, path)
    data = json.loads(_req(url).decode("utf-8"))
    return data if isinstance(data, list) else [data]


def fetch_tree(repo, path, outdir, stats):
    try:
        items = list_dir(repo, path)
    except Exception as e:
        print("  !! 列目录失败 %s: %s" % (path or "/", e))
        return
    for it in items:
        name = it.get("name")
        typ = it.get("type")
        if name in SKIP_DIRS:
            continue
        rel = (path + "/" + name) if path else name
        if typ == "dir":
            fetch_tree(repo, rel, outdir, stats)
            continue
        if typ != "file":
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext and ext not in KEEP_EXT:
            stats["skipped"] += 1
            continue
        dest = os.path.join(outdir, *rel.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            if it.get("size", 0) > 1000000:
                # GitHub contents API 对 >1MB 的文件不返回 base64 content，
                # 必须走 raw.githubusercontent.com 单独下。
                dl = it.get("download_url")
                if not dl:
                    stats["skipped"] += 1
                    print("  -- %-56s 大文件但无 download_url" % rel)
                    continue
                content = _req(dl, raw=True)
            else:
                body = _req(it["url"])
                meta = json.loads(body.decode("utf-8"))
                content = base64.b64decode(meta["content"])
            with open(dest, "wb") as f:
                f.write(content)
            stats["files"] += 1
            stats["bytes"] += len(content)
            print("  + %-56s %6.1f KB" % (rel, len(content) / 1024.0))
        except Exception as e:
            stats["failed"] += 1
            print("  !! %s: %s" % (rel, e))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if len(sys.argv) < 3:
        print(__doc__)
        return 1

    repo = sys.argv[1]
    outdir = sys.argv[2]
    paths = sys.argv[3:] or [""]

    print("仓库: %s" % repo)
    print("输出: %s" % outdir)
    print("代理: %s" % PROXY)
    print()

    stats = {"files": 0, "bytes": 0, "failed": 0, "skipped": 0}
    for p in paths:
        print("=== 拉取 %s ===" % (p or "/"))
        fetch_tree(repo, p, outdir, stats)

    print()
    print("完成: %d 个文件, %.2f MB, 失败 %d, 跳过 %d" % (
        stats["files"], stats["bytes"] / 1048576.0,
        stats["failed"], stats["skipped"]))
    return 0 if stats["failed"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
