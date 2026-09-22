"""606 自检用的极简断言框架（HAL / Siren / JSON:API 三份自检共用同一份计数器）。

只做三件事：记断言数、记失败标签、最后统一汇总。断言必须成对构造 ——
「该通过的通过」与「该拒绝的被拒绝」各来一条，只判一侧等于没测。
"""

FAILURES = []
STATE = {"assertions": 0}


def check(cond, label):
    STATE["assertions"] += 1
    if cond:
        return True
    FAILURES.append(label)
    print("FAIL %s" % label)
    return False


def expect_errors(errors, must_contain, must_not_contain=(), label=""):
    """同时校验漏报（该报的报了）与误报（不该报的没报）。"""
    ok = True
    for fragment in must_contain:
        STATE["assertions"] += 1
        if not any(fragment in e for e in errors):
            ok = False
            check(False, "%s: 漏报 %r（实得 %r）" % (label, fragment, errors))
    for fragment in must_not_contain:
        STATE["assertions"] += 1
        if any(fragment in e for e in errors):
            ok = False
            check(False, "%s: 误报 %r（实得 %r）" % (label, fragment, errors))
    return ok


def count():
    return STATE["assertions"]
