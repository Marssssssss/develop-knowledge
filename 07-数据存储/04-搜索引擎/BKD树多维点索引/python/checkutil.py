"""自检用的断言计数器，被 selfcheck_bkd.py / selfcheck_pack.py 共用。"""

N_OK = [0]


def eq(name, got, want):
    assert got == want, "%s: got %r want %r" % (name, got, want)
    N_OK[0] += 1


def ok(name, cond, extra=""):
    assert cond, "%s%s" % (name, (" -- " + str(extra)) if extra else "")
    N_OK[0] += 1


def raises(name, fn, exc=ValueError):
    try:
        fn()
    except exc:
        N_OK[0] += 1
        return
    raise AssertionError(name + ": 没有抛异常")


def report(title):
    print("%s: %d assertions OK" % (title, N_OK[0]))
