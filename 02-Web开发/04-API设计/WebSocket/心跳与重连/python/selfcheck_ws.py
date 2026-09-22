"""610 自检入口：跑完 frames 与 lifecycle 两组，统一汇总。

拆分原因：单份自检超过 300 行，与"单文件 ≤300 行"的约定冲突。
改一个字就要能重跑，所以入口只做装配，不含断言。
"""

import harness
import selfcheck_frames
import selfcheck_lifecycle


def main():
    selfcheck_frames.run()
    selfcheck_lifecycle.run()
    total = harness.count()
    if harness.FAILURES:
        print("\nFAILED %d/%d" % (len(harness.FAILURES), total))
        for label in harness.FAILURES:
            print("  - %s" % label)
        raise SystemExit(1)
    print("ALL PASS (%d 断言)" % total)


if __name__ == "__main__":
    main()
