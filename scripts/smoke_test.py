# -*- coding: utf-8 -*-
"""smoke_test.py —— 运行时冒烟测试。

`py_compile` 只检查语法，查不出"拆分时漏了某个名字"这类错误
（2026-11-20 实际发生过：HEAL_BY_DASH / _log_exception 丢失，
py_compile 通过但机器人启动即崩）。

本脚本做三件事：
  1. 逐个 import 所有运行时模块
  2. 对 robot 门面做名字可达性检查（外部脚本用到的每一个名字）
  3. 实例化关键对象 / 调用纯函数，确保没有 NameError

用法：python scripts\\smoke_test.py
"""
from __future__ import annotations

import os
import sys
import traceback

ROOT = r"D:\Projects\RocoAgent"
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)
sys.path.insert(0, str(__import__("paths").TOOLS)
               if False else SRC)

# 运行时模块（robot 依赖链）
MODULES = [
    "config", "winio", "perceive", "ui_probe", "policy_legacy",
    "statmodel", "damage", "foe_hp", "pvp_engine", "policy_v2",
    "digitmat", "matdet", "ocr_cn", "hud", "energy",
    "portraits", "portrait_db", "foe_panel", "ability_read", "trait_db",
    "foe_moves", "team_db", "matchup_db", "rank", "record",
    "screen_state", "state_bus", "robot",
]

# 外部脚本实际用到的 robot.* 名字（从 AST 扫描得来，手工核对过）
ROBOT_NAMES = [
    "Bot", "Screen", "Perc", "decide", "log", "LOG_DIR", "SKILL_BOX",
    "find_game", "find_action_button", "find_switch_rows",
    "has_start_button", "_card_lit", "_read_bar", "_is_skillbar",
    "_plate_p75", "_slots", "_log_exception", "acquire_lock",
]

# 纯函数冒烟：调用后不应抛异常
PURE_CALLS = [
    ("statmodel.calc_real_stat", lambda: __import__("statmodel").calc_real_stat(85, 60, 1.0, True)),
    ("statmodel.type_effect", lambda: __import__("statmodel").type_effect("草", ["水", "地"])),
    ("statmodel.defense_profile", lambda: __import__("statmodel").defense_profile(["水"])),
    ("foe_hp.max_hp", lambda: __import__("foe_hp").max_hp("音速犬")),
    ("foe_hp.incoming", lambda: __import__("foe_hp").incoming("岚鸟", "闪击", "恶魔狼")),
    ("pvp_engine.best_damage_from", lambda: __import__("pvp_engine").best_damage_from("音速犬", "水灵")),
    ("pvp_engine.max_hp", lambda: __import__("pvp_engine").max_hp("音速犬")),
    ("policy_legacy._heal_slot", lambda: __import__("policy_legacy")._heal_slot(
        {"skills": [{"kind": "dash"}, {"kind": "num"},
                    {"kind": "dash"}, {"kind": "dash"}]})),
    ("team_db.dump_team", lambda: __import__("team_db").dump_team()),
]


def main():
    fails = 0

    print("=== 1. 模块 import ===")
    for m in MODULES:
        try:
            __import__(m)
            print("  %-18s OK" % m)
        except Exception as e:
            fails += 1
            print("  %-18s FAIL %s: %s" % (m, type(e).__name__, e))

    print("\n=== 2. robot 门面名字可达 ===")
    try:
        import robot
        missing = [n for n in ROBOT_NAMES if not hasattr(robot, n)]
        if missing:
            fails += len(missing)
            print("  缺少: %s" % ", ".join(missing))
        else:
            print("  全部 %d 个名字可访问" % len(ROBOT_NAMES))
    except Exception as e:
        fails += 1
        print("  import robot FAIL: %s" % e)

    print("\n=== 3. 纯函数冒烟 ===")
    for name, fn in PURE_CALLS:
        try:
            r = fn()
            print("  %-32s -> %r" % (name, r))
        except Exception as e:
            fails += 1
            print("  %-32s FAIL %s: %s" % (name, type(e).__name__, e))

    print("\n=== 4. Bot 可构造（不连硬件）===")
    try:
        import robot

        class _Args:
            no_guard = True
            no_policy_v2 = False
            no_foe_scan = True
            no_input = True
            seconds = 0
            log = False

        # 只验证类定义完整（不实例化：__init__ 会连 BLE）
        assert hasattr(robot.Bot, "_battle_tick")
        assert hasattr(robot.Bot, "_idle_tick")
        assert hasattr(robot.Bot, "run")
        print("  Bot 类方法齐全 OK")
    except Exception as e:
        fails += 1
        print("  FAIL %s: %s" % (e, traceback.format_exc()[-300:]))

    print("\n%s" % ("ALL OK" if fails == 0 else "%d 处失败" % fails))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
