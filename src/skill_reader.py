# -*- coding: utf-8 -*-
"""skill_reader.py —— 技能卡伤害读数的多帧稳定化。

为什么需要
----------
2026-11-21 实盘抓到同一只精灵（音速犬）同一个对手，血量全程 100% 不变，
但卡面伤害整组乱跳：

    T4   s1=120  s2=350  s3=60
    T5   s1=-    s2=350  s3=60
    T6   s1=240  s2=350  s3=60
    T7   s1=150  s2=350  s3=60
    T8   s1=15?  s2=196  s3=420   <- 三个槽位同时变
    T9   s1=153  s2=120  s3=50

T4-T7 连续 4 次一致，是真值。T8 是**整组错位**：
`perceive._slots` 对没检出的槽位，数字位置是用"已有槽位的列中位数
± 偏移"推断的，一旦某个 pill 漏检，推断出的列会把**相邻卡片的数字**
框进来，导致整组读数一起错。

后果很直接：T8 读到 s3=420 >= 对手血量 -> 判定 kill -> 实际只有 60 伤害。

设计
----
不改 `perceive` 的识别逻辑（它已经足够复杂，动它有回归风险），
在**识别之后、决策之前**加一层稳定化。

三条规则：
    1. 每槽位保留最近 N 帧，取众数（平票取最新）
    2. 整组错位检测：本帧 >=3 个槽位同时与历史众数不同 -> 丢弃本帧
    3. 换精灵后清空历史（不同精灵的卡完全不同）

只影响决策输入，不影响 turns.jsonl 的原始记录。
"""
from __future__ import annotations

from collections import Counter

# 每槽位保留的帧数
HISTORY = 5

# 整组错位判据：同时有这么多槽位与历史众数不同，就认为是整组偏移
MISALIGN_SLOTS = 3

# 过时淘汰：连续这么多帧都不是众数，说明卡面真的变了（换局/升级/buff），
# 此时清空该槽位历史，让新值重新建立共识。
#
# 为什么必须有（2026-11-21 07:40 实测）：一开始没有淘汰机制，
# 结果第 1 帧的 [155, None, 254, 300] 把后面 28 帧全部锁死，
# 即使真实值已经变成 [240, None, 152, 150] 也不放行。
# 跨局时 pet 名字相同（同一只精灵打多局），历史不会因换精灵清空，
# 就变成永久锁死 —— 这比抖动本身更危险。
STALE_LIMIT = 4


def _value_of(slot):
    """从一帧的槽位数据里取"伤害值"，变化技能/未读返回 None。"""
    if not slot:
        return None
    return slot.get("value")


def _key_of(slot):
    """槽位的身份键：伤害值 + kind。

    只用伤害值会让"350 伤害卡"和"350 的其它卡"混在一起；
    带上 kind 更稳（num / dash / absent）。
    """
    if not slot:
        return None
    v = slot.get("value")
    k = slot.get("kind") or ""
    if v is None and not k:
        return None
    return (v, k)


class SkillStabilizer:
    """把单帧的卡面读数稳定成多帧一致的读数。"""

    def __init__(self, history=HISTORY, misalign=MISALIGN_SLOTS,
                 stale=STALE_LIMIT):
        self._history = history
        self._misalign = misalign
        self._stale = stale
        self._pet = None
        self._frames = []        # [[key_or_None] * 4, ...] 最近的帧
        self._miss = [0] * 4     # 每个槽位"连续不是众数"的帧数
        self._dropped = 0        # 累计丢弃帧数（诊断用）
        self._accepted = 0

    # ---------------------------------------------------------------- 内部

    def _reset(self):
        self._frames = []

    def _mode(self, idx):
        """第 idx 槽位的历史众数。没有历史返回 None。"""
        cnt = Counter()
        latest = {}
        for n, frame in enumerate(self._frames):
            if idx >= len(frame):
                continue
            k = frame[idx]
            if k is None:
                continue
            cnt[k] += 1
            latest[k] = n
        if not cnt:
            return None
        # 先比出现次数，平票比"谁更新"
        return max(cnt, key=lambda k: (cnt[k], latest.get(k, -1)))

    def _is_misaligned(self, keys):
        """本帧是否整组错位。

        判据：有 >= misalign 个槽位，历史众数存在，但本帧的值与它不同。
        """
        diff = 0
        checked = 0
        for i, k in enumerate(keys):
            if k is None:
                continue
            m = self._mode(i)
            if m is None:
                continue
            checked += 1
            if k != m:
                diff += 1
        if checked < self._misalign:
            return False
        return diff >= self._misalign

    # ---------------------------------------------------------------- 对外

    def update(self, skills, pet=None):
        """喂入一帧读数，返回稳定后的读数。

        skills : list，长度 4，元素是 perceive 的槽位字典或 None
        pet    : 当前我方精灵名（换精灵时清空历史）

        返回：与输入等长的 list，元素是**拷贝**后的槽位字典。
        """
        if not skills:
            return skills

        # 换精灵 -> 历史作废
        #
        # 注意：pet=None 不当作"换了精灵"。
        # 实测（2026-11-21 07:10）：识别抖动会让 pet 在
        # 「音速犬」和 None 之间反复跳，如果 None 也清空历史，
        # 稳定化就永远攒不到 2 帧，等于没生效。
        # 只有**明确换成了另一只**才清空。
        if pet and pet != self._pet:
            if self._pet is not None:
                self._reset()
            self._pet = pet

        keys = [_key_of(s) for s in skills]

        # 整组错位 -> 丢弃本帧，用历史众数
        #
        # 但"整组错位"和"真实变化"在单帧上无法区分：
        # 换局/换对手时，三个槽位也会同时变。
        # 所以这里累计 _group_miss：连续 STALE_LIMIT 次整组错位
        # 就承认是真实变化，清空历史重新建立共识（否则会永久锁死）。
        if self._is_misaligned(keys):
            self._dropped += 1
            self._group_miss = getattr(self, "_group_miss", 0) + 1
            if self._group_miss < self._stale:
                return self._rebuild_from_mode(skills)
            # 连续整组错位太多次 -> 认定为真实变化
            self._reset()
            self._miss = [0] * 4
            self._group_miss = 0
            self._stale_resets = getattr(self, "_stale_resets", 0) + 1
        else:
            self._group_miss = 0

        # ---- 1) 先用**入历史前**的原始 keys 算众数，决定输出
        #
        # ---- 1) 先做过时淘汰，再算输出
        #
        # 实测（2026-11-21 07:40）：淘汰必须发生在**算输出之前**。
        # 如果先算输出再淘汰，当帧仍会返回旧值，等于没放行。
        #
        # 判据用**原始输入**（不能用 keys，它可能已被"沿用上一帧"改写，
        # 那样 _miss 永远不增长，淘汰形同虚设）。
        #
        # 注意：单槽位的正常抖动**不累加** _miss。
        # 实测（2026-11-21 07:45）：slot1 在 120/None/240/150 之间跳，
        # 4 帧就把 _miss 攒到 4，误判成"真实变化"并清空全部历史。
        # 只有**整组错位**才说明可能换局了，才累加 _miss。
        for i, s in enumerate(skills):
            k = _key_of(s)
            if k is None:
                continue
            m = self._mode(i)
            if m is None:
                self._miss[i] = 0
            elif k == m:
                self._miss[i] = 0
        # （整组错位的累加在下面 _is_misaligned 分支里做）

        # ---- 2) 用**入历史前**的原始 keys 算众数，决定输出
        #
        # 踩过的坑（2026-11-21 07:35）：一开始先把 keys 按"沿用上一帧"
        # 改写，再算众数 —— 结果众数永远等于当前值，稳定化从未生效
        # （实盘 5 分钟没有一条 STAB 日志）。必须解耦：
        # 输出用原始值 + 历史众数比较，历史存的是"修正后"的值。
        out = []
        for i, s in enumerate(skills):
            if s is None:
                out.append(None)
                continue
            s = dict(s)
            m = self._mode(i)
            if m is not None and m[0] is not None:
                cnt = sum(1 for f in self._frames
                          if i < len(f) and f[i] == m)
                if cnt >= 2 and m[0] != s.get("value"):
                    s["value_raw"] = s.get("value")
                    s["value"] = m[0]
                    s["kind"] = m[1] or s.get("kind")
                    s["stabilized"] = True
            out.append(s)

        # ---- 3) 把**输出值**写回 keys 再入历史
        #
        # 这样历史里存的是稳定后的值，下一帧的众数才不会被单帧抖动带偏。
        for i, s in enumerate(out):
            if s is None:
                continue
            keys[i] = _key_of(s)

        # 单槽位抖动压制：本帧出现全新值，且上一帧的值已被历史确认
        # （>=2 帧一致）-> 沿用上一帧。这一步只影响入历史，
        # 输出已在上面决定，避免"先改再算"导致众数失效。
        #
        # 注意：这个压制必须能被过时淘汰打破（见上面 _miss），
        # 否则真实变化永远上不来。
        if self._frames:
            prev = self._frames[-1]
            for i, k in enumerate(keys):
                if k is None or i >= len(prev) or prev[i] is None:
                    continue
                if k == prev[i]:
                    continue
                seen = sum(1 for f in self._frames
                           if i < len(f) and f[i] == k)
                if seen > 0:
                    continue
                prev_cnt = sum(1 for f in self._frames
                               if i < len(f) and f[i] == prev[i])
                if prev_cnt >= 2:
                    keys[i] = prev[i]

        self._frames.append(keys)
        if len(self._frames) > self._history:
            self._frames.pop(0)
        self._accepted += 1
        return out

    def _rebuild_from_mode(self, skills):
        """整组错位时，用历史众数重建读数。"""
        out = []
        for i, s in enumerate(skills):
            if s is None:
                out.append(None)
                continue
            s = dict(s)
            m = self._mode(i)
            if m is not None:
                cnt = sum(1 for f in self._frames
                          if i < len(f) and f[i] == m)
                if cnt >= 2:
                    if m[0] != s.get("value"):
                        s["value_raw"] = s.get("value")
                        s["value"] = m[0]
                        s["kind"] = m[1] or s.get("kind")
                    s["stabilized"] = True
                    s["misaligned_dropped"] = True
            out.append(s)
        return out

    def stats(self):
        return {"accepted": self._accepted, "dropped": self._dropped,
                "pet": self._pet, "frames": len(self._frames)}

    def reset(self):
        self._reset()
        self._pet = None
        self._dropped = 0
        self._accepted = 0


# ---------------------------------------------------------------- 自检

if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    def card(v, kind="num"):
        return {"value": v, "kind": kind, "index": 1}

    st = SkillStabilizer()
    print("=== 连续 4 帧 350/60，第 5 帧整组错位 ===")
    for n in range(4):
        out = st.update([card(120), card(350), card(60), None], pet="音速犬")
        print("  帧%d -> %s" % (n + 1,
                               [None if s is None else s.get("value")
                                for s in out]))
    out = st.update([card(15), card(196), card(420), card(335)], pet="音速犬")
    print("  错位帧 -> %s  (dropped=%d)" % (
        [None if s is None else s.get("value") for s in out], st.stats()["dropped"]))
    print()
    print("=== 换精灵应清空历史 ===")
    out = st.update([card(999), card(888), card(777), None], pet="恶魔狼")
    print("  换精灵 -> %s" % [None if s is None else s.get("value")
                              for s in out])
