# -*- coding: utf-8 -*-
"""S8 血脉/愿力冲击/伤害类别 测试。

独立脚本，`if __name__ == "__main__"` 自跑（仓库无 pytest）：
    & $py src\test_s8_blood.py
末行打 ALL PASS 或 FAILED N，失败退出码 1。

覆盖：
  1. damage_type 四类（1 变化 / 2 物理 / 3 魔法 / 4 动态双攻取高）
     —— rkpp damage_type 与 roco-cal 物攻/魔攻 346 技零冲突
  2. 血脉门控（一种血脉一个技能，blood 行 lv15）
  3. 愿力强化共鸣魔法（替换第一格、冷却 3、每场 2 次）
  4. 应对状态威力 +150%（愿力冲击反制状态技）
  5. 裁决/滋养/点燃/净化（克制伤害 → 攻防速+20%/层、能量+2、
     第一格替换为该系愿力冲击）
"""
import sys

sys.path.insert(0, "src")
import simulator as SIM  # noqa: E402
import statmodel as S  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  \u2713 " if cond else "  \u2717 ") + msg)
    if not cond:
        FAILS.append(msg)


def stub(**kw):
    base = dict(trait="", name="\u6d4b", moves=[], hp=500, max_hp=500,
                alive=True, status={}, atk_buff=0.0, def_buff=0.0,
                taken_mult=1.0, energy=5, trait_atk_buff=0.0,
                trait_def_buff=0.0, trait_tmp_atk=0.0, trait_tmp_def=0.0,
                nrc_power_mult=1.0, nrc_power_bonus=0,
                atk=100, spa=100, dfn=100, spd=100, spe=100, types=[])
    base.update(kw)
    return type("P", (), base)()


def main():
    d = SIM.Dex()

    print("--- 1. damage_type \u56db\u7c7b ---")
    check(d.resolve_skill(7100150)["damage_type"] == 3,
          "\u5347\u9f99\u5486\u54ee dt=3\uff08\u9b54\u653b\uff0croco-cal \u786e\u8ba4\uff09")
    check(d.resolve_skill(7000320)["damage_type"] == 1,
          "\u9632\u5fa1 dt=1\uff08\u53d8\u5316\u6280\uff09")

    pa = stub(atk=200, spa=300)
    pd = stub(dfn=100, spd=100)
    mv_p = {"id": 0, "name": "p", "power": 50, "cost": 1, "type": "\u666e\u901a",
            "damage_type": 2}
    mv_m = dict(mv_p, damage_type=3)
    check(SIM.calc_damage(pa, pd, mv_m) > SIM.calc_damage(pa, pd, mv_p),
          "\u9b54\u653b\u8def\u5f84\u7528 spa\uff08spa300 > atk200\uff09")
    pd2 = stub(dfn=100, spd=300)
    check(SIM.calc_damage(pa, pd2, mv_m) < SIM.calc_damage(pa, pd, mv_m),
          "\u9b54\u653b\u53d7\u9b54\u6297\uff08spd\uff09\u5f71\u54cd")
    # dt=4 双攻取高：spa 高 → 与 dt=3 同输入等伤
    mv4 = dict(mv_p, power=80, damage_type=4)
    check(SIM.calc_damage(pa, pd, mv4) ==
          SIM.calc_damage(pa, pd, dict(mv4, damage_type=3)),
          "dt=4 spa \u66f4\u9ad8\u65f6\u8d70\u9b54\u653b\u8def\u5f84")
    pa2 = stub(atk=400, spa=300)
    check(SIM.calc_damage(pa2, pd, dict(mv4, damage_type=2)) ==
          SIM.calc_damage(pa2, pd, mv4),
          "dt=4 atk \u66f4\u9ad8\u65f6\u8d70\u7269\u653b\u8def\u5f84")

    print("--- 2. \u8840\u8109\u95e8\u63a7 ---")
    DIMO = "\u8fea\u83ab"
    blood = d.blood_skills(d.species(DIMO)["id"])
    check(len(blood) == 18, "\u8fea\u83ab 18 \u7cfb\u8840\u8109\u6280\u80fd")
    names = [m["name"] for m in SIM.Battle(
        d, [DIMO], ["\u97f3\u901f\u72ac"], seed=0,
        cfg_a=[{"name": DIMO, "iv": 60, "bloodline": "\u706b",
                "moves": ["\u706b\u7130\u51b2\u950b", "\u82b1\u9999"]}],
        cfg_b=[{"name": "\u97f3\u901f\u72ac", "iv": 60}]).me.moves]
    check("\u706b\u7130\u51b2\u950b" in names,
          "\u706b\u8840\u8109\u53ef\u5b66\u706b\u7cfb\u8840\u8109\u6280")
    check("\u82b1\u9999" not in names,
          "\u8349\u7cfb\u8840\u8109\u6280\u88ab\u95e8\u63a7\u5254\u9664")
    bt2 = SIM.Battle(d, [DIMO], ["\u97f3\u901f\u72ac"], seed=0,
                     cfg_a=[{"name": DIMO, "iv": 60,
                             "moves": ["\u82b1\u9999"]}],
                     cfg_b=[{"name": "\u97f3\u901f\u72ac", "iv": 60}])
    check("\u82b1\u9999" not in [m["name"] for m in bt2.me.moves],
          "\u65e0\u8840\u8109\u2192\u8349\u7cfb\u8840\u8109\u6280\u4e0d\u53ef\u7528")

    print("--- 3. \u613f\u529b\u5f3a\u5316 ---")
    mv_fire = d.yuanli_move(1)
    check(mv_fire and mv_fire["type"] == "\u706b"
          and mv_fire["power"] == 80 and mv_fire["damage_type"] == 4,
          "\u706b\u7cfb\u613f\u529b\u51b2\u51fb\uff08\u6309 name+type_id \u67e5\u8868\uff09")
    bt3 = SIM.Battle(d, ["\u97f3\u901f\u72ac"], ["\u97f3\u901f\u72ac"],
                     seed=0,
                     cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "bloodline": "\u706b", "max_hp": 9999,
                             "moves": ["\u62cd\u51fb"]}],
                     cfg_b=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999, "moves": ["\u62cd\u51fb"]}])
    bt3.begin_turn()
    ev = bt3.step("magic", 0, "skill", 0)
    check(any("\u613f\u529b\u5f3a\u5316" in e for e in ev),
          "\u613f\u529b\u5f3a\u5316\u89e6\u53d1")
    check(any("\u613f\u529b\u51b2\u51fb" in e and "\u2192" in e for e in ev),
          "\u5f53\u56de\u5408\u51fa\u624b\u613f\u529b\u51b2\u51fb")
    check(bt3.magic_state["a"]["used"] == 1
          and bt3.magic_state["a"]["cd"] == 3, "\u6b21\u6570/CD \u8bb0\u5f55")
    bt3.begin_turn()
    ev3b = bt3.step("magic", 0, "skill", 1)
    check(any("\u51b7\u5374" in e for e in ev3b), "CD \u671f\u4e0d\u53ef\u518d\u7528")

    print("--- 4. \u5e94\u5bf9\u72b6\u6001\u5a01\u529b +150% ---")
    bt4 = SIM.Battle(d, ["\u97f3\u901f\u72ac"], ["\u97f3\u901f\u72ac"],
                     seed=0,
                     cfg_a=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "bloodline": "\u706b", "max_hp": 9999,
                             "moves": ["\u62cd\u51fb"]}],
                     cfg_b=[{"name": "\u97f3\u901f\u72ac", "iv": 60,
                             "max_hp": 9999,
                             "moves": ["\u529b\u91cf\u589e\u6548"]}])
    bt4.begin_turn()
    ev4 = bt4.step("magic", 0, "skill", 0)
    check(any("\u5e94\u5bf9\u6210\u529f" in e and "\u72b6\u6001" in e
              for e in ev4),
          "\u5e94\u5bf9\u72b6\u6001\u547d\u4e2d\uff08\u5bf9\u65b9\u72b6\u6001\u6280\uff09")
    check(any("\u5e94\u5bf9\u72b6\u6001\u5a01\u529b" in e for e in ev4),
          "\u5e94\u5bf9\u5a01\u529b +150% \u64ad\u62a5")

    print("--- 5. \u88c1\u51b3/\u6ecb\u517b/\u70b9\u71c3/\u51c0\u5316 ---")
    check(set(SIM.YUANLI_TRAITS) == {"\u88c1\u51b3", "\u6ecb\u517b",
                                     "\u70b9\u71c3", "\u51c0\u5316"},
          "4 \u6761\u7279\u6027\u6ce8\u518c\uff08\u514917/\u83493/\u706b1/\u6c342\uff09")
    # 圣光迪莫（裁决）+ 光血脉 折线冲击 vs 光克制的系
    foe_type = None
    for tid in range(18):
        tn = d.db.execute("SELECT name FROM types WHERE id=?",
                          (tid,)).fetchone()["name"]
        if S.type_effect("\u5149", [tn]) > 1.0:
            foe_type = tn
            break
    rows = d.db.execute(
        "SELECT name FROM species WHERE implemented=1 AND total>0 "
        "AND type_names LIKE ? LIMIT 3", ('%"' + foe_type + '"%',
                                          )).fetchall()
    if rows:
        foe = rows[0]["name"]
        bt5 = SIM.Battle(d, ["\u5723\u5149\u8fea\u83ab"], [foe], seed=0,
                         cfg_a=[{"name": "\u5723\u5149\u8fea\u83ab",
                                 "iv": 60, "max_hp": 9999,
                                 "bloodline": "\u5149",
                                 "moves": ["\u6298\u7ebf\u51b2\u51fb"]}],
                         cfg_b=[{"name": foe, "iv": 60, "max_hp": 9999}])
        bt5.begin_turn()
        a0, e0 = bt5.me.trait_atk_buff, bt5.me.energy
        ev5 = bt5.step("skill", 0, "energy", None)
        if getattr(SIM.calc_damage, "last_type_eff", 1.0) > 1.0:
            check(any("\u88c1\u51b3" in e for e in ev5),
                  "\u88c1\u51b3\u89e6\u53d1\uff08\u514b\u5236\u4f24\u5bb3\uff09")
            check(bt5.me.trait_atk_buff > a0, "\u653b\u9632 +20%")
            _cost = int(bt5.me.moves[0].get("cost") or 0)
            check(bt5.me.energy == e0 - _cost + 2,
                  "\u80fd\u91cf +2\uff08\u6263\u8017\u80fd %d\uff09" % _cost)
            check(bt5.me._yuanli_override == 17,
                  "\u7b2c\u4e00\u683c\u66ff\u6362\u4e3a\u5149\u7cfb\u613f\u529b")
            bt5.begin_turn()
            ev5b = bt5.step("skill", 0, "energy", None)
            check(any("\u613f\u529b\u51b2\u51fb" in e and "\u2192" in e
                      for e in ev5b),
                  "\u4e0b\u56de\u5408\u7b2c\u4e00\u683c\u51fa\u624b\u613f\u529b\u51b2\u51fb")
        else:
            print("  \uff08\u514b\u5236\u672a\u6210\u7acb\uff0c\u8df3\u8fc7\uff09")

    print()
    if FAILS:
        print("FAILED %d" % len(FAILS))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
