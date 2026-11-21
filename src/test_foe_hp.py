# -*- coding: utf-8 -*-
"""test_foe_hp.py —— species() 缓存与读取的一致性。

为什么值得测
------------
`_publish_state` 每帧要调 max_hp / types_of x2 / hp_band / species，
这几个函数内部各自 `sqlite3.connect()`。实测每帧 **11.5 ms**，全是重复
open/close + 同一条 SQL —— 运行期数据库是不变的。

加了缓存之后降到 0.0 ms，但缓存最容易出的问题是：

  · 未知名字每次重查（缓存 None 需要单独处理，不能用 `if cached:`）
  · 缓存挂着上一次的库（改了库要能清）

用法：python src/test_foe_hp.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r"D:\Tools")

import foe_hp as FH  # noqa: E402

fails = 0


def check(cond, name, detail=""):
    global fails
    if not cond:
        fails += 1
    print("  %-4s %-48s %s" % ("OK" if cond else "FAIL", name, detail))


def main():
    FH.clear_cache()

    print("[1] 已知名字")
    row = FH.species("音速犬")
    check(row is not None, "species('音速犬') 有结果",
          "None" if row is None else row.get("name"))
    check(row is not None and row.get("name") == "音速犬", "名字正确")

    print()
    print("[2] 缓存命中返回同样的数据")
    a = FH.species("音速犬")
    b = FH.species("音速犬")
    check(a == b, "两次结果一致")
    check(a is b, "是同一个对象（确实走了缓存）")

    print()
    print("[3] 未知名字缓存 None 也要生效")
    t0 = time.perf_counter()
    FH.species("这个精灵不存在")
    t1 = time.perf_counter()
    FH.species("这个精灵不存在")
    t2 = time.perf_counter()
    check(FH.species("这个精灵不存在") is None, "未知名字返回 None")
    first = (t1 - t0) * 1000
    second = (t2 - t1) * 1000
    check(second <= first or second < 1.0,
          "第二次不重查库", "%.3fms -> %.3fms" % (first, second))

    print()
    print("[4] types_of 两种入参一致")
    by_name = FH.types_of("音速犬")
    by_row = FH.types_of(FH.species("音速犬"))
    check(by_name == by_row, "名字/行 两种入参结果一致", str(by_name))
    check(isinstance(by_name, list), "返回是 list")

    print()
    print("[5] 缓存把 5 次查库降到 0")
    def publish_like():
        FH.max_hp("音速犬")
        FH.types_of("音速犬")
        FH.types_of("水灵")
        FH.hp_band("水灵")
        FH.species("水灵")

    # 先预热（第一次会真的查库）
    publish_like()
    t0 = time.perf_counter()
    for _ in range(20):
        publish_like()
    per = (time.perf_counter() - t0) * 1000 / 20
    check(per < 1.0, "缓存后每帧 < 1ms", "%.3f ms/帧" % per)

    print()
    print("[6] clear_cache 之后重新查库")
    FH.clear_cache()
    check(FH.species("音速犬") is not None, "清缓存后仍能查到")
    check(FH.species("音速犬") is not None, "再次查询正常")

    print()
    print("[7] 空名字不炸")
    check(FH.species("") is None, "空串返回 None")
    check(FH.species(None) is None, "None 返回 None")
    check(FH.types_of("") == [], "空串 types 是 []")

    print()
    if fails:
        print("%d FAILURE(S)" % fails)
        return 1
    print("all foe_hp cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
