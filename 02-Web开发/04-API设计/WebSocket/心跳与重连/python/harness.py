"""610 自检用的极简断言框架（控制帧 / 关闭握手 / 心跳 / 重连退避共用同一份计数器）。

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


class Fixed(object):
    """确定性随机源：取区间中点 / 上界 / 下界，用于把 jitter 钉死。

    自检里凡是涉及随机的行为都必须用确定性源，否则"通过"只是运气好。
    """

    def __init__(self, pick="mid"):
        self.pick = pick

    def uniform(self, a, b):
        if self.pick == "mid":
            return (a + b) / 2.0
        if self.pick == "hi":
            return b
        return a
