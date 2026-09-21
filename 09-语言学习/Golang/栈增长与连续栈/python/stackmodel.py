"""Go goroutine 栈增长 / 收缩的可执行模型。

逐行转写自官方源码 src/runtime/stack.go（contiguous stacks）与 src/runtime/proc.go 的 newstack。
常量来源：
  - stackMin = 2048                                        (runtime/stack.go)
  - abi.StackNosplitBase = 800, abi.StackSmall = 128        (internal/abi/stack.go)
  - StackGuardMultiplier = 1 + IsAix + IsOpenbsd + isRace   (internal/runtime/sys/consts.go)
  - maxstacksize = 1e9 (64-bit) / 2.5e8 (32-bit)            (runtime/proc.go)
"""

STACK_MIN = 2048
STACK_NOSPLIT_BASE = 800
STACK_SMALL = 128
STACK_BIG = 4096

MAX_STACK_SIZE_64 = 1000000000
MAX_STACK_SIZE_32 = 250000000


class StackOverflow(Exception):
    pass


def stack_system(goos="linux", goarch="amd64"):
    """runtime/stack.go 的 stackSystem。"""
    return (4096 if goos == "windows" else 0) + (512 if goos == "plan9" else 0) + \
           (1024 if (goos == "ios" and goarch == "arm64") else 0)


def fixed_stack(goos="linux", goarch="amd64"):
    """fixedStack：把 stackMin + stackSystem 向上取整到 2 的幂（官方的 hackery）。"""
    x = STACK_MIN + stack_system(goos, goarch)
    p = 1
    while p < x:
        p *= 2
    return p


def stack_nosplit(multiplier=1):
    """stackNosplit = abi.StackNosplitBase * sys.StackGuardMultiplier。"""
    return STACK_NOSPLIT_BASE * multiplier


def stack_guard(goos="linux", goarch="amd64", multiplier=1):
    """stackGuard = stackNosplit + stackSystem + abi.StackSmall。"""
    return stack_nosplit(multiplier) + stack_system(goos, goarch) + STACK_SMALL


def guard_sentinel(name, ptr_size=8):
    """g.stackguard0 的三个哨兵值：stackPreempt / stackFork / stackForceMove。"""
    mask = (1 << (8 * ptr_size)) - 1
    base = {-1314: "stackPreempt", -1234: "stackFork", -275: "stackForceMove"}
    if name not in base:
        raise ValueError("unknown sentinel")
    return mask & name


class G(object):
    """对应 runtime.g 的栈相关字段。"""

    def __init__(self, size, sp_offset=0, goos="linux", goarch="amd64"):
        self.lo = 0x1000
        self.hi = self.lo + size
        self.sp = self.hi - sp_offset      # sp_offset = 已用字节数
        self.goos = goos
        self.goarch = goarch
        self.moves = 0                     # 栈被搬动过的次数

    @property
    def size(self):
        return self.hi - self.lo

    @property
    def used(self):
        return self.hi - self.sp


def newstack(gp, func_max_sp_delta=None, force_move=False, ptr_size=8):
    """proc.go 的 newstack 里「算新栈大小」的部分（不含抢占分支）。"""
    oldsize = gp.size
    newsize = oldsize * 2
    guard = stack_guard(gp.goos, gp.goarch)
    if func_max_sp_delta is not None:
        needed = func_max_sp_delta + guard
        used = gp.used
        while newsize - used < needed:
            newsize *= 2
    if force_move:
        newsize = oldsize     # stackForceMove：调试用，故意不翻倍
    maxsize = MAX_STACK_SIZE_64 if ptr_size == 8 else MAX_STACK_SIZE_32
    ceiling = 2 * maxsize
    if newsize > maxsize or newsize > ceiling:
        raise StackOverflow("stack overflow: %d > %d" % (newsize, maxsize))
    copystack(gp, newsize)
    return newsize


def copystack(gp, newsize):
    """stack.go 的 copystack：整块复制，**栈内相对偏移保持不变**。"""
    old_lo = gp.lo
    old_hi = gp.hi
    used = old_hi - gp.sp
    gp.lo = 0x1000 + (gp.moves + 1) * 0x100000   # 新地址（每次搬动换一块内存）
    gp.hi = gp.lo + newsize
    gp.sp = gp.hi - used                   # 相对栈顶的偏移不变
    gp.moves += 1
    return old_lo, old_hi


def shrinkstack(gp, debug_off=False):
    """stack.go 的 shrinkstack：返回新大小，None 表示不收缩。"""
    if debug_off:
        return None
    oldsize = gp.size
    newsize = oldsize // 2
    if newsize < fixed_stack(gp.goos, gp.goarch):
        return None                        # 不低于最小栈分配
    avail = gp.size
    used = gp.used + stack_nosplit()
    if used >= avail // 4:
        return None                        # 用了超过四分之一就不收缩
    copystack(gp, newsize)
    return newsize


def is_shrink_stack_safe(gp, syscallsp=0, async_safe_point=False,
                         parking_on_chan=False, waiting_for_suspend=False):
    """stack.go 的 isShrinkStackSafe 四条否决条件。"""
    if syscallsp != 0:
        return False                       # 系统调用里可能有指向栈的指针
    if async_safe_point:
        return False                       # 异步安全点没有精确指针图
    if parking_on_chan:
        return False                       # gopark 到 activeStackChans 置位之间
    if waiting_for_suspend:
        return False                       # 只是为了 suspendG 而等待
    return True
