# -*- coding: utf-8 -*-
"""split_robot2.py —— 把 robot.py 按职责拆成 6 个模块（第二版，含共享常量层）。

第一版漏了关键一层：各段代码都在用 robot.py 的模块级常量（TITLE / MY_BAR /
LOBBY_BTN / SKILL_BOX ...）和 log()。所以必须先把这一层抽出来。

拆分结构：
    config.py         常量 + 日志 + 单实例锁（所有人都依赖）
    winio.py          窗口 / 焦点 / 点击 / Screen
    perceive.py       词表 / 指纹 / 卡片判定 / Perc / 技能栏
    ui_probe.py       按钮 / VS 页 / 结算页 / 换人面板
    policy_legacy.py  旧 decide()
    robot.py          门面 + Bot 类 + 入口

用法：
    python scripts/split_robot2.py --dry
    python scripts/split_robot2.py --apply
"""
from __future__ import annotations

import io
import os
import sys

ROOT = r"D:\Projects\RocoAgent"
SRC = os.path.join(ROOT, "src", "robot.py")
ORIG = os.path.join(ROOT, "src", "robot.py.orig")

# 用备份的原始文件作为唯一输入，保证脚本可重复执行
SEG_CONFIG = (71, 225, "config.py", "常量 / 日志 / 单实例锁 / 崩溃留证")
SEGMENTS = [
    ("winio.py", 226, 517, "窗口查找 / 焦点 / 硬件点击 / Screen 截图与坐标换算"),
    ("perceive.py", 518, 1070, "词表 / 头像指纹 / 技能卡可放判定 / Perc 感知 / 技能栏解析"),
    ("ui_probe.py", 1071, 1307, "UI 探测：按钮 / VS 页 / 结算页 / 换人面板"),
    ("policy_legacy.py", 1308, 1525, "旧决策 decide()：治疗 / 聚能 / 最大伤害"),
]

# 每个模块需要从 config 拿哪些名字（用 ast 扫描的结果，人工确认过）
FROM_CONFIG = {
    "winio.py": ["LOG_DIR", "TITLE", "log"],
    "perceive.py": ["LOG_DIR", "MY_BAR", "FOE_BAR", "SKILL_BOX", "CNT_ROI",
                    "log", "species_vocab"],
    "ui_probe.py": ["LOBBY_BTN", "RESULT_BTN", "ACTION_BTN_BAND",
                    "CONFIRM_BAND", "RESULT_BAND", "RESULT_LIST_BAND",
                    "SKILL_BOX", "log"],
    "policy_legacy.py": ["HEAL_HINTS", "HEAL_BY_DASH", "HEAL_HP",
                         "HEAL_MAX_STREAK", "RETREAT_HP", "SWITCH_DMG"],
}

# 跨模块依赖
CROSS = {
    "winio.py": [],
    "perceive.py": ["from winio import Screen"],
    "ui_probe.py": ["from winio import Screen",
                    "from perceive import _is_skillbar"],
    "policy_legacy.py": [],
}

EXTRA = {
    "config.py": ["import atexit", "import os", "import sys", "import time",
                  "import paths"],
    "winio.py": ["import ctypes", "import ctypes.wintypes as wt", "import time",
                 "import mss", "import numpy as np", "import cv2"],
    "perceive.py": ["import time", "import numpy as np", "import cv2"],
    "ui_probe.py": ["import numpy as np", "import cv2"],
    "policy_legacy.py": ["import random", "import time"],
}


def main():
    apply = "--apply" in sys.argv
    if not os.path.exists(ORIG):
        print("缺少 %s（原始备份）" % ORIG)
        return 1

    lines = io.open(ORIG, encoding="utf-8").read().split("\n")
    print("原始 robot.py: %d 行" % len(lines))

    def block(a, b):
        return lines[a - 1:b]

    if not apply:
        print("\n  %-18s 行 %4d-%-4d  %s" % (SEG_CONFIG[2], SEG_CONFIG[0],
                                            SEG_CONFIG[1], SEG_CONFIG[3]))
        for f, a, b, d in SEGMENTS:
            print("  %-18s 行 %4d-%-4d  %s" % (f, a, b, d))
        print("\n(dry run)")
        return 0

    # ---- config.py
    a, b, _, desc = SEG_CONFIG
    cfg = ['# -*- coding: utf-8 -*-',
           '"""%s' % desc,
           '',
           '从 robot.py 拆出（第 %d-%d 行），纯搬移。' % (a, b),
           '所有模块共享这一层：常量、日志、单实例锁、崩溃留证。',
           '"""',
           'from __future__ import annotations', '']
    cfg += EXTRA["config.py"] + ['', '']
    cfg += block(a, b)
    io.open(os.path.join(ROOT, "src", "config.py"), "w",
            encoding="utf-8", newline="\n").write("\n".join(cfg))
    print("  wrote config.py (%d 行)" % len(cfg))

    # ---- 其他模块
    for fname, a, b, desc in SEGMENTS:
        head = ['# -*- coding: utf-8 -*-',
                '"""%s' % desc,
                '',
                '从 robot.py 拆出（第 %d-%d 行），纯搬移，逻辑未改。' % (a, b),
                '"""',
                'from __future__ import annotations', '']
        head += EXTRA.get(fname, [])
        names = FROM_CONFIG.get(fname, [])
        if names:
            head.append('')
            head.append('from config import (  # noqa: F401')
            for i in range(0, len(names), 4):
                head.append('    ' + ', '.join(names[i:i + 4]) + ',')
            head.append(')')
        head += CROSS.get(fname, [])
        head += ['', '']
        body = head + block(a, b)
        io.open(os.path.join(ROOT, "src", fname), "w",
                encoding="utf-8", newline="\n").write("\n".join(body))
        print("  wrote %s (%d 行)" % (fname, len(body)))

    # ---- 重建 robot.py：头部(1-70) + 占位 + Bot 类(1526+) + 门面
    out = block(1, 70)
    out.append('')
    out.append('# 常量 / 日志 / 锁 已移至 config.py；以下 re-export 保持兼容。')
    out.append('from config import *  # noqa: F401,F403')
    out.append('')
    out.append('# 感知与 IO 层已拆分，re-export 保持兼容。')
    out.append('from winio import *  # noqa: F401,F403')
    out.append('from perceive import *  # noqa: F401,F403')
    out.append('from ui_probe import *  # noqa: F401,F403')
    out.append('from policy_legacy import *  # noqa: F401,F403')
    out.append('')
    out.append('')
    out += block(1526, len(lines))

    io.open(SRC, "w", encoding="utf-8", newline="\n").write("\n".join(out))
    print("\n  robot.py 重建为 %d 行" % len(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
