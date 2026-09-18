#!/usr/bin/env python3
"""自检骨架:断言计数器 + 通用夹具(被 demos.py / main.py 共用)。"""

FAILS = []
TOTAL = [0]


def check(label, cond, detail=""):
    TOTAL[0] += 1
    if cond:
        print(f"  [ok] {label}")
    else:
        FAILS.append(label)
        print(f"  [FAIL] {label} {detail}")


def force_old(h, o):
    """把对象直接放进老年代(模拟它已经活过多轮 minor)。"""
    if o in h.eden:
        h.eden.remove(o)
    for s in h.survivors:
        if o in s:
            s.remove(o)
    o.gen, o.age = 1, h.tenuring
    h.old.append(o)
    return o
