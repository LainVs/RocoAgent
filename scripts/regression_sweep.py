# -*- coding: utf-8 -*-
"""Full regression sweep of every test touched today, to certify the repo
state before closing the session. Exit code summary printed at the end."""
import os, subprocess, sys

ROOT = r"D:\Projects\RocoAgent"
PY = r"C:\Users\Lenovo\Desktop\hardwork\CV_ESP_Game_1\.venv\Scripts\python.exe"
tests = [
    "test_imports.py",        # 符号完整性（编辑导入语句的安全网）
    "test_statmodel.py",      # 数值模型回归
    "test_damage_model.py",   # 伤害模型 + outgoing 新接口
    "test_battle_fx.py",      # 印记/天气新口径
    "test_policy.py",         # policy_v2 历史回归
    "test_policy_switch.py",  # 决策矩阵（KO 幻想锁）
    "test_decide.py",         # legacy decide 回归
    "test_priority.py",       # 先手规则
    "test_skill_names.py",    # 技能词表
    "test_skill_kind.py",     # 效果分类
    "test_policy_cost.py",    # 耗能查表
    "test_session_moves.py",  # 三层链
    "test_cast_route.py",     # 播报分流
    "test_avatar_ident.py",   # 头像通道
    "test_skill_pill_boxes.py",  # 名字带 ROI
    "test_standalone.py",     # 独立计算器
    "test_perceive.py",       # 感知新旧对比
    "test_record.py",         # 战绩读取
]
passed, failed, skip = [], [], []
for t in tests:
    path = os.path.join(ROOT, "src", t)
    if not os.path.exists(path):
        skip.append(t)
        continue
    r = subprocess.run([PY, path], cwd=ROOT, capture_output=True, timeout=900)
    out = (r.stdout or b"").decode("utf-8", "replace")
    ok = r.returncode == 0
    tag = "PASS" if ok else "FAIL(%d)" % r.returncode
    print("%-26s %s" % (t, tag))
    if ok:
        passed.append(t)
    else:
        failed.append(t)
        print("   stderr tail:", (r.stderr or b"").decode("utf-8",
                                                         "replace")[-300:])
print("\n%d passed, %d failed, %d skipped" % (len(passed), len(failed),
                                              len(skip)))
sys.exit(1 if failed else 0)
