# -*- coding: utf-8 -*-
"""新模块回归：数据层 / 伤害模型 / 对位库。

跑法：python test_smart.py
"""
import os
import sys
import tempfile

import dmg_calc as D
import matchup_db as M
import rocodata as R


def test_data_layer():
    assert len(R.species()) > 1000, "种族表太小"
    assert len(R.skills()) > 1000, "技能表太小"
    wolf = R.find_species("恶魔狼", exact=True)
    assert wolf, "找不到恶魔狼"
    assert 16 in wolf[0]["types"], "恶魔狼不是恶属性"
    moves = R.pet_moves(wolf[0]["_sid"])
    assert len(moves) >= 5, "恶魔狼技能太少"
    # 属性字段必须是 skill_type_id，不是 type（type 恒为 1）
    by_name = {s.get("name"): s for _sid, s in moves}
    if "蝙蝠" in by_name:
        assert R.skill_type(by_name["蝙蝠"]) == 16, "蝙蝠应为恶属性"
    print("  data layer OK: species=%d skills=%d wolf_moves=%d" % (
        len(R.species()), len(R.skills()), len(moves)))


def test_damage_fit():
    cards = [
        {"i": 1, "power": 150, "shown": 142, "stab": 1.0},
        {"i": 2, "power": 100, "shown": 380, "stab": 1.0},
        {"i": 3, "power": 65, "shown": 154, "stab": D.STAB},
    ]
    k, pairs, rel = D.fit_k(cards)
    assert k is not None, "拟合失败"
    assert 1.7 < k < 2.1, "k 偏离标定值: %.3f" % k
    assert rel < 0.06, "拟合相对误差过大: %.3f" % rel
    mults = [m for _c, m in pairs]
    assert mults[0] == 0.5, "吞噬应为被克 0.5，实际 %s" % mults[0]
    assert mults[1] == 2.0, "火焰切割应为克制 2.0，实际 %s" % mults[1]
    # 每张卡的预测误差 <= 8%
    for c, m in pairs:
        pred = D.predict(c["power"], k, c["stab"], m)
        err = abs(pred - c["shown"]) / float(c["shown"])
        assert err <= 0.08, "卡%s 预测 %d vs 实示 %d" % (c["i"], pred, c["shown"])
    print("  damage fit OK: k=%.3f rel=%.3f mults=%s" % (k, rel, mults))


def test_mult_helpers():
    assert D.snap_mult(0.52) == 0.5
    assert D.snap_mult(1.9) == 2.0
    assert D.snap_mult(0.78) == 0.75
    assert D.stab_of([16], 16) == D.STAB
    assert D.stab_of([16], 1) == 1.0
    k2 = D.predict_for_pet(1.9, 218, 109)
    assert abs(k2 - 0.95) < 1e-6, "按攻击值缩放 k 不对"
    print("  helpers OK")


def test_matchup_db():
    tmp = os.path.join(tempfile.mkdtemp(), "test.db")
    conn = M.connect(tmp)
    M.import_rounds(conn)
    M.import_results(conn)
    s = M.summary(conn)
    assert s["matches"] > 0, "没有导入任何对局"
    # 重复导入不应翻倍
    M.import_rounds(conn)
    s2 = M.summary(conn)
    assert s2["matches"] == s["matches"], "重复导入导致对局翻倍"
    # 对位聚合
    M.update_matchup(conn, "恶魔狼", "圣剑-X", result="win", dealt=500, taken=200)
    M.update_matchup(conn, "恶魔狼", "圣剑-X", result="loss", dealt=300, taken=400)
    rows = M.matchup_stats(conn, our_pet="恶魔狼")
    assert rows and rows[0]["games"] == 2 and rows[0]["wins"] == 1
    assert abs(rows[0]["dmg_dealt"] - 800) < 1e-6
    conn.close()
    print("  matchup db OK: matches=%d turns=%d" % (s["matches"], s["turns"]))


if __name__ == "__main__":
    fails = 0
    for fn in (test_data_layer, test_damage_fit, test_mult_helpers, test_matchup_db):
        try:
            fn()
        except AssertionError as e:
            fails += 1
            print("  FAIL %s: %s" % (fn.__name__, e))
        except Exception as e:
            fails += 1
            print("  ERROR %s: %s: %s" % (fn.__name__, type(e).__name__, e))
    print("all smart tests pass" if not fails else "%d test(s) failed" % fails)
    sys.exit(1 if fails else 0)
