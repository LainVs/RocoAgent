# -*- coding: utf-8 -*-
"""port_sim.py —— 把 NRC_AI 的效果引擎搬到 src/sim/。

只搬不改逻辑，唯一改动是把 `src.` 前缀换成 `sim.`。

搬什么
------
    models.py                   Pokemon / Skill / BattleState
    effect_models.py            E(197) / Timing(14) / SkillTiming(6)
    effect_engine.py            re-export 兼容层
    engine/_monolith.py         220 个 handler（3822 行）
    effect_data.py              手动配置（技能 139 + 特性 169）
    skill_effects_generated.py  自动生成（359 技能）
    skill_db.py                 技能加载 + 效果注入
    battle.py                   回合流程 + DamageCalculator

不搬什么
--------
    mcts.py       完美信息（get_actions 能看对手技能），P4 自己写 PIMC
    pokemon_db.py 属性数据错的（4/5 与 roco-cal 不一致），adapter 用 my_team
    server.py / main.py / team_builder.py    UI/入口

跑法：
    python scripts\\port_sim.py
    python scripts\\port_sim.py --dry
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "external", "nrc-ai", "src")
DST = os.path.join(ROOT, "src", "sim")

# (源相对路径, 目标相对路径)
FILES = [
    ("models.py", "models.py"),
    ("effect_models.py", "effect_models.py"),
    ("effect_engine.py", "effect_engine.py"),
    ("engine/__init__.py", "engine/__init__.py"),
    ("engine/_monolith.py", "engine/_monolith.py"),
    ("effect_data.py", "effect_data.py"),
    ("skill_effects_generated.py", "skill_effects_generated.py"),
    ("skill_db.py", "skill_db.py"),
    ("battle.py", "battle.py"),
]


def rewrite(text):
    """把 `src.` 换成 `sim.`。

    只替换真正的 import 语句，避免误伤注释里的 "src." 字样。
    """
    out = []
    for line in text.splitlines(keepends=True):
        s = line.lstrip()
        if s.startswith(("from src.", "import src.")):
            line = line.replace("from src.", "from sim.").replace(
                "import src.", "import sim.")
        out.append(line)
    return "".join(out)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(SRC):
        print("!! 找不到 %s" % SRC)
        return 1

    print("源: %s" % SRC)
    print("目标: %s" % DST)
    print()

    total_lines = 0
    total_subs = 0
    for src_rel, dst_rel in FILES:
        src = os.path.join(SRC, *src_rel.split("/"))
        dst = os.path.join(DST, *dst_rel.split("/"))
        if not os.path.exists(src):
            print("  !! 缺源文件 %s" % src_rel)
            return 1
        text = open(src, encoding="utf-8").read()
        new = rewrite(text)
        n = sum(1 for a, b in zip(text.splitlines(), new.splitlines())
                if a != b)
        lines = len(new.splitlines())
        total_lines += lines
        total_subs += n
        print("  %-30s %5d 行  改 %2d 处 import" % (dst_rel, lines, n))
        if not args.dry:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8", newline="") as f:
                f.write(new)

    # __init__.py
    init_path = os.path.join(DST, "__init__.py")
    init_text = '''# -*- coding: utf-8 -*-
"""sim —— 洛克王国战斗模拟器（效果引擎移植自 NRC_AI）。

来源：https://github.com/ColinHong10/NRC_AI （MIT License）
移植日期：2026-11-21
改动：只把 `src.` 前缀改成 `sim.`，未改任何逻辑。

详见 VERSION.md。
"""
'''
    if not args.dry:
        os.makedirs(DST, exist_ok=True)
        with open(init_path, "w", encoding="utf-8", newline="") as f:
            f.write(init_text)
    print("  %-30s (新建)" % "__init__.py")

    print()
    print("合计 %d 行，改写 %d 处 import" % (total_lines, total_subs))
    if args.dry:
        print("（--dry：未写入）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
