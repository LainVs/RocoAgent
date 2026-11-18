"""burst.py - grab N frames at a fixed interval into a folder.

usage: python burst.py <outdir> <count> <interval_ms>
"""
import os
import sys
import time

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cap import grab  # noqa: E402

outdir = sys.argv[1]
count = int(sys.argv[2])
iv = int(sys.argv[3]) / 1000.0
os.makedirs(outdir, exist_ok=True)
for f in os.listdir(outdir):
    try:
        os.remove(os.path.join(outdir, f))
    except Exception:
        pass

t0 = time.time()
for i in range(count):
    arr = grab()
    Image.fromarray(arr[:, :, ::-1]).save(os.path.join(outdir, "b%03d.png" % i))
    print("%d %.3f %dx%d" % (i, time.time() - t0, arr.shape[1], arr.shape[0]))
    time.sleep(iv)
