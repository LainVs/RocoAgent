# -*- coding: utf-8 -*-
"""从 rocoarena.com 抓取天梯样本库阵容，存成结构化 JSON。

用途
----
1. 知道常见阵容长什么样（6 只 + 性格 + 4 个技能）
2. 给对手模型做先验：对面带某只精灵时，它大概率带哪 4 个技能
3. 给决策做配队参考

网站是 SSR 渲染的（能直接抓 HTML 里的文本），不需要浏览器。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import paths

BASE = "https://rocoarena.com"
OUT = os.path.join(str(paths.DATA), "arena_teams.json")
CACHE = os.path.join(str(paths.LOGS), "arena_cache")


def fetch(url, retries=3):
    os.makedirs(CACHE, exist_ok=True)
    key = urllib.parse.quote(url, safe="")[:120] + ".html"
    p = os.path.join(CACHE, key)
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 86400:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    for i in range(1, retries + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                html = r.read().decode("utf-8", "replace")
            with open(p, "w", encoding="utf-8") as f:
                f.write(html)
            return html
        except Exception as e:
            print("  第 %d 次失败: %s" % (i, str(e)[:70]))
            time.sleep(3 * i)
    return ""


def text_of(html):
    """HTML -> 纯文本（保留换行结构）。"""
    s = re.sub(r"<script[\s\S]*?</script>", " ", html)
    s = re.sub(r"<style[\s\S]*?</style>", " ", s)
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"</(p|div|li|h[1-6]|tr)>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    s = re.sub(r"[ \t]+", " ", s)
    return "\n".join(ln.strip() for ln in s.splitlines() if ln.strip())


def list_teams():
    """抓样本库首页，返回 [(名字, url, id)]。"""
    html = fetch(BASE + "/library")
    teams = []
    seen = set()
    for m in re.finditer(r'href="(/library\?team=([0-9a-f-]+))"[^>]*>([^<]*)<',
                         html):
        tid = m.group(2)
        if tid in seen:
            continue
        seen.add(tid)
        name = m.group(3).strip()
        if name:
            teams.append({"name": name, "url": BASE + m.group(1), "id": tid})
    return teams


def parse_team(html):
    """解析一套阵容：6 只精灵，每只有 名字/属性/性格/4 技能。

    纯文本结构是三条一组，**没有 markdown 前缀**：
        海枝枝（翠绿纶布）
        水 / 幽 · 急躁
        天洪、灵媒、嘲弄、打湿
    所以按"中间那行含 · 且下一行含 、"来定位。
    """
    txt = text_of(html)
    lines = txt.splitlines()

    title = None
    for ln in lines:
        if "站点收录阵容：" in ln:
            title = ln.split("站点收录阵容：", 1)[1].split("。")[0]
            break

    # 从"站点收录阵容"之后开始找（前面是标题和 meta）
    start = 0
    for i, ln in enumerate(lines):
        if ln == "站点收录阵容":
            start = i + 1
            break

    mons = []
    i = start
    while i + 2 < len(lines):
        name = lines[i]
        type_nature = lines[i + 1]
        moves_line = lines[i + 2]
        # 判据：第二行有 ·（属性 · 性格），第三行是技能列表。
        # 注意单属性精灵是 "恶 · 聪明"，没有 "/"，所以不能要求必须有 /。
        if "·" in type_nature:
            types_part, nature = type_nature.split("·", 1)
            types = [t.strip() for t in types_part.split("/") if t.strip()]
            moves = [m.strip() for m in moves_line.split("、") if m.strip()]
            # 属性必须是已知的 18 系之一，避免把正文当成精灵
            KNOWN = {"普通", "火", "水", "草", "电", "冰", "武", "毒", "地",
                     "翼", "萌", "幻", "虫", "幽", "机械", "龙", "恶", "光"}
            if types and moves and all(t in KNOWN for t in types):
                mons.append({
                    "name": name.strip(),
                    "types": types,
                    "nature": nature.strip(),
                    "moves": moves,
                })
                i += 3
                continue
        i += 1

    return {"title": title, "members": mons}


def main():
    print("抓样本库首页 …")
    teams = list_teams()
    print("找到 %d 套阵容" % len(teams))

    out = []
    for t in teams:
        print("  %-16s " % t["name"], end="", flush=True)
        html = fetch(t["url"])
        d = parse_team(html)
        d.update({"id": t["id"], "name": t["name"], "url": t["url"]})
        out.append(d)
        names = [m["name"] for m in d["members"]]
        print("%d 只: %s" % (len(d["members"]), "、".join(names) or "解析失败"))

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n已保存 -> %s" % OUT)

    # 统计：哪些精灵最常见
    from collections import Counter
    c = Counter()
    for t in out:
        for m in t["members"]:
            c[m["name"]] += 1
    print("\n出现频率 top 12:")
    for name, n in c.most_common(12):
        print("  %-18s %d" % (name, n))

    # 统计：每只精灵的常见技能
    print("\n技能先验（出现 >= 2 次的）：")
    for name, _ in c.most_common(8):
        mv = Counter()
        for t in out:
            for m in t["members"]:
                if m["name"] == name:
                    for s in m["moves"]:
                        mv[s] += 1
        if mv:
            print("  %-14s %s" % (name, "、".join(
                "%s×%d" % (k, v) for k, v in mv.most_common(4))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
