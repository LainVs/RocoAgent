# -*- coding: utf-8 -*-
"""test_live_state.py —— 验证 live_state 契约与前端 API 的端到端一致性。

不需要游戏、不需要机器人。跑法：
    python src/test_live_state.py

它做三件事：
  1. 用假数据组装一份状态，写入 logs/live_state.json
  2. 校验字段类型符合 docs/FRONTEND.md §三 的契约
  3. 校验"未知就是 None"，绝不出现 0 冒充未知
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import live_state as LS

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
        print("  FAIL  %s" % msg)
    return cond


def main():
    print("=" * 66)
    print("live_state 契约验证")
    print("=" * 66)

    # ---------- 1. 完整状态
    print("\n[1] 完整状态（双方都已知）")
    state = LS.build(
        turn=12, clock=8, mode="battle",
        me=LS.me_block("寂灭骨龙", ["龙"], hp_pct=78.4, hp_abs=382, hp_max=490,
                       energy=6, energy_max=10,
                       buffs=[{"name": "物攻", "layers": 1}]),
        foe=LS.foe_block("水灵", ["水"], hp_pct=45.2,
                         hp_range=(349, 419), def_range=(178, 231),
                         nature="固执", nature_conf=0.62),
        skills=[
            LS.skill_block(1, "隼鳞", "龙", 3, dmg=245, pct=58.0),
            LS.skill_block(2, "龙之怒", "龙", 2, dmg=186, pct=44.0),
            LS.skill_block(3, "碎岩", "地", 1, dmg=92, pct=22.0),
            LS.skill_block(4, "龙舞", "龙", 3, dmg=None, pct=None,
                           usable=False, note="强化"),
        ],
        matchup={"out": 3.0, "in": 0.5, "tag": "双重克制"},
        verdict={"kill_turns": 2, "die_turns": 4, "order": "first"},
        system=LS.system_block(fps_ms=4.2,
                               detectors={"bar": True, "skillbar": True,
                                          "countdown": True}),
    )

    check(state["mode"] == "battle", "mode")
    check(state["turn"] == 12, "turn")
    check(state["me"]["hp_pct"] == 78.4, "me.hp_pct 保留一位小数")
    check(state["me"]["hp_max"] == 490, "me.hp_max")
    check(len(state["skills"]) == 4, "skills 有 4 条")
    check(state["skills"][3]["dmg"] is None, "不可用技能的 dmg 是 None 不是 0")
    check(state["skills"][3]["usable"] is False, "usable 标记")
    check(state["foe"]["hp_range"] == [349, 419], "hp_range 转 list")
    check(state["matchup"]["out"] == 3.0, "matchup.out")

    ok = LS.publish(state, force=True)
    check(ok, "publish 成功")
    check(os.path.exists(LS.PATH), "文件已写入")

    with open(LS.PATH, encoding="utf-8") as f:
        back = json.load(f)
    check(back["seq"] == state["seq"], "往返后 seq 一致")
    check(back["me"]["name"] == "寂灭骨龙", "往返后名字一致")

    # ---------- 2. 未知字段
    print("\n[2] 未知状态（机器人刚启动，什么都还没读到）")
    s2 = LS.build(turn=None, mode="idle",
                  me=LS.me_block(None, [], None, None, None, None),
                  foe=LS.foe_block(None, [], None))
    check(s2["me"]["name"] is None, "未知名字是 None")
    check(s2["me"]["hp_pct"] is None, "未知血量是 None，不是 0")
    check(s2["me"]["hp_abs"] is None, "未知绝对值是 None")
    check(s2["foe"]["hp_pct"] is None, "敌方未知血量是 None")
    check(s2["turn"] is None, "未知回合是 None")

    # ---------- 3. 边界值
    print("\n[3] 边界值")
    s3 = LS.build(me=LS.me_block("x", [], hp_pct=150.0),
                  foe=LS.foe_block("y", [], hp_pct=-5.0))
    check(s3["me"]["hp_pct"] == 100.0, "血量上限夹到 100")
    check(s3["foe"]["hp_pct"] == 0.0, "血量下限夹到 0")

    # ---------- 4. 原子写
    print("\n[4] 原子写（不留 .tmp）")
    LS.publish(state, force=True)
    check(not os.path.exists(LS.PATH + ".tmp"), "没有残留 .tmp")

    # ---------- 5. 节流
    print("\n[5] 节流（MIN_INTERVAL=0.08s）")
    LS.publish(state, force=True)
    r1 = LS.publish(state)          # 立刻再写，应该被拒
    check(r1 is False, "间隔太短的写入被拒绝")
    import time
    time.sleep(0.1)
    r2 = LS.publish(state)
    check(r2 is True, "过了间隔后允许写入")

    # ---------- 6. 清理
    print("\n[6] clear()")
    LS.clear()
    check(not os.path.exists(LS.PATH), "clear 后文件不存在")

    # ---------- 结果
    print()
    print("=" * 66)
    if FAILS:
        print("%d 项失败" % len(FAILS))
        for f in FAILS:
            print("  - %s" % f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
