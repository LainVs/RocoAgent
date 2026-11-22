# -*- coding: utf-8 -*-
"""把「变化技能给的属性增益/减伤」整理成本地数据 -> data/external/rkpp-official/skill_buff_calc.json

为什么必须落盘（2026-11-22）
--------------------------
`simulator.skill_buff_pct()` 原来直接读

    C:/Users/Lenovo/Downloads/洛克PVP助手/.../native-data/authority/skill.json

的**绝对路径**。那个目录不在仓库里，一旦用户挪走/删掉，所有 buff 数值会
静默退化成中位数兜底（ATK_BUFF_MEDIAN）—— 数值全错，而且没有任何报错。
这和「data/external 是固定资产」的原则冲突，所以把**需要的那部分**抽出来
入库（源文件 1.1 MB，抽完只剩几十 KB）。

顺带补上原来缺的两类：
  · 防御增益（phyDefPercent / magDefPercent）—— 原来只取攻击
  · 减伤（damageTakenPercent）

用法：
    & $py scripts/build_skill_buffs.py
    & $py scripts/build_skill_buffs.py --src "D:/别的路径/native-data"
"""
import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "external", "rkpp-official",
                   "skill_buff_calc.json")
BUFF_MAP = os.path.join(ROOT, "data", "external", "rkpp-official",
                        "roco_buff_calc_map.json")
DEFAULT_SRC = (r"C:\Users\Lenovo\Downloads\洛克PVP助手\洛克PVP助手\app"
               r"\wwwroot\native-data\authority\skill.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC)
    args = ap.parse_args()
    if not os.path.exists(args.src):
        print("跳过：找不到 %s" % args.src)
        print("（换机器没有那个项目也能跑，只是变化技能的增益要退回中位数兜底）")
        return 0

    calc = json.load(io.open(BUFF_MAP, encoding="utf-8"))
    auth = json.load(io.open(args.src, encoding="utf-8"))

    out = {}
    n_power0 = 0
    for s in auth.values():
        dp = s.get("dam_para") or [0]
        if (dp[0] or 0) > 0:
            continue                      # 有伤害的技能不在这里管
        n_power0 += 1
        atk = dfn = taken = 0
        for e in (s.get("skill_result") or []):
            g = calc.get(str(e.get("effect_id")))
            if not g:
                continue
            atk = max(atk, g.get("phyAtkPercent") or 0,
                      g.get("magAtkPercent") or 0)
            dfn = max(dfn, g.get("phyDefPercent") or 0,
                      g.get("magDefPercent") or 0)
            taken = max(taken, g.get("damageTakenPercent") or 0)
        if atk or dfn or taken:
            out[s.get("name")] = {"atk": atk / 100.0, "def": dfn / 100.0,
                                  "taken": taken / 100.0}
    payload = {
        "source": "洛克PVP助手 native-data/authority/skill.json "
                  "+ roco_buff_calc_map.json",
        "note": "变化技能（dam_para<=0）的属性增益百分比；atk/def 是 0~1 "
                "的小数，taken 是受到的伤害降低比例",
        "power0_skills": n_power0,
        "count": len(out),
        "skills": out,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    print("写出 %s" % OUT)
    print("  变化技能 %d 个，其中 %d 个有可用的增益数值"
          % (n_power0, len(out)))
    for nm in ("力量增效", "防御", "有效预防", "超级电池", "冰捆缚"):
        v = out.get(nm)
        print("    %-8s %s" % (nm, v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
