"""Go 栈增长演示：从 2KB 一路涨到溢出，以及收缩的四分之一判据。"""

from stackmodel import (
    G, StackOverflow, fixed_stack, stack_guard, newstack, shrinkstack,
    stack_system, MAX_STACK_SIZE_64,
)


def main():
    print("== 1. 各平台的栈常量 ==")
    print("  %-8s %-10s %-12s %-10s" % ("GOOS", "stackSystem", "fixedStack", "stackGuard"))
    for goos in ("linux", "windows", "plan9"):
        print("  %-8s %-10d %-12d %-10d"
              % (goos, stack_system(goos), fixed_stack(goos), stack_guard(goos)))

    print("\n== 2. 连续增长（每次翻倍）==")
    g = G(2048, sp_offset=100)
    print("  %-10s %-10s %-8s" % ("oldsize", "newsize", "used"))
    for _ in range(6):
        old = g.size
        newstack(g)
        print("  %-10d %-10d %-8d" % (old, g.size, g.used))

    print("\n== 3. 帧很大时一次翻两倍 ==")
    g2 = G(2048, sp_offset=2000)
    n = newstack(g2, func_max_sp_delta=3000)
    print("  oldsize=2048 used=2000 funcMaxSPDelta=3000 guard=%d" % stack_guard("linux"))
    print("  needed=%d ；4096-2000=2096 不够 → 再翻倍 → newsize=%d"
          % (3000 + stack_guard("linux"), n))

    print("\n== 4. 涨到溢出 ==")
    g3 = G(1 << 28, sp_offset=10)
    try:
        while True:
            old = g3.size
            newstack(g3)
            if old > (1 << 30):
                break
    except StackOverflow as e:
        print("  最后一次 %d 字节 → %s" % (g3.size, e))
    print("  64 位上限 maxstacksize=%d（1 GB），ceiling=%d" % (MAX_STACK_SIZE_64, 2 * MAX_STACK_SIZE_64))

    print("\n== 5. 收缩：只用不到四分之一才缩 ==")
    print("  %-8s %-8s %-10s %-8s" % ("oldsize", "used", "used+800", "结果"))
    for used in (1000, 1247, 1248, 2000):
        g4 = G(8192, sp_offset=used)
        r = shrinkstack(g4)
        print("  %-8d %-8d %-10d %-8s"
              % (8192, used, used + 800, "收缩到 %d" % r if r else "不收缩"))
    print("  判据：used = (hi-sp) + stackNosplit ，used >= avail/4 就不收缩（是 >= 不是 >）")


if __name__ == "__main__":
    main()
