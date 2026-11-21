# -*- coding: utf-8 -*-
"""test_matches.py —— 场次 id / 三份日志对齐的回归测试。

为什么值得单独测
----------------
以前三份日志没有共同标识，3168 条回合里只有 45.6% 能唯一归属到某一场，
44.5% 落在多场重叠区（时间戳只有时分秒，没有日期）。这类问题**不会报错**，
只会让"这一场打得怎么样"永远算不出来 —— 属于沉默失效。

这里用合成数据把对齐规则钉死：
  · 有 mid 的三份记录必须能 join 上
  · 老记录（没有 mid）必须标 legacy，且**不能**被猜着塞进某一场
  · 同一场两条 result（段位卡 + 历史战绩）优先取可信的那条

用法：python src/test_matches.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import matches  # noqa: E402

fails = 0


def check(cond, name, detail=""):
    global fails
    if not cond:
        fails += 1
    print("  %-4s %-44s %s" % ("OK" if cond else "FAIL", name, detail))


def write(path, rows):
    with io.open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main():
    tmp = tempfile.mkdtemp()
    old = (matches.ROUNDS, matches.TURNS, matches.RESULTS, matches._CACHE)
    matches.ROUNDS = os.path.join(tmp, "rounds.jsonl")
    matches.TURNS = os.path.join(tmp, "turns.jsonl")
    matches.RESULTS = os.path.join(tmp, "results.jsonl")
    matches._CACHE = {"key": None, "data": None}

    MID = "20261121-093017"
    write(matches.ROUNDS, [
        # 老记录：没有 mid
        {"match": 1, "start": "09:00:00", "end": "09:02:00",
         "seconds": 120.0, "actions": 3},
        # 新记录
        {"mid": MID, "match": 2, "start": "09:30:17", "end": "09:33:00",
         "seconds": 163.0, "actions": 6},
    ])
    write(matches.TURNS, [
        {"mid": MID, "ts": "09:30:20", "turn": 1, "action": ["skill", 1],
         "why": "ok", "our_pet": "音速犬", "foe_pet": "水灵",
         "my_pct": 100.0, "foe_pct": 100.0,
         "skills": [{"i": 1, "v": 175, "t": "175"}]},
        {"mid": MID, "ts": "09:30:30", "turn": 2, "action": ["skill", 2],
         "why": "blind fallback: slot 2", "our_pet": "音速犬",
         "foe_pet": "水灵", "my_pct": 80.0, "foe_pct": 60.0,
         "skills": [{"i": 1, "v": None, "t": "1?5"},
                    {"i": 2, "v": 55, "t": "55"}]},
        # 没有 mid 的老回合：不应被塞进任何一场
        {"ts": "09:01:00", "turn": 1, "action": ["energy"],
         "why": "charge", "skills": []},
    ])
    write(matches.RESULTS, [
        # 先来一条段位卡的
        {"ts": "09:34:00", "before": 2, "after": 2, "of": 3,
         "delta": 0, "result": "flat", "mid": MID},
        # 再来一条历史战绩面板的（更可信）
        {"ts": "09:35:00", "before": 125, "after": 126,
         "before_losses": 155, "after_losses": 155,
         "total": 281, "delta": 1, "result": "win", "mid": MID},
    ])

    try:
        print("[1] mid 格式")
        m = matches.new_mid(1795230817.0)      # 2026-11-21 09:13:37 +0800
        check(len(m) == 15 and m[8] == "-", "new_mid 形如 YYYYMMDD-HHMMSS", m)
        check(matches.new_mid(0) != matches.new_mid(1),
              "不同秒的 mid 不同")
        check(matches.short_mid("20261121-093017") == "1121-0930",
              "short_mid 可读化", matches.short_mid("20261121-093017"))

        print()
        print("[2] summarize 的对齐")
        items = matches.summarize()
        check(len(items) == 2, "两场都列出来", str(len(items)))
        # 文件顺序倒序：新记录在前
        new, legacy = items[0], items[1]
        check(new["mid"] == MID, "第一条是新记录", new["mid"])
        check(new["legacy"] is False, "新记录 legacy=False")
        check(legacy["legacy"] is True, "老记录 legacy=True")

        print()
        print("[3] join 的内容")
        check(new["result"] == "win", "胜负取到", new["result"])
        check(new["result_src"] == "record",
              "优先用历史战绩那条（不是段位卡 flat）", new["result_src"])
        check(new["foe"] == "水灵", "对面精灵", new["foe"])
        check(new["our"] == "音速犬", "我方精灵", new["our"])
        check(new["decisions"] == 2, "回合去重", str(new["decisions"]))
        check(new["blind"] == 1, "盲打计数", str(new["blind"]))
        check(new["slots"] == 3 and new["slots_bad"] == 1,
              "技能槽可读率 2/3",
              "%d/%d" % (new["slots"] - new["slots_bad"], new["slots"]))

        print()
        print("[4] 老记录不能被猜着归属")
        check(legacy["decisions"] == 0,
              "老记录不挂任何回合（没有 mid 就不猜）",
              str(legacy["decisions"]))
        check(legacy["result"] == "unknown",
              "老记录没有胜负", legacy["result"])

        print()
        print("[5] match_detail")
        d = matches.match_detail(MID)
        check(d is not None, "能取到这一场")
        check(d["turn_rows"] == 2, "带回 2 条回合", str(d["turn_rows"]))
        check(d["result"]["result"] == "win", "详情里的胜负")
        check(matches.match_detail("不存在") is None, "不存在的 mid 返回 None")

        print()
        print("[6] 缓存按 (mtime, size) 失效")
        before = matches.summarize()
        with io.open(matches.RESULTS, "a", encoding="utf-8") as f:
            f.write(json.dumps({"mid": MID, "ts": "09:36:00",
                                "result": "win", "delta": 1,
                                "before": 1, "after": 2, "of": 3},
                               ensure_ascii=False) + "\n")
        after = matches.summarize()
        check(after[0]["result"] == "win", "追加后仍能读到最新")
        check(len(after) == len(before), "不会重复计数")

    finally:
        matches.ROUNDS, matches.TURNS, matches.RESULTS, matches._CACHE = old
        try:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass

    print()
    if fails:
        print("%d FAILURE(S)" % fails)
        return 1
    print("all matches cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
