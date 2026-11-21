# -*- coding: utf-8 -*-
"""fetch_rocoarena.py —— 拉取 ROCO ARENA 的天梯样本库。

为什么要这个数据
----------------
ROCO ARENA（rocoarena.com）是玩家独立维护的洛克王国工具站，
它的 `/api/teams` 接口返回真实天梯阵容，每套含：

    catalogIdentity.skills   实际装备的 4 个技能（含 gameSkillId）
    abilityName / abilityEffect   特性
    nature / ivStats         性格 + 个体分配
    skillPool                该精灵的完整技能池（含效果描述、耗能、威力）
    recommendedNatures / recommendedInvestments

这正好补上我们的两个缺口：
    1. 对手可能带什么技能（foe_moves 的先验）
    2. 技能的完整效果描述（补 roco-cal / nrc-ai 的漏）

接口说明
--------
    GET /api/teams?summary=1&page=N&pageSize=M&source=site
        -> {groups: [{id, team: {...成员名字...}, variants: [...]}], total}

    GET /api/teams/<team_id>
        -> {team: {payload: {members: [...]}}}   完整数据

    无需登录，但带 no-store 缓存头，要自己存。

跑法：
    python scripts\\fetch_rocoarena.py                  # 拉全部（默认 12 页）
    python scripts\\fetch_rocoarena.py --pages 3        # 只拉前 3 页
    python scripts\\fetch_rocoarena.py --out data/rocoarena_teams.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, "data", "external", "rocoarena", "teams.json")

PROXY = os.environ.get("ROCO_PROXY", "http://127.0.0.1:7897")
BASE = "https://rocoarena.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RocoAgent/1.0"


def _opener():
    handlers = []
    if PROXY:
        handlers.append(urllib.request.ProxyHandler(
            {"http": PROXY, "https": PROXY}))
    return urllib.request.build_opener(*handlers)


def _get(url, retries=4, timeout=90):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "application/json, */*",
                "Referer": BASE + "/library",
            })
            with _opener().open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def list_page(page, page_size):
    url = "%s/api/teams?summary=1&page=%d&pageSize=%d&source=site" % (
        BASE, page, page_size)
    return _get(url)

def team_detail(team_id):
    url = "%s/api/teams/%s" % (BASE, team_id)
    return _get(url)


def extract_member(m):
    """从 member 里抽我们关心的字段。"""
    ident = m.get("catalogIdentity") or {}
    skills = []
    for s in (ident.get("skills") or []):
        skills.append({
            "gameSkillId": s.get("gameSkillId"),
            "name": s.get("name"),
        })
    base = m.get("baseStats") or {}
    return {
        "name": m.get("name"),
        "petId": ident.get("petId"),
        "abilityName": m.get("abilityName"),
        "abilityEffect": m.get("abilityEffect"),
        "nature": m.get("nature"),
        "ivStats": m.get("ivStats"),
        "enhancement": m.get("enhancement"),
        "baseStats": base,
        "skills": skills,
        "recommendedNatures": m.get("recommendedNatures"),
        "recommendedInvestments": m.get("recommendedInvestments"),
        # 技能池单独存，体积大
        "skillPool": [
            {
                "name": sp.get("name"),
                "gameSkillId": sp.get("gameSkillId"),
                "power": sp.get("power"),
                "energyCost": sp.get("energyCost"),
                "type": sp.get("type"),
                "category": sp.get("category"),
                "effect": sp.get("effect"),
            }
            for sp in (m.get("skillPool") or [])
        ],
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=12, help="拉多少页")
    ap.add_argument("--page-size", type=int, default=12)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--no-detail", action="store_true",
                    help="只拉摘要，不拉每个队伍的详情")
    args = ap.parse_args()

    print("代理: %s" % (PROXY or "直连"))
    print("输出: %s" % args.out)
    print()

    teams = []
    total = None
    seen = set()
    page_count = 1

    for page in range(1, args.pages + 1):
        if page > page_count:
            break
        try:
            d = list_page(page, args.page_size)
        except Exception as e:
            print("  第 %d 页失败: %s" % (page, e))
            break
        pag = d.get("pagination") or {}
        if total is None:
            total = pag.get("totalItems")
            page_count = pag.get("pageCount") or 1
            print("站点报告: %s 组 / %s 个阵容，共 %d 页" % (
                pag.get("totalGroups"), pag.get("totalItems"), page_count))
        groups = d.get("groups") or []
        if not groups:
            print("  第 %d 页空，停止" % page)
            break
        print("  第 %d/%d 页: %d 组" % (page, page_count, len(groups)))

        for g in groups:
            for v in (g.get("variants") or [g.get("team")]):
                tid = v.get("id")
                if not tid or tid in seen:
                    continue
                seen.add(tid)
                teams.append({
                    "id": tid,
                    "name": v.get("name"),
                    "teamTypes": v.get("teamTypes"),
                    "source": v.get("source"),
                    "gameMagic": (v.get("gameMagic") or {}).get("name"),
                    "memberNames": [x.get("name")
                                    for x in (v.get("members") or [])],
                })
        time.sleep(0.4)

    print()
    print("共收集 %d 个阵容" % len(teams))

    if not args.no_detail:
        print()
        print("=== 拉详情 ===")
        ok = bad = 0
        for i, t in enumerate(teams, 1):
            try:
                d = team_detail(t["id"])
                payload = ((d.get("team") or {}).get("payload") or {})
                t["members"] = [extract_member(m)
                                for m in (payload.get("members") or [])]
                ok += 1
                if i % 10 == 0 or i == len(teams):
                    print("  %d/%d" % (i, len(teams)))
            except Exception as e:
                t["error"] = str(e)
                bad += 1
            time.sleep(0.3)
        print("  成功 %d, 失败 %d" % (ok, bad))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({
            "source": BASE,
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "site_total": total,
            "count": len(teams),
            "teams": teams,
        }, f, ensure_ascii=False, indent=1)
    print()
    print("已写入 %s (%.1f KB)" % (
        args.out, os.path.getsize(args.out) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
