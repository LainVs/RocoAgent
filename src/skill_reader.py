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

    def __init__(self, history=HISTORY, misalign=MISALIGN_SLOTS):
        self._history = history
        self._misalign = misalign
        self._pet = None
        self._frames = []        # [[key_or_None] * 4, ...] 最近的帧
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
        if self._is_misaligned(keys):
            self._dropped += 1
            return self._rebuild_from_mode(skills)

        # 单槽位抖动检测（2026-11-21 07:20 实测数据）：
        #
        # 连续两次轮询里，槽位变化的数量分布是：
        #     0 个变化 405 次
        #     1 个变化 192 次   <- 常态
        #     2 个变化 189 次   <- 常态
        #     3 个变化 124 次   <- 整组错位
        #     4 个变化   5 次
        #
        # 也就是说 1~2 个槽位跳变是最常见的抖动形态，
        # 光靠"整组错位"判据压不住。这里再加一条：
        # 如果某槽位本帧的值只出现 1 次，而上一帧是另一个值，
        # 且历史里有 >=2 帧一致的旧值，就沿用旧值。
        if self._frames:
            prev = self._frames[-1]
            for i, k in enumerate(keys):
                if k is None or i >= len(prev) or prev[i] is None:
                    continue
                if k == prev[i]:
                    continue
                # 本帧这个值在历史里出现过吗？
                seen = sum(1 for f in self._frames
                           if i < len(f) and f[i] == k)
                if seen > 0:
                    continue
                # 上一帧的值必须已经被历史确认（>=2 帧一致），
                # 否则第一帧就会被无限沿用，真实变化永远上不来。
                prev_cnt = sum(1 for f in self._frames
                               if i < len(f) and f[i] == prev[i])
                if prev_cnt >= 2:
                    keys[i] = prev[i]      # 全新值 + 旧值已确认 -> 抖动

        # 正常帧：入历史
        self._frames.append(keys)
        if len(self._frames) > self._history:
            self._frames.pop(0)
        self._accepted += 1

        out = []
        for i, s in enumerate(skills):
            if s is None:
                out.append(None)
                continue
            s = dict(s)
            m = self._mode(i)
            # 只有"某值出现过 >=2 次"才覆盖单帧读数，
            # 否则保持原样（首回合不阻塞决策）。
            if m is not None and m[0] is not None:
                cnt = sum(1 for f in self._frames
                          if i < len(f) and f[i] == m)
                if cnt >= 2 and m[0] != s.get("value"):
                    s["value_raw"] = s.get("value")
                    s["value"] = m[0]
                    s["kind"] = m[1] or s.get("kind")
                    s["stabilized"] = True
            out.append(s)
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
