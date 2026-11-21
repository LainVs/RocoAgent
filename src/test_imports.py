# -*- coding: utf-8 -*-
"""test_imports.py —— 验证 robot.py 的关键导入没被编辑误删。

背景（2026-11-21 08:36 事故）：
    给 config 导入加 `STALL_EXIT_S` 时，替换掉了这一行原有的
    `SWITCH_ROW_ABS, log, acquire_lock, _stdout_is_log, _log_exception`，
    导致 robot 启动即崩：

        NameError: name 'acquire_lock' is not defined
        NameError: name '_log_exception' is not defined

    botsup 连续重启 5 次全部 5 秒内退出（crash loop x5），
    机器人完全无法启动。

这类错误编译器查不出来（名字只在运行时解析），必须有测试兜住。

跑法：
    python src\\test_imports.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


# robot.py 从 config 导入、且在模块级别被引用的名字。
# 加新的模块级名字时同步补到这里。
ROBOT_NEEDS = [
    "acquire_lock",        # main() 里调用，少了直接崩
    "_log_exception",      # 异常兜底，少了崩在 except 里
    "_stdout_is_log",      # 日志重定向判断
    "log",                 # 到处在用
    "SWITCH_ROW_ABS",      # 换人面板坐标
    "STALL_EXIT_S",        # 看门狗阈值（2026-11-21 新增）
    "STUCK_S",
    "POLL_S",
    "SKILL_BOX",
    "MY_BAR",
    "FOE_BAR",
    "TURN_LOG",
    "LOCK_PATH",
]

# 其他模块的关键符号
OTHER_MODULES = {
    "config": ["acquire_lock", "STALL_EXIT_S", "STUCK_S", "LOCK_PATH"],
    "skill_kind": ["classify", "is_buff", "is_copy", "KIND_COPY",
                   "KIND_SELF_BUFF"],
    "skill_reader": ["SkillStabilizer"],
    "opponent_model": ["build", "build_from_actions", "load_actions",
                       "_norm_me", "_is_dummy", "ALIASES", "DUMMY_FOES"],
    "simulator": ["Dex", "run_match", "GreedyPolicy", "BuffPolicy",
                  "SmartPolicy", "ATK_BUFF_MEDIAN"],
    "policy_v2": ["decide"],
}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print("=== robot.py 的模块级名字 ===")
    try:
        import robot
    except Exception as e:
        print("  FAIL 导入 robot 失败: %s: %s" % (type(e).__name__, e))
        sys.exit(1)
    for name in ROBOT_NEEDS:
        check(hasattr(robot, name), "robot.%s 存在" % name)

    print()
    print("=== 其他模块的关键符号 ===")
    import importlib
    for mod, names in OTHER_MODULES.items():
        try:
            m = importlib.import_module(mod)
        except Exception as e:
            check(False, "%s 导入失败: %s" % (mod, e))
            continue
        missing = [n for n in names if not hasattr(m, n)]
        check(not missing,
              "%s 的 %d 个符号齐全%s" % (
                  mod, len(names),
                  "" if not missing else "（缺 %s）" % missing))

    print()
    if FAILS:
        print("FAILED %d:" % len(FAILS))
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
