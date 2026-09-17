"""极简断言 harness —— 两个自检模块(SemVer 侧 / OCI 侧)共用。

与同批次其他 demo 一致: 刻意不依赖 pytest, ``python semver_check.py`` 直接跑出
「N 通过 / M 失败」并打印失败明细。
"""

from __future__ import annotations

PASS, FAIL = 0, 0
FAILED = []


def check(label, cond, detail=""):
    """记一条断言。``label`` 描述**规范语义**, 不是实现细节。"""
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILED.append("%s %s" % (label, detail))
        print("  FAIL %s %s" % (label, detail))


def raises(fn, *a, **kw):
    """断言调用抛错: 规范层面的非法输入必须在解析/校验期暴露。"""
    try:
        fn(*a, **kw)
        return False
    except ValueError:
        return True


def report(title="制品晋升与语义化版本 自检"):
    print("\n%s: 断言 %d 通过 / %d 失败" % (title, PASS, FAIL))
    if FAILED:
        print("失败明细:")
        for f in FAILED:
            print("  - " + f)
        raise SystemExit(1)
