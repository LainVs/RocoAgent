# -*- coding: utf-8 -*-
"""belief.py —— 对手技能信念分布（PIMC 的采样器）。

背景
----
PIMC（Perfect Information Monte Carlo）第一步：从"对手可能带什么技能"
的信念分布里采样一组 4 技能，再对每个采样做确定性搜索。本文件管采样，
不跑战斗引擎。

对外接口
--------
    sample_moveset(foe_species, k=4, rng=None)
        -> list[Skill]，长度 <= k（技能池不足时可能更少）
    moveset_distribution(foe_species, top=12)
        -> [(技能名, 概率), ...]，按概率降序

信念来源（按优先级，实现为加权池）：
    1. ROCO ARENA 天梯样本库（data/external/rocoarena/teams.json）
       同精灵真实玩家的配招，最可信。60% 概率直接抽一整组真实配招
       （保留玩家真实的 4 技能连招），其余走单技能加权池。
    2. 我方日志观测（FOE SCAN 行 + 库里的 foe_skill_obs 表，只读）
    3. 全部可学技能（data/roco.sqlite3 的 species_skills 表，均匀兜底）

单技能权重：
    w(skill) = ARENA_W * arena_cnt + obs_cnt + UNIFORM_BASE

注意
----
· ROCO ARENA 的 member.name 带形态后缀（"音速犬（看守麦田的样子）"），
  匹配前先剥掉 "（" 之后的部分（_base_name）。
· 构造 Skill 一律走 sim.adapter.build_skill(skill_id)（数值以我们库为准），
  adapter.py 不许改动，只调用。
· 本文件对 data/roco.sqlite3 只读（SELECT），绝无写操作。
"""
from __future__ import annotations

import collections
import json
import os
import random
import re
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.dirname(_HERE)
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import paths                       # noqa: E402
from sim import adapter            # noqa: E402
from sim.skill_db import get_skill as _sim_get_skill  # noqa: E402

ARENA_JSON = str(paths.ROOT / "data" / "external" / "rocoarena" / "teams.json")
FOE_SCAN_LOG = paths.log_path("bot_stdout.log")

# ---------------------------------------------------------------- 权重常量
ARENA_SET_P = 0.6     # 优先抽"一整组真实配招"的概率（比重加权更高，更可信）
ARENA_W = 3.0         # rocoarena 命中一个技能加的权重（同 foe_moves）
OBS_W = 1.0           # 日志观测到一次加的权重
UNIFORM_BASE = 0.2    # 每个可学技能的均匀兜底权重

# ---------------------------------------------------------------- 基础工具
_SKILL_CACHE = {}


# ---------------------------------------------------------------- 效果扁平化
def sanitize_effects(effects):
    """把"嵌套的 SkillEffect"展开成 Engine 能消费的平铺标签。

    背景（2026-11-21 实测）：sim 技能生成数据里有 8 个技能（富养化/风隐/
    雪球/架势/休息回复/徒长/根吸收/杠杆置换）的效果是
        SkillEffect(timing, effects=[SkillEffect(timing, effects=[EffectTag(...)])])
    即**两层 SkillEffect 嵌套**。而 battle._iter_flat_tags 不会递归展开，
    下游 `tag.type` 直接炸 AttributeError（本任务采样 卡瓦重 时打爆过引擎）。

    battle.py 不许改动，所以在**我们自己的采样入口**先把效果摊平：
    任意层级的 SkillEffect 包着的 EffectTag 都提到它的 timing/filter 下。
    语义不变（嵌套 wrapper 只是重复包一层），Engine 能正常消费。
    """
    from sim.effect_models import SkillEffect as _SE
    out = []
    for item in effects or []:
        if isinstance(item, _SE):
            inner = sanitize_effects(getattr(item, "effects", None) or [])
            flat = []
            for t in inner:
                if isinstance(t, _SE):
                    # 再包一层的情况（保险）：把它的 tags 提上来
                    flat.extend(sanitize_effects(getattr(t, "effects", None) or []))
                else:
                    flat.append(t)
            out.append(_SE(timing=item.timing, effects=flat, filter=item.filter))
        else:
            out.append(item)
    return out


def _db():
    conn = sqlite3.connect(str(paths.DB))
    conn.row_factory = sqlite3.Row
    return conn


def _base_name(name):
    """剥掉形态/立绘后缀："音速犬（看守麦田的样子）" -> "音速犬"。

    后缀标记与 opponent_model / adapter.normalize_name 保持一致：
    （ 、( 、#（立绘编号）。
    """
    if not name:
        return ""
    s = str(name)
    for mark in ("（", "(", "#"):
        if mark in s:
            s = s.split(mark)[0]
    return s.strip()


def _build_by_id(skill_id):
    """按 ID 构造 Skill（带小缓存）。数值以我们库为准（adapter 规则）。

    顺带把嵌套 SkillEffect 摊平（见 sanitize_effects），避免引擎炸。
    """
    if skill_id in _SKILL_CACHE:
        return _SKILL_CACHE[skill_id]
    sk = adapter.build_skill(int(skill_id))
    if sk is not None:
        sk.effects = sanitize_effects(getattr(sk, "effects", None) or [])
    _SKILL_CACHE[skill_id] = sk
    return sk


def _build_by_name(name):
    """按技能名构造 Skill：先查我们库的 id 走 adapter，查不到再退回 sim 技能库。

    含同样的嵌套 Effect 摊平。
    """
    if name in _SKILL_CACHE:
        return _SKILL_CACHE[name]
    sk = None
    try:
        db = _db()
        try:
            row = db.execute(
                "SELECT id FROM skills WHERE name=? LIMIT 1", (name,)).fetchone()
        finally:
            db.close()
        if row:
            sk = adapter.build_skill(int(row["id"]))
    except Exception:
        sk = None
    if sk is None:
        # NRC 技能库兜底（数值不如我们库准，但至少有这个技能）
        sk = _sim_get_skill(name)
    if sk is not None:
        sk.effects = sanitize_effects(getattr(sk, "effects", None) or [])
    _SKILL_CACHE[name] = sk
    return sk


# ---------------------------------------------------------------- 数据源
_ARENA_CACHE = None


def _arena_loadouts():
    """base_species -> list[(skill_ids, skill_names)]，每个元素是一整套真实配招。

    数据来源：data/external/rocoarena/teams.json（真实天梯样本库，已实测）。
    """
    global _ARENA_CACHE
    if _ARENA_CACHE is not None:
        return _ARENA_CACHE
    idx = collections.defaultdict(list)
    try:
        with open(ARENA_JSON, encoding="utf-8") as f:
            d = json.load(f)
        for team in d.get("teams") or []:
            for m in team.get("members") or []:
                base = _base_name(m.get("name"))
                ids, names = [], []
                for s in m.get("skills") or []:
                    gid = s.get("gameSkillId")
                    nm = s.get("name")
                    if gid:
                        ids.append(int(gid))
                    if nm:
                        names.append(nm)
                if base and ids:
                    idx[base].append((ids, names))
    except Exception:
        idx = collections.defaultdict(list)
    _ARENA_CACHE = dict(idx)
    return _ARENA_CACHE


def learnable(species):
    """这只精灵的可学技能池：skill_name -> skill_id（同名去重，保留一个 id）。

    不筛 power>0：状态技/防御技也要能进信念池（真实配招常带）。
    """
    base = _base_name(species)
    db = _db()
    try:
        rows = db.execute(
            """SELECT s.id, s.name, s.power, s.cost
               FROM species sp
               JOIN species_skills ss ON ss.species_id = sp.id
               JOIN skills s ON s.id = ss.skill_id
               WHERE sp.name = ?
               ORDER BY s.power DESC, s.id""", (base,)).fetchall()
    finally:
        db.close()
    out = {}
    for r in rows:
        nm = r["name"]
        if nm and nm not in out:
            out[nm] = int(r["id"])
    return out


def _observations(species):
    """日志观测到的技能：skill_name -> count（只读，与 foe_moves 口径一致）。

    来源：
      1. data/roco.sqlite3 的 foe_skill_obs 表（跨重启累积，SELECT 只读）
      2. logs/bot_stdout.log 的 "FOE SCAN -> ... 技能=..." 行（正则扫描）
    """
    base = _base_name(species)
    obs = collections.Counter()
    try:
        db = sqlite3.connect("file:%s?mode=ro" % paths.DB, uri=True)
        db.row_factory = sqlite3.Row
        try:
            for r in db.execute(
                    "SELECT skill, n FROM foe_skill_obs WHERE species=?",
                    (base,)):
                obs[r["skill"]] += int(r["n"] or 0)
        finally:
            db.close()
    except Exception:
        pass
    try:
        pat = re.compile(r"FOE SCAN -> (\S+) lv\S* 特性=\S+ 技能=(\S+)")
        with open(FOE_SCAN_LOG, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = pat.search(line)
                if not m:
                    continue
                if _base_name(m.group(1)) != base:
                    continue
                sk = m.group(2)
                if sk not in ("None", ""):
                    obs[sk] += 1
    except OSError:
        pass
    return obs


# ---------------------------------------------------------------- 权重与抽样
def _weighted_pool(species):
    """加权技能池。

    返回 (items, weights, id_of)：
        items  — 技能名列表
        weights— name -> 权重（arena + obs + uniform）
        id_of  — name -> skill_id（可能是 None，抽样后按名回退构造）
    """
    base = _base_name(species)
    learn = learnable(base)                       # name -> id
    w = {nm: UNIFORM_BASE for nm in learn}        # 均匀兜底

    # 1. ROCO ARENA 真实配招（每出现一次 +ARENA_W）
    for ids, _names in _arena_loadouts().get(base, []):
        for gid in ids:
            sk = _build_by_id(gid)
            if sk is None:
                continue
            if sk.name not in w:
                w[sk.name] = UNIFORM_BASE
            w[sk.name] += ARENA_W

    # 2. 日志观测（每出现一次 +OBS_W）
    for nm, n in _observations(base).items():
        if nm not in w:
            w[nm] = UNIFORM_BASE
        w[nm] += OBS_W * n

    items = list(w.keys())
    id_of = {nm: learn.get(nm) for nm in items}
    return items, w, id_of


def _weighted_sample(items, weights, k, rng):
    """按权重无放回抽 k 个。（权重全 0 时会退化成接近均匀。）"""
    items = list(items)
    w = dict(weights)
    out = []
    while len(out) < k and items:
        tot = sum(w[i] for i in items) or 1.0
        r = rng.random() * tot
        acc = 0.0
        pick = items[-1]
        for i in items:
            acc += w[i]
            if r <= acc:
                pick = i
                break
        out.append(pick)
        items.remove(pick)
    return out


# ---------------------------------------------------------------- 对外接口
def sample_moveset(foe_species, k=4, rng=None):
    """从信念分布采样一组对手技能，返回 list[Skill]（副本），长度 <= k。

    60% 概率直接抽 ROCO ARENA 里该精灵的一整套真实配招（保留连招），
    其余按加权池无放回抽 k 个；技能池不足时返回更少。
    """
    rng = rng or random
    base = _base_name(foe_species)

    # 优先级 1：真实配招整组采样
    loadouts = _arena_loadouts().get(base, [])
    if loadouts and rng.random() < ARENA_SET_P:
        ids, _names = rng.choice(loadouts)
        out = []
        for gid in ids[:k]:
            sk = _build_by_id(gid)
            if sk is not None:
                out.append(sk.copy())
        if out:
            return out

    # 优先级 2+3：加权池（arena + 观测 + 均匀兜底）
    items, w, _id_of = _weighted_pool(base)
    if items:
        drawn = _weighted_sample(items, w, min(k, len(items)), rng)
        out = []
        for nm in drawn:
            sk = _build_by_name(nm)
            if sk is not None:
                out.append(sk.copy())
        if out:
            return out

    # 最终兜底：可学技能均匀抽
    learn = learnable(base)
    names = list(learn.keys())
    if names:
        if len(names) > k:
            names = rng.sample(names, k)
        out = []
        for nm in names:
            sk = _build_by_name(nm)
            if sk is not None:
                out.append(sk.copy())
        return out
    return []


def moveset_distribution(foe_species, top=12):
    """技能名 -> 概率 的边缘分布，按概率降序。返回 [(技能名, 概率), ...]。"""
    base = _base_name(foe_species)
    items, w, _id_of = _weighted_pool(base)
    if not items:
        return []
    tot = sum(w.values()) or 1.0
    rows = sorted(((nm, w[nm] / tot) for nm in items), key=lambda r: -r[1])
    return rows[:top]


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sp = sys.argv[1] if len(sys.argv) > 1 else "恶魔狼"
    print("=== belief 自检（%s）===" % sp)
    print("\n分布 top 12:")
    for nm, p in moveset_distribution(sp, top=12):
        print("    %-12s p=%.3f" % (nm, p))
    print("\n采样 8 组配招：")
    r = random.Random(20261121)
    seen = set()
    for _ in range(8):
        ms = sample_moveset(sp, k=4, rng=r)
        sig = tuple(m.name for m in ms)
        seen.add(sig)
        print("    %s" % " / ".join(sig))
    print("\n不同配招 %d 组 / 8 次" % len(seen))