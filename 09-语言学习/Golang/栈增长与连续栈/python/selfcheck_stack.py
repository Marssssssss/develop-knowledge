"""Go 栈增长模型的自检：断言来自 src/runtime/stack.go 与 proc.go 的实读结论。"""

import sys

from stackmodel import (
    STACK_MIN, STACK_NOSPLIT_BASE, STACK_SMALL, STACK_BIG,
    MAX_STACK_SIZE_64, MAX_STACK_SIZE_32,
    StackOverflow, G,
    stack_system, fixed_stack, stack_nosplit, stack_guard, guard_sentinel,
    newstack, shrinkstack, is_shrink_stack_safe,
)

PASS = [0]
FAIL = [0]


def ok(cond, label):
    if cond:
        PASS[0] += 1
    else:
        FAIL[0] += 1
        print("FAIL: " + label)


def eq(got, want, label):
    ok(got == want, "%s (got=%r want=%r)" % (label, got, want))


def raises(fn, label):
    try:
        fn()
    except StackOverflow:
        PASS[0] += 1
        return
    except Exception as e:  # noqa
        FAIL[0] += 1
        print("FAIL: %s (raised %r)" % (label, e))
        return
    FAIL[0] += 1
    print("FAIL: %s (no exception)" % label)


# ---------------------------------------------------------------- E1 常量
def e1_constants():
    eq(STACK_MIN, 2048, "E1 stackMin=2048")
    eq(STACK_NOSPLIT_BASE, 800, "E1 abi.StackNosplitBase=800")
    eq(STACK_SMALL, 128, "E1 abi.StackSmall=128")
    eq(STACK_BIG, 4096, "E1 abi.StackBig=4096")
    eq(MAX_STACK_SIZE_64, 1000000000, "E1 64 位 maxstacksize=1e9")
    eq(MAX_STACK_SIZE_32, 250000000, "E1 32 位 maxstacksize=2.5e8")
    eq(stack_nosplit(1), 800, "E1 默认 StackGuardMultiplier=1 → stackNosplit=800")
    eq(stack_nosplit(2), 1600, "E1 race 构建下 multiplier=2")
    eq(stack_guard("linux"), 928, "E1 linux stackGuard = 800+0+128")
    eq(stack_guard("windows"), 5024, "E1 windows stackGuard = 800+4096+128")
    eq(stack_guard("plan9"), 1440, "E1 plan9 stackGuard = 800+512+128")


# ---------------------------------------------------------------- E2 fixedStack
def e2_fixed_stack():
    eq(stack_system("linux"), 0, "E2 linux stackSystem=0")
    eq(stack_system("windows"), 4096, "E2 windows stackSystem=4096")
    eq(stack_system("plan9"), 512, "E2 plan9 stackSystem=512")
    eq(fixed_stack("linux"), 2048, "E2 linux fixedStack=2048")
    eq(fixed_stack("windows"), 8192, "E2 windows 2048+4096=6144 上取整到 8192")
    eq(fixed_stack("plan9"), 4096, "E2 plan9 2048+512=2560 上取整到 4096")
    # 上取整到 2 的幂：任何平台的结果都必须是 2 的幂
    for goos in ("linux", "windows", "plan9", "ios"):
        v = fixed_stack(goos)
        ok(v & (v - 1) == 0, "E2 fixedStack(%s)=%d 是 2 的幂" % (goos, v))


# ---------------------------------------------------------------- E3 翻倍
def e3_grow_doubles():
    g = G(2048, sp_offset=100)
    n = newstack(g)
    eq(n, 4096, "E3 2048 → 4096")
    eq(g.size, 4096, "E3 栈真的变成 4096")
    eq(g.used, 100, "E3 已用部分不变")
    eq(g.sp - g.lo, 4096 - 100, "E3 sp 相对栈底的偏移 = 新大小 - 已用")
    n2 = newstack(g)
    eq(n2, 8192, "E3 再涨一次 4096 → 8192")


# ---------------------------------------------------------------- E4 「至少够放新帧」
def e4_needed_loop():
    # oldsize=2048, used=2000, funcMaxSPDelta=3000, guard=928 → needed=3928
    g = G(2048, sp_offset=2000)
    n = newstack(g, func_max_sp_delta=3000)
    eq(n, 8192, "E4 4096-2000=2096 < 3928，再翻倍到 8192")
    ok(n - 2000 >= 3000 + stack_guard("linux"),
       "E4 结果满足 newsize-used >= needed")
    # 帧很小的时候一次翻倍就够
    g2 = G(2048, sp_offset=100)
    eq(newstack(g2, func_max_sp_delta=64), 4096, "E4 小帧：一次翻倍就够")
    # 边界：needed 恰好等于 newsize-used 时不再翻倍（条件是严格小于）
    g3 = G(2048, sp_offset=0)
    guard = stack_guard("linux")
    delta = 4096 - guard          # needed = 4096，newsize-used = 4096
    eq(newstack(g3, func_max_sp_delta=delta), 4096, "E4 恰好相等时不翻倍")


# ---------------------------------------------------------------- E5 上限
def e5_limits():
    eq(MAX_STACK_SIZE_64 * 2, 2000000000, "E5 maxstackceiling = 2*maxstacksize")
    g = G(1 << 29, sp_offset=10)   # 512MB
    raises(lambda: newstack(g), "E5 512MB 再翻倍 1GB > 1e9 → stack overflow")
    g2 = G(1 << 28, sp_offset=10)  # 256MB
    eq(newstack(g2), 1 << 29, "E5 256MB → 512MB 还没超")
    g3 = G(1 << 28, sp_offset=10)
    raises(lambda: newstack(g3, ptr_size=4), "E5 32 位下 512MB > 2.5e8 → overflow")


# ---------------------------------------------------------------- E6 收缩的四分之一
def e6_shrink_quarter():
    g = G(8192, sp_offset=1000)
    eq(shrinkstack(g), 4096, "E6 used=1000+800=1800 < 2048 → 收缩到 4096")
    eq(g.size, 4096, "E6 栈变成 4096")
    eq(g.used, 1000, "E6 已用部分不变")
    # 超过四分之一不收缩
    g2 = G(8192, sp_offset=1300)
    eq(shrinkstack(g2), None, "E6 used=2100 >= 2048 → 不收缩")
    eq(g2.size, 8192, "E6 大小不变")
    # 边界：used 恰好等于 avail/4 时不收缩（官方是 >=）
    g3 = G(8192, sp_offset=1248)   # 1248+800 = 2048 = 8192/4
    eq(shrinkstack(g3), None, "E6 恰好等于四分之一也不收缩（>= 判据）")
    g4 = G(8192, sp_offset=1247)
    eq(shrinkstack(g4), 4096, "E6 小于四分之一才收缩")


# ---------------------------------------------------------------- E7 收缩下限
def e7_shrink_floor():
    eq(shrinkstack(G(4096, sp_offset=10)), 2048, "E7 linux 4096 → 2048")
    eq(shrinkstack(G(2048, sp_offset=10)), None, "E7 2048 再缩会低于 fixedStack=2048")
    eq(shrinkstack(G(8192, sp_offset=10, goos="windows")), None,
       "E7 windows fixedStack=8192，8192 不能再缩")
    eq(shrinkstack(G(16384, sp_offset=10, goos="windows")), 8192,
       "E7 windows 16384 → 8192")
    eq(shrinkstack(G(4096, sp_offset=10, goos="plan9")), None,
       "E7 plan9 fixedStack=4096，4096 不能再缩")


# ---------------------------------------------------------------- E8 哨兵值
def e8_sentinels():
    eq(guard_sentinel(-1314, 8), 0xfffffffffffffade, "E8 stackPreempt = 0xfffffade")
    eq(guard_sentinel(-1234, 8), 0xfffffffffffffb2e, "E8 stackFork = 0xfffffb2e")
    eq(guard_sentinel(-275, 8), 0xfffffffffffffeed, "E8 stackForceMove = 0xfffffeed")
    eq(guard_sentinel(-1314, 4), 0xfffffade, "E8 32 位下 stackPreempt = 0xfffffade")
    # 三个哨兵都比任何真实 SP 大（官方注释：These are all larger than any real SP）
    ok(min(guard_sentinel(-1314, 8), guard_sentinel(-1234, 8),
           guard_sentinel(-275, 8)) > (1 << 47),
       "E8 哨兵值都远大于真实用户态 SP")


# ---------------------------------------------------------------- E9 ForceMove 不翻倍
def e9_force_move():
    g = G(2048, sp_offset=100)
    eq(newstack(g, force_move=True), 2048, "E9 stackForceMove 时 newsize = oldsize")
    eq(g.size, 2048, "E9 大小不变")
    eq(g.moves, 1, "E9 但栈确实被搬动了一次 copystack")
    g2 = G(2048, sp_offset=100)
    newstack(g2)
    eq(g2.moves, 1, "E9 正常增长也会搬动一次")


# ---------------------------------------------------------------- E10 连续栈：相对偏移守恒
def e10_contiguous_invariant():
    g = G(2048, sp_offset=777)
    before = (g.lo, g.hi, g.sp)
    used_before = g.used
    rel = g.sp - g.lo
    newstack(g)
    eq(g.used, used_before, "E10 复制后已用字节数不变")
    eq(g.sp - g.hi, before[2] - before[1], "E10 sp 相对栈顶的偏移不变")
    ok(g.lo != before[0], "E10 栈底地址确实换了（是复制不是原地扩展）")
    ok(rel != g.sp - g.lo, "E10 相对栈底的偏移会变（因为新栈更大）")


# ---------------------------------------------------------------- E11 isShrinkStackSafe
def e11_shrink_safe():
    eq(is_shrink_stack_safe(None), True, "E11 默认安全")
    eq(is_shrink_stack_safe(None, syscallsp=0x2000), False, "E11 系统调用中不收缩")
    eq(is_shrink_stack_safe(None, async_safe_point=True), False,
       "E11 异步安全点不收缩")
    eq(is_shrink_stack_safe(None, parking_on_chan=True), False,
       "E11 gopark 到 activeStackChans 之间不收缩")
    eq(is_shrink_stack_safe(None, waiting_for_suspend=True), False,
       "E11 为 suspendG 而等待时不收缩")


def main():
    for fn in (e1_constants, e2_fixed_stack, e3_grow_doubles, e4_needed_loop,
               e5_limits, e6_shrink_quarter, e7_shrink_floor, e8_sentinels,
               e9_force_move, e10_contiguous_invariant, e11_shrink_safe):
        fn()
    print("PASS=%d FAIL=%d" % (PASS[0], FAIL[0]))
    return 1 if FAIL[0] else 0


if __name__ == "__main__":
    sys.exit(main())
