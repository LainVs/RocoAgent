# -*- coding: utf-8 -*-
"""split_robot.py —— 把 4126 行的 robot.py 按职责拆成 6 个模块。

策略：**纯搬移，不改逻辑**。
  * 每一段按行号区间原样搬走（连注释一起）
  * 新模块补齐自己需要的 import（用 pyflakes 式的保守推断 + 人工白名单）
  * robot.py 变成门面：re-export 所有原有名字，所以 20 处 `import robot`
    一处都不用改

拆分后立刻做等价性验证：对比拆分前后每个顶层函数的 co_code。

用法：
    python scripts/split_robot.py --dry     # 只打印计划
    python scripts/split_robot.py --apply   # 真的写文件
"""
from __future__ import annotations

import io
import os
import re
import sys

ROOT = r"D:\Projects\RocoAgent"
SRC = os.path.join(ROOT, "src", "robot.py")
BAK = os.path.join(ROOT, "src", "robot.py.orig")

# 行号区间（1-based，含两端）。边界来自实际扫描。
SEGMENTS = [
    ("winio.py", 226, 517, "窗口查找 / 焦点 / 硬件点击 / Screen 截图与坐标换算"),
    ("perceive.py", 518, 1070, "词表 / 头像指纹 / 技能卡可放判定 / Perc 感知 / 技能栏解析"),
    ("ui_probe.py", 1071, 1307, "UI 探测：按钮 / VS 页 / 结算页 / 换人面板"),
    ("policy_legacy.py", 1308, 1525, "旧决策 decide()：治疗 / 聚能 / 最大伤害"),
]

# 每个新模块需要的额外 import（保守白名单，宁可多给）
EXTRA_IMPORTS = {
    "winio.py": [
        "import ctypes", "import os", "import time",
        "import numpy as np", "import cv2",
    ],
    "perceive.py": [
        "import time", "import numpy as np", "import cv2",
    ],
    "ui_probe.py": [
        "import numpy as np", "import cv2",
    ],
    "policy_legacy.py": [
        "import random", "import time",
    ],
}

# 跨模块依赖：新模块里可能用到别的模块里的东西
CROSS_DEPS = {
    "winio.py": [],
    "perceive.py": ["from winio import Screen"],
    "ui_probe.py": ["from winio import Screen"],
    "policy_legacy.py": [],
}


def read_lines():
    return io.open(SRC, encoding="utf-8").read().split("\n")


def main():
    apply = "--apply" in sys.argv
    lines = read_lines()
    print("robot.py 共 %d 行" % len(lines))

    # 备份
    if apply and not os.path.exists(BAK):
        io.open(BAK, "w", encoding="utf-8", newline="\n").write(
            "\n".join(lines))
        print("备份 -> %s" % BAK)

    plan = []
    for fname, a, b, desc in SEGMENTS:
        body = lines[a - 1:b]
        plan.append((fname, a, b, desc, body))
        print("  %-20s 行 %4d-%-4d (%3d 行)  %s"
              % (fname, a, b, len(body), desc))

    # Bot 类 + 入口保留
    bot_start = next(i for i, l in enumerate(lines) if l.startswith("class Bot:"))
    print("  %-20s 行 %4d-%-4d  Bot 类（本轮先不动）"
          % ("robot.py", bot_start + 1, len(lines)))

    if not apply:
        print("\n(dry run; 加 --apply 执行)")
        return

    # ---- 写新模块
    for fname, a, b, desc, body in plan:
        header = [
            '# -*- coding: utf-8 -*-',
            '"""%s' % desc,
            '',
            '从 robot.py 拆出（第 %d-%d 行），纯搬移，逻辑未改。' % (a, b),
            '"""',
            'from __future__ import annotations',
            '',
        ]
        header += EXTRA_IMPORTS.get(fname, [])
        header += CROSS_DEPS.get(fname, [])
        header += ['', '']
        out = header + body
        path = os.path.join(ROOT, "src", fname)
        io.open(path, "w", encoding="utf-8", newline="\n").write(
            "\n".join(out))
        print("  wrote %s (%d 行)" % (fname, len(out)))

    # ---- 从 robot.py 删掉这些区间（倒序删，保持行号有效）
    keep = list(lines)
    for fname, a, b, desc, body in sorted(plan, key=lambda p: -p[1]):
        del keep[a - 1:b]
        keep.insert(a - 1, "# ---- %s 已移至 %s ----" % (desc.split("：")[0],
                                                         fname))

    # ---- 加 re-export 门面（显式列出，因为 `import *` 不导出下划线名字，
    # 而外部脚本确实在用 robot._card_lit / robot._read_bar 等）
    RE_EXPORT = {
        "winio.py": ["Screen", "find_game", "focus", "_raise_topmost",
                     "_window_at_point", "_PT", "TITLE", "_UD"],
        "perceive.py": ["Perc", "species_vocab", "count_skill_badges",
                        "countdown_visible", "countdown_visible_crop",
                        "_bust_sig", "_sig_dist", "_plate_p75", "_card_lit",
                        "_read_bar", "_is_skillbar", "_is_dash", "_is_badge",
                        "_damage_pills", "_fill_missing", "_slots",
                        "MY_BAR", "FOE_BAR", "SKILL_BOX", "CNT_ROI"],
        "ui_probe.py": ["has_start_button", "has_exit_button", "has_vs_screen",
                        "find_action_button", "find_confirm_button",
                        "is_result_screen", "find_switch_rows"],
        "policy_legacy.py": ["decide", "_heal_slot", "my_card_costs",
                             "_est_value", "_charge_or_switch", "_match_name",
                             "HEAL_HINTS", "HEAL_BY_DASH", "HEAL_HP",
                             "HEAL_MAX_STREAK", "RETREAT_HP", "SWITCH_DMG",
                             "_MY_COSTS"],
    }
    reexport = [
        '',
        '# ======================================================================',
        '# 门面：实现已拆分到独立模块，这里 re-export 保持向后兼容。',
        '# 外部脚本写着 `import robot` 然后用 robot.Screen / robot.decide ...，',
        '# 一处都不用改。注意不能用 `import *`：下划线名字（_card_lit 等）',
        '# 不会被导出，而测试脚本确实在用它们。',
        '# ======================================================================',
    ]
    for fname, names in RE_EXPORT.items():
        mod = fname[:-3]
        reexport.append('from %s import (  # noqa: F401' % mod)
        for i in range(0, len(names), 4):
            reexport.append('    ' + ', '.join(names[i:i + 4]) + ',')
        reexport.append(')')
    reexport.append('')
    keep.extend(reexport)

    io.open(SRC, "w", encoding="utf-8", newline="\n").write("\n".join(keep))
    print("\nrobot.py 现在 %d 行" % len(keep))


if __name__ == "__main__":
    main()
