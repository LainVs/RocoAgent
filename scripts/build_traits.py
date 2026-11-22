# -*- coding: utf-8 -*-
"""把「特性效果」整理成模拟器可用的规则表 -> data/traits.json

数据来源
--------
1. **roco-cal 的结构化特性**（权威源，`data/pets_trait_json/`，264 个文件）
   形态长这样，是机器可读的：

       {"特性": {"名称": "专注力", "效果": "入场首回合，获得物攻+100%",
         "battle_effect": {"affects_battle": true, "effect_groups": [{
             "mode": "first_turn",
             "context": {"subject": "self", "timing": "turn_start"},
             "effects": [{"kind": "stat_buff", "target": "self",
                          "values": {"atk": 1.0}}]}]}}}

   实测 260 条能解析，其中 **111 条**有结构化效果组（其余被 roco-cal
   自己标成 ignored，只有文字）。

2. **文本推导的补充**（TEXT_DERIVED）：吸血类效果 roco-cal 全标成了
   ignored（`渴求`「入场时获得30%吸血效果」就是），没有结构化数据。
   这里只收**描述明确、且条件我们能判定**的少数几条，并保留原文备查。

为什么大部分结构化特性也用不了
------------------------------
效果组的触发条件大量依赖本模拟器**没有建模**的状态：天气、印记层数、
等级、异常状态、对手换人次数、全场死亡数、蓄力……硬套等于编数据。
所以运行时有一张显式的 SUPPORTED 白名单，只应用能判定的组合，
其余计入 unsupported（`/api/sim/traits` 会报出来覆盖率）。

用法：
    & $py scripts/build_traits.py
"""
import glob
import io
import json
import os
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

SRC = os.path.join(ROOT, "data", "external", "roco-cal", "data",
                   "pets_trait_json")
OUT = os.path.join(ROOT, "data", "traits.json")

# 文本推导的补充。key = 特性名；每条规则带 source（原文），便于核对。
# 只收条件能判定的：
#   · 渴求/贪得无厌  无条件的「入场时获得30%吸血」
#   · 先机虹吸       先手时额外 30%（出手顺序我们是知道的）
# 明确**不收**的（条件无法判定，硬套就是编数据）：
#   · 傲慢           只对「蓄力技能」生效（本模拟器没有蓄力机制）
#   · 滴眼液         需要「雨天」天气
#   · 异类           原文自相矛盾（效果写 100%、ignored_text 写 50%）
TEXT_DERIVED = {
    "渴求": [{"timing": "entrance", "mode": "always", "kind": "lifesteal",
              "value": 0.30, "source": "入场时获得30%吸血效果"}],
    "贪得无厌": [{"timing": "entrance", "mode": "always", "kind": "lifesteal",
                  "value": 0.30, "source": "入场时获得30%吸血"}],
    "先机虹吸": [{"timing": "usage", "mode": "firsthand", "kind": "lifesteal",
                  "value": 0.30, "source": "先手攻击时，额外吸血30%"}],
}

# 只保留这些字段，避免把 roco-cal 的内部结构原样搬进运行时
KEEP = ("kind", "target", "value", "values", "value_per_stack",
        "values_per_stack", "value_type", "filters")


def load_json(p):
    # 有 BOM 的文件（实测 1 个）用 utf-8-sig
    with io.open(p, encoding="utf-8-sig") as fh:
        return json.load(fh)


def main():
    traits = {}
    files = glob.glob(os.path.join(SRC, "**", "*.json"), recursive=True)
    for p in files:
        try:
            d = load_json(p)
        except Exception:
            continue
        t = (d or {}).get("特性") or {}
        name = (t.get("名称") or "").strip()
        if not name:
            continue
        be = t.get("battle_effect") or {}
        rules = []
        if be.get("affects_battle"):
            for g in be.get("effect_groups") or []:
                cx = g.get("context") or {}
                timing = cx.get("timing") or "default"
                mode = g.get("mode") or "always"
                for e in g.get("effects") or []:
                    r = {"timing": timing, "mode": mode}
                    for k in KEEP:
                        if k in e:
                            r[k] = e[k]
                    rules.append(r)
        traits[name] = {
            "desc": (t.get("效果") or "").strip(),
            "structured": bool(be.get("affects_battle")),
            "rules": rules,
        }

    # 合并文本推导项
    n_text = 0
    for name, rs in TEXT_DERIVED.items():
        ent = traits.setdefault(name, {"desc": "", "structured": False,
                                       "rules": []})
        for r in rs:
            if r not in ent["rules"]:
                ent["rules"].append(r)
                n_text += 1

    kinds = Counter(r.get("kind") for t in traits.values()
                    for r in t["rules"])
    timings = Counter(r.get("timing") for t in traits.values()
                      for r in t["rules"])
    payload = {
        "source": "roco-cal data/pets_trait_json（结构化） + 文本推导补充",
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "运行时只应用 src/simulator.py 里 SUPPORTED 白名单的组合，"
                "其余计入 unsupported",
        "text_derived_rules": n_text,
        "traits": traits,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))

    print("写出 %s" % OUT)
    print("  特性 %d 条（结构化 %d 条，规则 %d 条，其中文本推导 %d 条）"
          % (len(traits),
             sum(1 for t in traits.values() if t["structured"]),
             sum(len(t["rules"]) for t in traits.values()), n_text))
    print("  kind:", kinds.most_common())
    print("  timing:", timings.most_common())

    # 用户点名的例子，以及吸血类特性挂在谁身上
    for nm in ("专注力", "渴求", "贪得无厌", "先机虹吸"):
        t = traits.get(nm)
        if t:
            print("  [%s] %s" % (nm, t["desc"]))
            for r in t["rules"][:3]:
                print("      %s/%s %s=%s %s"
                      % (r.get("timing"), r.get("mode"), r.get("kind"),
                         r.get("value", r.get("values")),
                         r.get("source", "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
