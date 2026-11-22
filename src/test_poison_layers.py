# -*- coding: utf-8 -*-
"""S4.6：中毒 buff 与中毒印记分离生命周期 + 双层叠加（用户点名核对）。

原文口径：
  中毒（buff/减益）：回合末 3% 毒系伤害，毒系免疫；**换人清空**
  中毒印记：回合末 3% 毒系伤害；**换人保留**（队伍常驻）
  两者数值相同、生命周期不同 → 引擎分开存、分开结算，总毒伤 = 3%×(buff+印记)

回归点：
  1. 换人：buff 清空、印记保留
  2. 回合末双层叠加：印记 3% + buff 6%（2 层）= 9%
  3. 中毒无 halve（灼烧才有）；印记无衰减
"""
import sys

sys.path.insert(0, "src")
import simulator as SIM  # noqa: E402

d = SIM.Dex()
bad = 0


def check(cond, msg):
    global bad
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        bad += 1


print("--- 布景：a 侧挂印记，a[1] 带 buff ---")
bt = SIM.Battle(d, ["音速犬", "冰钻布鲁斯"], ["音速犬"], seed=0)
bt.begin_turn()
bt.add_mark("a", "poison_mark")     # 对方挂在我方队伍侧的印记
bt.a[1].status = {"poison": 2}      # 替补身上的中毒 buff
bt.step("switch", 0, "energy", None)  # 换上 a[1]（回合结束）
bt.begin_turn()

print("--- 1. 换人后状态 ---")
check(not bt.a[0].status, "下场精灵 buff 清空（换人清减益）")
check(bt.marks["a"].get("poison_mark") == 1, "a 侧印记保留（印记换人不消失）")
check(bt.a[1].status.get("poison") == 2, "上场精灵 buff 保留")

print()
print("--- 2. 回合末双层毒伤叠加 ---")
p = bt.me
hp0 = p.hp
ev = bt.step("energy", None, "energy", None)
want_mark = max(1, int(p.max_hp * 0.03))
want_buff = max(1, int(p.max_hp * 0.06))
lost = hp0 - p.hp
for e in ev:
    if "毒" in e:
        print("   ", e)
print("    总扣血 %d（印记 %d + buff %d = %d）"
      % (lost, want_mark, want_buff, want_mark + want_buff))
check(lost == want_mark + want_buff, "双层叠加正确（9%）")

print()
print("--- 3. 层数生命周期 ---")
check(p.status.get("poison") == 2, "中毒 buff 无 halve（2 层保留）")
check(bt.marks["a"].get("poison_mark") == 1, "印记 1 层保留（无衰减）")

print()
if bad:
    print("FAILED %d" % bad)
    sys.exit(1)
print("ALL PASS")
