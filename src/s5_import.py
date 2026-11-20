# -*- coding: utf-8 -*-
"""s5_import.py - 把 S5 赛季精灵补进 roco.sqlite3。

数据来源
--------
bilibili 洛克王国世界 WIKI（https://wiki.biligame.com/nrc/精灵图鉴）
每只精灵页面的「基础资料 / 种族资质」段落里有六维，格式固定：

    种族资质 502 生命 118 物攻 128 魔攻 42 物防 81 魔防 63 速度 70

所以抓页面 + 正则就能拿到全部数值，不需要手工抄。

用法
----
    python s5_import.py --fetch          # 从 wiki 抓（需要网络）
    python s5_import.py --apply          # 写入数据库
    python s5_import.py --verify         # 只校验一致性

设计原则
--------
* 抓不到就**留空**，不填占位值（库里已经因为手录占位踩过坑）
* 写入前先备份
* 所有新增行打 `provisional=1`，人工核对后再置 0
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import paths

DB = str(paths.DB)
CACHE = str(paths.LOGS / "wiki_cache")

# S5 名单（含进化链）
S5_PETS = [
    # 传说
    "小冥龙", "冥古龙",
    # 通行证
    "地绡银龙", "龙嵴金脉",
    # 情怀返场
    "阿布", "超能阿布",
    # 奇遇
    "乐乐鲨", "鸡麻麻",
    "蛋包蛋", "爆弹包",
    "蟹蟹椰", "沙咔蟹",
    "鲸羽", "圆环鲸羽",
    "豆粒鹅", "刨雪鹅",
    "西瓜虫", "冬虫夏瓜",
    "滴溜豚", "旋流豚",
    "吸泥鸥", "泥沼飞鸥",
    "花怨鳗", "怨潮鳗",
    "小翼龙", "迅翼龙",
    "小剪影", "影子剪纸",
    "云梦豚",
]

TYPE_IDS = {
    "普通": 0, "火": 1, "水": 2, "草": 3, "电": 4, "冰": 5, "武": 6,
    "毒": 7, "地": 8, "翼": 9, "萌": 10, "幻": 11, "虫": 12, "幽": 13,
    "机械": 14, "龙": 15, "恶": 16, "光": 17,
}

WIKI = "https://wiki.biligame.com/nrc/"


def _get(name, retries=3):
    """抓 wiki 页面，带磁盘缓存 + 限流重试。"""
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, urllib.parse.quote(name) + ".html")
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 86400:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    url = WIKI + urllib.parse.quote(name)
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": WIKI,
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                html = r.read().decode("utf-8", "replace")
            with open(p, "w", encoding="utf-8") as f:
                f.write(html)
            time.sleep(1.2)          # 别把 wiki 打急
            return html
        except Exception as e:
            code = getattr(e, "code", None)
            if code == 567 or "567" in str(e):
                wait = 6 * attempt
                print("[限流 %ds]" % wait, end=" ", flush=True)
                time.sleep(wait)
                continue
            print("  抓取失败 %s: %s" % (name, e))
            time.sleep(2)
    return ""


NUM = r"(\d+)"


def parse_stats(html):
    """从页面里抠六维。

    wiki 的文本形如：
        种族资质 502 生命 118 物攻 128 魔攻 42 物防 81 魔防 63 速度 70
    中间可能夹 HTML 标签，先剥标签再匹配。
    """
    if not html:
        return None
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)

    # 找 "生命 X ... 速度 Y" 这一段
    m = re.search(
        r"生命\s*" + NUM + r"\s*物攻\s*" + NUM + r"\s*魔攻\s*" + NUM +
        r"\s*物防\s*" + NUM + r"\s*魔防\s*" + NUM + r"\s*速度\s*" + NUM,
        text)
    if not m:
        return None
    hp, atk, spa, df, spd, spe = (int(x) for x in m.groups())

    total = None
    mt = re.search(r"种族资质\s*" + NUM, text)
    if mt:
        total = int(mt.group(1))

    return {"hp": hp, "atk": atk, "spa": spa, "def": df, "spd": spd,
            "spe": spe, "total": total or (hp + atk + spa + df + spd + spe)}


LIST_URL = WIKI + urllib.parse.quote("精灵列表")


def fetch_list(retries=3):
    """抓「精灵列表」页（一张表，含全部精灵的结构化属性）。"""
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, "_list.html")
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 86400:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(LIST_URL, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0 Safari/537.36",
            "Referer": WIKI,
        })
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                html = r.read().decode("utf-8", "replace")
            with open(p, "w", encoding="utf-8") as f:
                f.write(html)
            return html
        except Exception as e:
            print("列表页第 %d 次失败: %s" % (attempt, str(e)[:60]))
            time.sleep(5 * attempt)
    return ""


ROW_RE = re.compile(r"<tr class=\"npc-table-row\"([^>]*)>")
ATTR_RE = re.compile(r'data-([a-z]+)="([^"]*)"')


def parse_list_types(html):
    """从精灵列表页解析 {名字: [属性...]}。

    每行是：
        <tr class="npc-table-row" data-number="001" data-type="光" ...>
        ... <a href="/nrc/迪莫" title="迪莫">迪莫</a> ...
    data-type 是主属性；双属性会出现在 npc-table-types 的 aria-label 里
    （形如 aria-label="幽系|龙系"）。两种都读。
    """
    out = {}
    if not html:
        return out

    # 按行切
    rows = re.split(r"(?=<tr class=\"npc-table-row\")", html)
    for row in rows[1:]:
        m = ROW_RE.match(row)
        if not m:
            continue
        attrs = dict(ATTR_RE.findall(m.group(1)))
        name = None
        mn = re.search(r'<a href="/nrc/[^"]*" title="([^"]+)">', row)
        if mn:
            name = mn.group(1)
        if not name:
            continue

        types = []
        # 1) 表格里的 aria-label：aria-label="光系" 或 "幽系|龙系"
        for ma in re.finditer(r'aria-label="([^"]*系[^"]*)"', row[:2000]):
            for t in re.split(r"[|/、\s]+", ma.group(1)):
                t = t.strip().rstrip("系")
                if t in TYPE_IDS and t not in types:
                    types.append(t)
        # 2) data-type 兜底
        if not types and attrs.get("type"):
            for t in re.split(r"[|/、\s]+", attrs["type"]):
                t = t.strip()
                if t in TYPE_IDS and t not in types:
                    types.append(t)
        if types:
            out[name] = types[:2]
    return out


def parse_feature(html):
    """特性：基础资料区块里 '特性 名字 描述'。

    wiki 结构：... 基础资料 特性 <名字> <描述> 种族资质 ...
    所以取「特性」与「种族资质」之间的内容。
    """
    if not html:
        return None
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    m = re.search(r"基础资料\s*特性\s*(.{0,120}?)\s*种族资质", text)
    if not m:
        return None
    blob = m.group(1).strip()
    if not blob:
        return None
    # 名字通常是前 2~8 个汉字，后面跟描述
    mn = re.match(r"([\u4e00-\u9fa5·A-Za-z]{2,10})\s*(.*)", blob)
    if not mn:
        return {"name": blob[:10], "desc": ""}
    return {"name": mn.group(1), "desc": mn.group(2).strip()}


def fetch_all():
    print("抓精灵列表页（含全部属性）…")
    lst = fetch_list()
    types_map = parse_list_types(lst)
    print("列表页解析到 %d 只精灵的属性" % len(types_map))

    out = {}
    for n in S5_PETS:
        print("抓 %s …" % n, end=" ", flush=True)
        html = _get(n)
        st = parse_stats(html)
        ty = types_map.get(n, [])
        ft = parse_feature(html)
        out[n] = {"stats": st, "types": ty, "feature": ft}
        if st:
            print("六维 %d/%d/%d/%d/%d/%d 总值%d  属性%s  特性%s" % (
                st["hp"], st["atk"], st["spa"], st["def"], st["spd"],
                st["spe"], st["total"], "".join(ty) or "?",
                (ft or {}).get("name") or "?"))
        else:
            print("未解析到六维（属性%s）" % ("".join(ty) or "?"))
    p = str(paths.LOGS / "s5_fetched.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n已保存 ->", p)
    return out


def apply(data):
    shutil.copyfile(DB, DB + ".bak_s5")
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    added = updated = 0
    for name, d in data.items():
        st = d.get("stats")
        if not st:
            print("  跳过 %s（没有六维）" % name)
            continue
        ty = d.get("types") or []
        tid = [TYPE_IDS[t] for t in ty if t in TYPE_IDS]
        row = db.execute("SELECT id FROM species WHERE name=?", (name,)).fetchone()
        if row:
            db.execute(
                "UPDATE species SET types=?, type_names=?, hp=?, atk=?, spa=?, "
                "def=?, spd=?, spe=?, total=?, provisional=1 WHERE id=?",
                (json.dumps(tid), json.dumps(ty, ensure_ascii=False),
                 st["hp"], st["atk"], st["spa"], st["def"], st["spd"],
                 st["spe"], st["total"], row["id"]))
            updated += 1
        else:
            # 新 id 用 990000100 起，避开已占用的手录区
            nid = db.execute(
                "SELECT COALESCE(MAX(id),990000099)+1 FROM species "
                "WHERE id>=990000100").fetchone()[0]
            db.execute(
                "INSERT INTO species(id,name,types,type_names,hp,atk,spa,def,"
                "spd,spe,total,provisional) VALUES(?,?,?,?,?,?,?,?,?,?,?,1)",
                (nid, name, json.dumps(tid), json.dumps(ty, ensure_ascii=False),
                 st["hp"], st["atk"], st["spa"], st["def"], st["spd"],
                 st["spe"], st["total"]))
            added += 1
            print("  新增 %s id=%d" % (name, nid))

        ft = d.get("feature")
        if ft and ft.get("name"):
            r = db.execute("SELECT id FROM features WHERE name=?",
                           (ft["name"],)).fetchone()
            if r:
                fid = r["id"]
            else:
                import zlib
                fid = 990000000 + (int(zlib.crc32(ft["name"].encode()) & 0x7FFFFFFF) % 10000000)
                db.execute("INSERT OR REPLACE INTO features(id,name,desc,"
                           "effect_type,category,params) VALUES(?,?,?,'panel',NULL,NULL)",
                           (fid, ft["name"], ft.get("desc")))
            sid = db.execute("SELECT id FROM species WHERE name=?", (name,)).fetchone()["id"]
            db.execute("INSERT OR REPLACE INTO feature_pets(species_id,feature_id,"
                       "is_primary) VALUES(?,?,1)", (sid, fid))
    db.commit()
    db.close()
    print("\n新增 %d，更新 %d，备份 %s.bak_s5" % (added, updated, DB))


def verify():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    bad = []
    for r in db.execute("SELECT id,name,hp,atk,spa,def,spd,spe,total "
                        "FROM species WHERE total>0"):
        s = r["hp"] + r["atk"] + r["spa"] + r["def"] + r["spd"] + r["spe"]
        if s != r["total"]:
            bad.append((r["name"], r["total"], s))
    print("六维之和 != total 的精灵: %d / %d" % (
        len(bad), db.execute("SELECT COUNT(*) FROM species WHERE total>0").fetchone()[0]))
    for n, t, s in bad[:15]:
        print("  %-12s total=%d 实际=%d" % (n, t, s))
    if len(bad) > 15:
        print("  …还有 %d 条" % (len(bad) - 15))

    print("\nS5 覆盖:")
    miss = []
    for n in S5_PETS:
        r = db.execute("SELECT total,type_names FROM species WHERE name=?",
                       (n,)).fetchone()
        if not r:
            miss.append(n)
        else:
            print("  %-10s total=%-4d %s" % (n, r["total"], r["type_names"]))
    if miss:
        print("  仍缺: %s" % "、".join(miss))
    db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()

    if a.verify:
        verify()
        return 0
    if a.fetch:
        data = fetch_all()
        if a.apply:
            apply(data)
        return 0
    if a.apply:
        p = str(paths.LOGS / "s5_fetched.json")
        with open(p, encoding="utf-8") as f:
            apply(json.load(f))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
