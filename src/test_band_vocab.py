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

    # 2) 第二级（species_skills）不含扬沙 —— 这就是需要配置的原因
    learn = SM._learnable_vocab(db, "卡瓦重")
    check("species_skills 不含「扬沙」（数据不全的证据）",
          "扬沙" not in learn, "共 %d 个" % len(learn))
    check("species_skills 非空（否则不该退化）", len(learn) > 10,
          "共 %d 个" % len(learn))

    # ---- 2b) 并集：两个来源漏的地方相反，必须都保留 ---------------
    #
    # 「扬沙」配置有/池内无  -> 需要配置
    # 「吞噬」配置无(过期)/池内有 -> 需要池
    # 单用任一份都会漏掉另一半。
    uv, utier = SM._band_vocab(db, "卡瓦重")
    check("并集含「扬沙」（配置来源）", "扬沙" in uv, utier)
    check("并集 tier = config+species", utier == "config+species", utier)
    check("并集规模 < 60（否则门槛会跳变）", len(uv) < 60,
          "%d 个" % len(uv))

    uv2, _t2 = SM._band_vocab(db, "音速犬")
    check("音速犬并集含「吞噬」（可学池来源，配置里没有）",
          "吞噬" in uv2, "配置=%s" % SM._owned_vocab(db, "音速犬"))
    check("音速犬配置里确实没有「吞噬」",
          "吞噬" not in SM._owned_vocab(db, "音速犬"))

    # 所有队伍精灵的并集规模都不该跨 60
    for pet in ("音速犬", "卡瓦重", "冰钻布鲁斯", "银月狼王", "恶魔狼"):
        v, _ = SM._band_vocab(db, pet)
        check("并集规模 <60: %s" % pet, len(v) < 60, "%d 个" % len(v))

    # 3) 不在 my_team 的精灵只走可学池
    check("不在 my_team 的精灵配置为空", SM._owned_vocab(db, "棋绮后") == [])
    check("但它的 species_skills 非空",
          len(SM._learnable_vocab(db, "棋绮后")) > 10)

    # 4) 全库兜底
    check("全库词表 > 900", len(SM._all_vocab(db)) > 900,
          "%d 个" % len(SM._all_vocab(db)))
    db.close()

    # 5) 核心判据：并集下扬沙能过
    import ocr_cn
    nm_c, sc_c = ocr_cn.Ocr.match("扬沙", uv)
    check("并集下「扬沙」匹配 >= 0.80", sc_c >= 0.80,
          "-> %r %.2f" % (nm_c, sc_c))
    nm_l, sc_l = ocr_cn.Ocr.match("扬沙", learn)
    check("仅可学池下「扬沙」匹配 < 0.80（说明配置这一份必要）",
          (nm_l is None) or sc_l < 0.80, "-> %r %.2f" % (nm_l, sc_l))

    # 6) 不引入误配：真正无关的词仍被拒。
    #
    # ⚠ 断言要挑**两个来源都没有**的词。第一版用了「岩脉崩毁」，
    # 而它恰好在卡瓦重的可学池里（可学池很宽松，40k 行）——
    # 并集接受它是**正确**的，不是误配。用它会测出假失败。
    for bad in ("火焰切割", "完全不存在的技能名"):
        nm, sc = ocr_cn.Ocr.match(bad, uv)
        near = SM._unique_near(bad, uv)
        check("并集拒绝无关词「%s」" % bad,
              (nm is None or sc < 0.80) and near is None,
              "-> %r %.2f near=%r" % (nm, sc, near))

    # 并集确实让"配置外但池内"的词变得可接受（这是本轮的修复目标）
    _db2 = sqlite3.connect(SM.DB)
    check("「岩脉崩毁」不在卡瓦重配置里、但在可学池里",
          "岩脉崩毁" not in SM._owned_vocab(_db2, "卡瓦重")
          and "岩脉崩毁" in SM._learnable_vocab(_db2, "卡瓦重"))
    _db2.close()

    # 7) raw_of 必须带 tier
    SM.STORE = os.path.join(os.environ.get("TEMP", "/tmp"),
                            "band_vocab_test.json")
    SM._DATA, SM._DIRTY = None, False
    SM._BAND_RAW[("测试", 1)] = ("扬沙", 4, 0.80, "config", 0.0, "match")
    raw, nv, thr, tier, how = SM.raw_of("测试", 1)
    check("raw_of 返回 tier", tier == "config",
          "raw=%r nvoc=%d thr=%.2f tier=%s" % (raw, nv, thr, tier))
    check("raw_of 返回 how", how == "match", "how=%s" % how)

    # ---- 唯一性近似兜底（形近字错读）--------------------------------
    #
    # 实测：raw='诉毁' vs 配置[借用,火云车,诋毁,闪光冲击]
    #   -> ocr_cn.Ocr.match 只给 0.50，被 0.80 门槛拒
    #   -> 但"等长+编辑距离<=1"的候选只有「诋毁」，可以安全采纳
    #
    # 危险场景必须被拒绝（模糊即拒），否则会误配：
    #   raw='诋毁' 对 [诋毁,底毁,抵毁] -> 3 个候选 -> 拒
    #   raw='扬沙' 对全库（扬尘/扬沙/流沙）-> 拒
    print("\n== 唯一性近似兜底 ==")
    cfg = ["借用", "火云车", "诋毁", "闪光冲击"]
    check("诉毁 -> 诋毁（配置内唯一近似）",
          SM._unique_near("诉毁", cfg) == "诋毁",
          str(SM._unique_near("诉毁", cfg)))
    check("形近词多候选时拒绝",
          SM._unique_near("诋毁", ["诋毁", "底毁", "抵毁"]) is None)
    check("火云车/火云马/火云箭 多候选时拒绝",
          SM._unique_near("火云车", ["火云车", "火云马", "火云箭"]) is None)
    check("完全相同（编辑距 0）也走唯一判定",
          SM._unique_near("扬沙", ["扬沙", "虫击", "冰雹"]) == "扬沙")
    check("无候选时返回 None",
          SM._unique_near("完全不存在的名字", cfg) is None)
    check("长度不同不参与（避免 2 字误配 4 字）",
          SM._unique_near("毁", cfg) is None)

    print()
    if FAIL:
        print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())