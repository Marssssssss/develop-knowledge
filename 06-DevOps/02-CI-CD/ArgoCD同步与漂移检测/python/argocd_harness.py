"""极简断言 harness —— 两个自检模块(同步侧 / diff 侧)共用。

刻意不依赖 pytest 等第三方库: 本 demo 的硬约束是零第三方依赖, 直接
``python argocd_check.py`` 就能跑出「N 通过 / M 失败」并打印失败明细。
"""

from __future__ import annotations

PASS, FAIL = 0, 0
FAILED = []


def check(label, cond, detail=""):
    """记一条断言。``label`` 用中文描述**官方语义**(不是实现细节)。"""
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILED.append("%s %s" % (label, detail))
        print("  FAIL %s %s" % (label, detail))


def raises(fn, *a, **kw):
    """断言调用会抛错(配置类错误必须在加载/校验期暴露, 而不是静默忽略)。"""
    try:
        fn(*a, **kw)
        return False
    except ValueError:
        return True


def report(title="Argo CD 语义自检"):
    print("\n%s: 断言 %d 通过 / %d 失败" % (title, PASS, FAIL))
    if FAILED:
        print("失败明细:")
        for f in FAILED:
            print("  - " + f)
        raise SystemExit(1)
