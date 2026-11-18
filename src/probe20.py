"""probe20.py - grab 20 consecutive observations and print them, for eyeballing
against a screenshot. No input is sent.

usage: python probe20.py [n]
"""
import sys

sys.path.insert(0, r"D:\Projects\RocoAgent\src")
sys.path.insert(0, r"D:\Tools")
import robot  # noqa: E402

n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
s = robot.Screen()
p = robot.Perc(s)
print("rect", s.rect)
for i in range(n):
    st = p.observe()
    sk = "  ".join("%d:%s(%s)" % (x["index"], x["text"], x["color"])
                   for x in st["skills"] if x)
    print("%2d battle=%d bar=%d/%d me=%5s foe=%5s | %s" % (
        i, st["battle"], st["bar"], st["npills"], st["my_pct"], st["foe_pct"], sk),
        flush=True)
