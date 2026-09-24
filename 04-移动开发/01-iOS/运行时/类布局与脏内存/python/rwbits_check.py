"""自检框架：断言计数与汇总。"""

N = 0
FAILED = []


def ok(cond, msg):
    global N
    N += 1
    if not cond:
        FAILED.append(msg)
        print("  FAIL:", msg)


def raises(exc, fn, msg):
    global N
    N += 1
    try:
        fn()
    except exc:
        return
    except Exception as e:
        FAILED.append("%s (got %r)" % (msg, e))
        print("  FAIL: %s (got %r)" % (msg, e))
        return
    FAILED.append("%s (no raise)" % msg)
    print("  FAIL: %s (no raise)" % msg)


def section(t):
    print("[%s]" % t)


def report():
    print()
    print("assertions: %d, failed: %d" % (N, len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  -", f)
        raise SystemExit(1)
    print("ALL OK")
