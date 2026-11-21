# -*- coding: utf-8 -*-
"""对比初版（policy_legacy）与新版（policy_v2）的选择差异。

用户的问题
----------
"初版机器人都知道用字面伤害更高的，现在的却做不到的时候了"

要回答这个问题，必须把两版逻辑摆在一起看，找出：
    · 初版凭什么选最高
    · 新版在什么情况下会不选最高

跑法：
    python scripts/compare_policy_versions.py
"""
import os
import sys

sys.path.insert(0, "src")

import policy_legacy as OLD
import policy_v2 as NEW


def card(i, v, cost=3, lit=True, kind="num", color="green", text=None):
    return {"i": i, "index": i, "dmg": v, "value": v, "cost": cost,
            "lit": lit, "kind": kind, "color": color,
            "text": text if text is not None else (str(v) if v else "--")}


def run_old(skills, my_pct=100.0, foe_pct=90.0, costs=None):
    """初版：policy_legacy.decide(st, ctx)"""
    st = {"skills": skills, "my_pct": my_pct, "foe_pct": foe_pct}
    ctx = {"card_costs": costs or {}}
    r = OLD.decide(st, ctx)
    if r is None or r[0] is None:
        return None, "no decision"
    return r[0], r[1]


def run_new(skills, my_pct=100.0, foe_pct=90.0, my_hp=400, foe_hp=400):
    """新版：policy_v2.decide(state)"""
    state = {
        "me": {"name": "测试", "hp": my_hp, "hp_max": my_hp, "energy": 10,
               "skills": skills},
        "foe": {"name": "对手", "hp": foe_hp, "hp_max": foe_hp},
        "bench": [], "turn": 3, "buffed": False,
    }
    act, why, _ = NEW.decide(state)
    return act, why


def show(title, skills, **kw):
    print()
    print("=" * 70)
    print(title)
    print("-" * 70)
    for s in skills:
        if s is None:
            print("  (空卡)")
            continue
        print("  slot%d  dmg=%-5s cost=%-2s lit=%-5s kind=%s" % (
            s["i"], s["dmg"], s["cost"], s["lit"], s["kind"]))

    a_old, w_old = run_old(skills, **{k: v for k, v in kw.items()
                                      if k in ("my_pct", "foe_pct")})
    a_new, w_new = run_new(skills, **{k: v for k, v in kw.items()
                                      if k in ("my_hp", "foe_hp")})
    print()
    print("  初版 -> %-16s %s" % (str(a_old), w_old[:60]))
    print("  新版 -> %-16s %s" % (str(a_new), w_new[:60]))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    # 场景 1：典型的"高伤害 + 高耗能 vs 低伤害 + 低耗能"
    show("场景 1：300 伤害 cost=6  vs  150 伤害 cost=2（用户提到的形态）",
         [card(1, 300, 6), card(2, 150, 2)])

    # 场景 2：扬沙 vs 主轴（用户实际遇到的）
    show("场景 2：扬沙 360/cost1  vs  主轴 150/cost2",
         [card(1, 360, 1), card(2, 150, 2)])

    # 场景 3：高伤卡读不全（value=None）
    s3 = [card(1, None, 1, text="36?"), card(2, 150, 2)]
    s3[0]["dmg"] = None
    show("场景 3：高伤卡读不全（text='36?'）vs 读得清的 150", s3)

    # 场景 4：高伤卡 lit=False（能量不足）
    show("场景 4：高伤 400 但 lit=False  vs  150 可点",
         [card(1, 400, 6, lit=False), card(2, 150, 2)])

    # 场景 5：高伤卡是 dash（变化技能）
    s5 = [card(1, 400, 3), card(2, 150, 2)]
    s5[0]["kind"] = "dash"
    show("场景 5：高伤卡被判成 dash（变化技能）vs 150", s5)

    # 场景 6：能杀但慢速，会被反杀
    show("场景 6：400 伤害能杀，但会被先手反杀（新版会考虑这个）",
         [card(1, 400, 6), card(2, 150, 2)], my_hp=100, foe_hp=300)

    print()
    print("=" * 70)
    print("说明")
    print("-" * 70)
    print("  初版 = policy_legacy.decide：只比字面伤害（扣一点能耗惩罚）")
    print("  新版 = policy_v2.decide：算期望值（伤害/击杀/承受/属性/能量/换人）")
    print()
    print("  新版不选最高伤害的**合法理由**：")
    print("    · 高伤卡 lit=False（能量不足，放不出）")
    print("    · 高伤卡能杀但会被先手反杀，换一张更稳")
    print("    · 高伤卡读不全（value=None），不敢赌")
    print("    · 高伤卡是变化技能（dash/copy），不是伤害")
    print("  新版不选最高伤害的**bug 形态**：")
    print("    · 卡面读数错（OCR 把 360 读成 60）")
    print("    · 稳定化把新值锁成旧值")
    print("    · 估算值（_est_value）虚高，压过真实读数")
    return 0


if __name__ == "__main__":
    sys.exit(main())
