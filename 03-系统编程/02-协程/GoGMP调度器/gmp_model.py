"""Go 运行时 GMP 调度器的可执行模型。

依据（本轮实读，全部来自官方源码）：
  * src/runtime/runtime2.go —— P 的 runq [256] + runnext、G 状态枚举与 _Gscan 叠加位
  * src/runtime/proc.go     —— runqput / runqputslow / globrunqgetbatch / runqgrab /
                               runqsteal / findRunnable / sysmon / retake
  * design/24543-non-cooperative-preemption.md —— 异步抢占与 unsafe-point

关键常量（源码原值）：
  runq 容量 256           —— runtime2.go: runq [256]guintptr
  forcePreemptNS = 10ms   —— proc.go: const forcePreemptNS = 10 * 1000 * 1000
  sysmon 起始 20us，idle>50 后翻倍，上限 10ms
  全局队列每 61 个 schedtick 取一次（pp.schedtick%61 == 0）
  globrunqgetbatch(len(pp.runq)/2) —— 注意 len 是**容量** 256，故批量恒为 128
  runqgrab: n = t - h; n = n - n/2 —— 偷走「靠头的一半」，ceil

模型口径（源码未定量处显式标注）：
  * 时间统一用纳秒整数；sysmon 的「一次 tick」= 调用一次 tick()；
  * findRunnable 的查找顺序按源码：runnext → 本地 runq → 每 61 tick 查全局 → 窃取；
  * 本模型不建模 netpoll、GC、cgo，只保留调度骨架。
"""

# G 状态（runtime2.go 的 iota 枚举，注意 5 与 7 是历史保留位）
_Gidle, _Grunnable, _Grunning, _Gsyscall, _Gwaiting = 0, 1, 2, 3, 4
_Gmoribund_unused, _Gdead, _Genqueue_unused, _Gcopystack, _Gpreempted = 5, 6, 7, 8, 9
_Gscan = 0x1000
_Gscanrunnable, _Gscanrunning, _Gscansyscall, _Gscanwaiting, _Gscanpreempted = (
    _Gscan + _Grunnable, _Gscan + _Grunning, _Gscan + _Gsyscall,
    _Gscan + _Gwaiting, _Gscan + _Gpreempted)

RUNQ_CAP = 256
FORCE_PREEMPT_NS = 10 * 1000 * 1000      # 10ms
SYSMON_MAX_DELAY_US = 10 * 1000          # 10ms
GLOBAL_CHECK_EVERY = 61
GLOBAL_BATCH = RUNQ_CAP // 2             # globrunqgetbatch(len(pp.runq)/2) = 128


def status_of(v):
    """atomicstatus &^ _Gscan 得到「扫描结束后会变成的状态」。"""
    return v & ~_Gscan


class G:
    def __init__(self, goid, name=""):
        self.goid = goid
        self.name = name or ("g%d" % goid)
        self.status = _Grunnable
        self.unsafe = False          # 处于 unsafe-point（uintptr 存活 / write barrier 中）
        self.running_since = None

    def __repr__(self):
        return self.name


class P:
    def __init__(self, idx):
        self.idx = idx
        self.runq = []
        self.runnext = None
        self.schedtick = 0
        self.syscalltick = 0
        self.m = None
        self.status = "Prunning"
        # sysmon 记账
        self.smontick_sched = -1
        self.smon_when = 0
        self.smontick_syscall = -1
        self.syscall_when = 0

    def qlen(self):
        return len(self.runq) + (1 if self.runnext else 0)

    def empty(self):
        return self.qlen() == 0


class M:
    def __init__(self, mid):
        self.mid = mid
        self.p = None
        self.curg = None
        self.spinning = False
        self.in_syscall = False


class Scheduler:
    def __init__(self, nprocs=4):
        self.gomaxprocs = nprocs
        self.allp = [P(i) for i in range(nprocs)]
        self.globalq = []
        self.machines = []
        self.nmspinning = 0
        self.globrunqgets = 0
        self.preemptions = 0
        self.stolen = []
        self.handoffs = 0
        self.have_sysmon = True
        self.next_mid = 0

    def newm(self):
        m = M(self.next_mid)
        self.next_mid += 1
        self.machines.append(m)
        return m

    # ---------------- 入队 / 出队 ----------------
    def runqput(self, p, g, next=False):
        """runqput：next=True 先占 runnext，把原来那个踢进本地队列。"""
        if next:
            if not self.have_sysmon:
                next = False            # 没有 sysmon 就别用 runnext，否则一对互相唤醒的 G 会饿死别人
            else:
                old = p.runnext
                p.runnext = g
                if old is None:
                    return "runnext"
                g = old
        if len(p.runq) < RUNQ_CAP:
            p.runq.append(g)
            return "local"
        return self.runqputslow(p, g)

    def runqputslow(self, p, g):
        """本地满了：搬走一半（128）+ 新来的这个一起放进全局队列。"""
        n = len(p.runq) // 2
        batch = p.runq[:n]
        del p.runq[:n]
        batch.append(g)
        self.globalq.extend(batch)
        return len(batch)                # 129

    def runqget(self, p):
        """先取 runnext（它继承了当前时间片），再按 FIFO 取本地队列。"""
        if p.runnext is not None:
            g, p.runnext = p.runnext, None
            return g
        if p.runq:
            return p.runq.pop(0)
        return None

    def globrunqgetbatch(self, want=GLOBAL_BATCH):
        got = self.globalq[:want]
        del self.globalq[:want]
        return got

    # ---------------- findRunnable ----------------
    def find_runnable(self, p):
        g = self.runqget(p)
        if g is not None:
            return g, "local"
        p.schedtick += 1
        if p.schedtick % GLOBAL_CHECK_EVERY == 0 and self.globalq:
            self.globrunqgets += 1
            batch = self.globrunqgetbatch()     # 恒定请求 128 个
            if batch:
                first, rest = batch[0], batch[1:]
                p.runq.extend(rest)
                return first, "global"
        return self.steal(p), "steal"

    def steal(self, p):
        for p2 in self.allp:
            if p2 is p or p2.empty():
                continue                        # 不偷空闲 P
            taken = self.runqgrab(p2)
            if not taken:
                if p2.runnext is not None:      # 最后手段：偷 runnext
                    g, p2.runnext = p2.runnext, None
                    self.stolen.append(g)
                    return g
                continue
            g = taken[-1]                       # 被偷批次里最新的那个先跑
            p.runq.extend(taken[:-1])           # 更早的几个留在本地
            self.stolen.append(g)
            return g
        return None

    @staticmethod
    def runqgrab(victim):
        n = len(victim.runq)
        take = n - n // 2                       # 靠头的一半（ceil）
        taken, left = victim.runq[:take], victim.runq[take:]
        victim.runq = left
        return taken


class Sysmon:
    """sysmon 的休眠间隔：idle==0 时置 20us，idle>50 后每次翻倍，封顶 10ms。"""

    def __init__(self):
        self.delay = 0
        self.idle = 0

    def tick(self):
        if self.idle == 0:
            self.delay = 20
        elif self.idle > 50:
            self.delay *= 2
        if self.delay > SYSMON_MAX_DELAY_US:
            self.delay = SYSMON_MAX_DELAY_US
        self.idle += 1
        return self.delay


def retake_preempt(p, now):
    """retake() 的抢占分支：schedtick 没变且超过 10ms 才抢占。"""
    if p.schedtick != p.smontick_sched:
        p.smontick_sched = p.schedtick
        p.smon_when = now
        return False
    if p.smon_when + FORCE_PREEMPT_NS <= now:
        return True
    return False


def retake_syscall(p, now, runq_empty, idle_or_spinning):
    """retake() 的 syscall 分支：超过 1 个 sysmon tick 就夺回 P；
    但若本地没活、且还有空闲/自旋线程、且不到 10ms，就先不夺。"""
    if p.syscalltick != p.smontick_syscall:
        p.smontick_syscall = p.syscalltick
        p.syscall_when = now
        return False
    if runq_empty and idle_or_spinning and p.syscall_when + FORCE_PREEMPT_NS > now:
        return False
    return True


def cooperative_safe_points(ops):
    """协作式抢占：安全点只在函数调用处（循环里没调用就一个安全点都没有）。"""
    return sum(1 for op in ops if op == "call")


def async_preempt(g, now, running_since):
    """异步抢占：任意指令都能停，除了 unsafe-point。"""
    if g.unsafe:
        return False
    return now - running_since >= FORCE_PREEMPT_NS


def entersyscall(sched, m):
    """M 进系统调用：解绑 P 并把它交给别的 M（handoffp）。"""
    p = m.p
    m.in_syscall = True
    m.curg = None
    if p is not None:
        p.m = None
        p.syscalltick += 1
        m.p = None
        nm = sched.newm()
        nm.p = p
        p.m = nm
        sched.handoffs += 1
        return nm
    return None
