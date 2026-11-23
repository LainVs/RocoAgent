# -*- coding: utf-8 -*-
"""test_band_vocab.py —— BAND 两级词表回归。

## 锁住什么

`my_team` 配置必须**优先于** `species_skills` 当 BAND 的候选词表。

## 实机铁证（2026-11-23 08:0x）

`--band-dump` 落盘图 `band_卡瓦重_2_1795392346_ctx.png`：

    [火][60]      <- 胶囊（60 伤害）
       扬沙       <- 名字带，清楚可见

`my_team` 槽3 配置 = `[筛管奔流, 扬沙, 虫击, 冰雹]`，槽2 正是扬沙。
但单级词表时被拒三次：

    BAND -> 卡瓦重 槽2 未识别 (conf 0.00 < 0.80) raw='扬沙' vocab=58

原因：`species_skills` 不含「扬沙」。该表数据不全 —— `species` 表里
「卡瓦重」有 4 个同名 id（3042/3294/3295/3296），跨行取并集仍缺扬沙，
而游戏里确实带着（dump 图 + 配置双重铁证）。

## 为什么配置优先是安全的

- 只有 4 个候选词 -> 分母极小，1 字偏差分数高
- 不可能配到别的精灵的技能（硬约束）
- 它是用户亲手配的，权威性高于任何推导出来的池子

并且**不引入误配**：配置里没有的词（火焰切割/岩脉崩毁/吞噬）
在卡瓦重的配置词表下仍然 0.00 被拒。
"""
import os
import sys

ROOT = r"D:\Projects\RocoAgent"
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, r"D:\Tools")
import paths
paths.use_tools()
import sqlite3

import session_moves as SM

FAIL = []


def check(name, cond, detail=""):
    print("  %s %s %s" % ("ok  " if cond else "FAIL", name, detail))
    if not cond:
        FAIL.append(name)


def main():
    print("== 两级词表 ==")
    db = sqlite3.connect(SM.DB)

    # 1) 第一级必须是 my_team 配置
    own = SM._owned_vocab(db, "卡瓦重")
    check("_owned_vocab(卡瓦重) 返回 4 个", len(own) == 4, str(own))
    check("配置词表含「扬沙」（实机确实带着）", "扬沙" in own, str(own))

    # 2) 第二级（species_skills）不含扬沙 —— 这就是需要第一级的原因
    learn = SM._learnable_vocab(db, "卡瓦重")
    check("species_skills 不含「扬沙」（数据不全的证据）",
          "扬沙" not in learn, "共 %d 个" % len(learn))
    check("species_skills 非空（否则不该退化）", len(learn) > 10,
          "共 %d 个" % len(learn))

    # 3) 不在 my_team 的精灵走第二级
    check("不在 my_team 的精灵配置为空", SM._owned_vocab(db, "棋绮后") == [])
    check("但它的 species_skills 非空",
          len(SM._learnable_vocab(db, "棋绮后")) > 10)

    # 4) 全库兜底
    check("全库词表 > 900", len(SM._all_vocab(db)) > 900,
          "%d 个" % len(SM._all_vocab(db)))
    db.close()

    # 5) 核心判据：扬沙在配置词表下能过，在可学池下不能过
    import ocr_cn
    nm_c, sc_c = ocr_cn.Ocr.match("扬沙", own)
    check("配置词表下「扬沙」匹配 >= 0.80", sc_c >= 0.80,
          "-> %r %.2f" % (nm_c, sc_c))
    nm_l, sc_l = ocr_cn.Ocr.match("扬沙", learn)
    check("可学池下「扬沙」匹配 < 0.80（旧行为，说明修复必要）",
          (nm_l is None) or sc_l < 0.80, "-> %r %.2f" % (nm_l, sc_l))

    # 6) 不引入误配
    for bad in ("火焰切割", "岩脉崩毁", "吞噬"):
        nm, sc = ocr_cn.Ocr.match(bad, own)
        check("配置词表拒绝配置外的「%s」" % bad,
              nm is None or sc < 0.80, "-> %r %.2f" % (nm, sc))

    # 7) raw_of 必须带 tier
    SM.STORE = os.path.join(os.environ.get("TEMP", "/tmp"),
                            "band_vocab_test.json")
    SM._DATA, SM._DIRTY = None, False
    SM._BAND_RAW[("测试", 1)] = ("扬沙", 4, 0.80, "config", 0.0)
    raw, nv, thr, tier = SM.raw_of("测试", 1)
    check("raw_of 返回 tier", tier == "config",
          "raw=%r nvoc=%d thr=%.2f tier=%s" % (raw, nv, thr, tier))

    print()
    if FAIL:
        print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())