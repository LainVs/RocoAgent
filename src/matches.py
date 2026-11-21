# -*- coding: utf-8 -*-
"""matches.py —— 把三份日志按「场次」拼起来。

为什么需要
----------
以前每场对局的信息散在三个文件里，而且**没有共同的场次标识**：

    rounds.jsonl   {match: 3, start: "09:22:25", end: "09:25:09", ...}
    turns.jsonl    {ts: "09:23:10", turn: 3, action: [...], skills: [...]}
    results.jsonl  {ts: "09:21:25", result: "win", ...}

`match` 是**进程内计数**，机器人重启就从 1 重来（实测最大 47，但 1 号出现
了 11 次）；时间戳又只有时分秒、没有日期。于是想回答"这一场打得怎么样"
只能靠时间窗猜，实测：

    turns 按时间窗落进 rounds：唯一归属 45.6%，落空 9.9%，**多场重叠 44.5%**

也就是接近一半的数据根本对不上。

现在每场对局开始时生成一个稳定 id：

    mid = "YYYYMMDD-HHMMSS"     例：20261121-093017

同一秒不可能开两场，所以它跨进程重启也唯一。三份日志都写这个字段。

本模块的作用
------------
    summarize()      每场一行：时长 / 手数 / 胜负 / 对面 / 读数质量
    match_detail(mid) 某一场的全部回合 + 结论

老记录没有 mid，用 `legacy=True` 标出来，并且**不去猜**归属 —— 时间窗
重叠时宁可为空，也不要把两场的回合混在一起。

用法：
    python src/matches.py --summary 20
    python src/matches.py --detail 20261121-093017
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, "logs")
ROUNDS = os.path.join(LOGS, "rounds.jsonl")
TURNS = os.path.join(LOGS, "turns.jsonl")
RESULTS = os.path.join(LOGS, "results.jsonl")


# ---------------------------------------------------------------- id

def new_mid(when=None):
    """生成场次 id。默认用当前时间。"""
    t = time.localtime(when if when is not None else time.time())
    return time.strftime("%Y%m%d-%H%M%S", t)


def short_mid(mid):
    """给界面用：20261121-093017 -> 1121-0930。"""
    if not mid:
        return "—"
    s = str(mid)
    if len(s) == 15 and s[8] == "-":
        return "%s-%s" % (s[4:8], s[9:13])
    return s


# ---------------------------------------------------------------- 读取

_CACHE = {"key": None, "data": None}


def _stat_all():
    out = []
    for p in (ROUNDS, TURNS, RESULTS):
        try:
            st = os.stat(p)
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def _load_jsonl(path):
    rows = []
    try:
        with io.open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    except OSError:
        pass
    return rows


def load_all():
    """读三份日志，(mtime, size) 没变就复用。"""
    key = _stat_all()
    if _CACHE["key"] == key and _CACHE["data"] is not None:
        return _CACHE["data"]
    data = {
        "rounds": _load_jsonl(ROUNDS),
        "turns": _load_jsonl(TURNS),
        "results": _load_jsonl(RESULTS),
    }
    _CACHE["key"] = key
    _CACHE["data"] = data
    return data


# ---------------------------------------------------------------- 归属

def _legacy_mid(r):
    """老记录没有 mid：给一个显式标注的合成 id，不参与真正的时间窗归属。"""
    return "legacy-%s-%s" % (r.get("match"), r.get("start") or "?")


def _pick_result(rows):
    """同一场可能被两条通道各记一次，优先用「历史战绩」累计计数那条。

    两条通道的字段差异：
      record 通道  {before, after, before_losses, after_losses, total, result}
      badge  通道  {before, after, of, result}
    record 通道读的是游戏自己维护的单调计数，可信度高得多。
    """
    if not rows:
        return None
    for r in reversed(rows):
        if "before_losses" in r:
            return {"result": r.get("result"), "src": "record"}
    r = rows[-1]
    return {"result": r.get("result"), "src": "badge"}


def _turn_stats(rows):
    """一场里的回合质量。"""
    if not rows:
        return {"rows": 0, "decisions": 0, "blind": 0, "unreadable": 0,
                "slots": 0, "slots_bad": 0, "foe": None, "our": None}
    seen = set()
    blind = unreadable = slots = bad = 0
    foe = Counter()
    our = Counter()
    for r in rows:
        key = (r.get("turn"), r.get("my_pct"), r.get("foe_pct"),
               tuple(r.get("action") or ()))
        seen.add(key)
        why = (r.get("why") or "").lower()
        if "blind" in why:
            blind += 1
        if "unreadable" in why or "no readable" in why:
            unreadable += 1
        if r.get("foe_pet"):
            foe[r["foe_pet"]] += 1
        if r.get("our_pet"):
            our[r["our_pet"]] += 1
        for s in (r.get("skills") or []):
            if not s:
                continue
            slots += 1
            t = (s.get("t") or "")
            if s.get("v") is None or "?" in t:
                bad += 1
    return {
        "rows": len(rows),
        "decisions": len(seen),
        "blind": blind,
        "unreadable": unreadable,
        "slots": slots,
        "slots_bad": bad,
        "foe": foe.most_common(1)[0][0] if foe else None,
        "our": our.most_common(1)[0][0] if our else None,
    }


# ---------------------------------------------------------------- 对外

def summarize(limit=None):
    """每场一行，按开始时间倒序。

    `legacy=True` 表示这条来自没有 mid 的老记录：回合内容**不可用**
    （时间戳没有日期，无法确定归属），只保留 rounds 自己的字段。
    """
    d = load_all()
    by_mid = defaultdict(list)
    for t in d["turns"]:
        m = t.get("mid")
        if m:
            by_mid[m].append(t)
    res_by_mid = defaultdict(list)
    for r in d["results"]:
        m = r.get("mid")
        if m:
            res_by_mid[m].append(r)

    items = []
    for i, r in enumerate(d["rounds"]):
        raw_mid = r.get("mid")
        mid = raw_mid or _legacy_mid(r)
        legacy = not bool(raw_mid)
        st = (_turn_stats(by_mid.get(raw_mid))
              if raw_mid else _turn_stats([]))
        res = _pick_result(res_by_mid.get(raw_mid)) if raw_mid else None
        items.append({
            "mid": mid,
            "short": short_mid(mid) if not legacy else "老#%s" % r.get("match"),
            "legacy": legacy,
            "start": r.get("start"),
            "end": r.get("end"),
            "seconds": r.get("seconds"),
            "actions": r.get("actions"),
            "result": (res or {}).get("result") or "unknown",
            "result_src": (res or {}).get("src"),
            "foe": st["foe"],
            "our": st["our"],
            "turn_rows": st["rows"],
            "decisions": st["decisions"],
            "blind": st["blind"],
            "unreadable": st["unreadable"],
            "slots": st["slots"],
            "slots_bad": st["slots_bad"],
            # 排序用文件顺序，不用 mid。
            #
            # rounds.jsonl 是**追写**的，写入顺序就是时间顺序；而老记录
            # 只有 HH:MM:SS 没有日期，拿字符串排会把不同天混在一起
            #（"legacy-9-..." vs "legacy-10-..."）。
            "_seq": i,
        })

    items.sort(key=lambda x: x["_seq"], reverse=True)
    for x in items:
        x.pop("_seq", None)
    if limit:
        items = items[:int(limit)]
    return items


def match_detail(mid):
    """某一场的全部回合 + 结论。"""
    d = load_all()
    round_rec = None
    for r in d["rounds"]:
        if (r.get("mid") or _legacy_mid(r)) == mid:
            round_rec = r
            break
    if round_rec is None and not str(mid).startswith("legacy-"):
        return None
    turns = [t for t in d["turns"] if t.get("mid") == mid] if \
        not str(mid).startswith("legacy-") else []
    results = [r for r in d["results"] if r.get("mid") == mid] if \
        not str(mid).startswith("legacy-") else []
    return {
        "mid": mid,
        "round": round_rec,
        "result": _pick_result(results),
        "turn_rows": len(turns),
        "stats": _turn_stats(turns),
        "turns": turns,
    }


def main(argv):
    if "--detail" in argv:
        mid = argv[argv.index("--detail") + 1]
        d = match_detail(mid)
        if not d:
            print("没有这一场：%s" % mid)
            return 1
        print(json.dumps({k: v for k, v in d.items() if k != "turns"},
                         ensure_ascii=False, indent=2))
        return 0

    n = 20
    if "--summary" in argv:
        i = argv.index("--summary")
        if i + 1 < len(argv):
            try:
                n = int(argv[i + 1])
            except ValueError:
                pass
    items = summarize(n)
    print("%-16s %-4s %-9s %6s %5s %6s %-12s %s"
          % ("mid", "胜负", "开始", "时长", "手数", "可读", "对面", "回溯"))
    for x in items:
        rate = ("%d/%d" % (x["slots"] - x["slots_bad"], x["slots"])
                if x["slots"] else "—")
        tag = "老记录" if x["legacy"] else (
            "盲打%d" % x["blind"] if x["blind"] else "")
        print("%-16s %-4s %-9s %5ss %5d %6s %-12s %s"
              % (x["short"], x["result"][:4], x["start"] or "—",
                 x["seconds"] if x["seconds"] is not None else "—",
                 x["decisions"], rate, x["foe"] or "—", tag))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
