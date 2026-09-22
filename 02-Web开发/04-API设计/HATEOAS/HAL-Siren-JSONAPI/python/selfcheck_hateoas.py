"""606 自检入口：依次跑 HAL/Siren 与 JSON:API 两份自检后统一汇总。

运行：python selfcheck_hateoas.py
"""

import sys

import harness
import selfcheck_hal_siren  # noqa: F401  顶层即执行断言
import selfcheck_jsonapi    # noqa: F401

if __name__ == "__main__":
    print()
    if harness.FAILURES:
        print("FAILED %d: %s" % (len(harness.FAILURES), harness.FAILURES[:5]))
        sys.exit(1)
    print("ALL PASS (%d 断言)" % harness.count())
