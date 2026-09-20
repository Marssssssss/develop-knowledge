"""GMP 调度器自检：断言取自 Go 运行时源码的常量与分支行为。"""

from gmp_model import (
    G, P, M, Scheduler, Sysmon, retake_preempt, retake_syscall,
    cooperative_safe_points, async_preempt, entersyscall, status_of,
    _Gidle, _Grunnable, _Grunning, _Gsyscall, _Gwaiting, _Gdead,
    _Gcopystack, _Gpreempted, _Genqueue_unused, _Gscan, _Gscanrunning,
    RUNQ_CAP, FORCE_PREEMPT_NS, GLOBAL_CHECK_EVERY, GLOBAL_BATCH,
)

PASS = []


def ok(name, cond):
    if not cond:
        raise AssertionError("FAIL: " + name)
    PASS.append(name)


def eq(name, got, want):
    if got != want:
        raise AssertionError("FAIL: %s got=%r want=%r" % (name, got, want))
    PASS.append("%s (= %r)" % (name, got))


# ---- 1. G 状态枚举与 _Gscan 叠加位 ----
eq("_Gscan 是 0x1000", _Gscan, 0x1000)
eq("_Gscanrunning 的低位仍是 _Grunning", status_of(_Gscanrunning), _Grunning)
eq("状态枚举（0/1/2/3/4/6/8/9）",
   (_Gidle, _Grunnable, _Grunning, _Gsyscall, _Gwaiting, _Gdead, _Gcopystack, _Gpreempted),
   (0, 1, 2, 3, 4, 6, 8, 9))
eq("5 与 7 是历史保留位", (_Genqueue_unused, 5), (7, 5))

# ---- 2. runq 容量与「满则搬一半到全局」 ----
eq("runq 容量", RUNQ_CAP, 256)
eq("全局批量 = len(runq)/2（按容量算，恒 128）", GLOBAL_BATCH, 128)

s = Scheduler(nprocs=1)
p = s.allp[0]
for i in range(256):
    s.runqput(p, G(i))
eq("本地填满 256", len(p.runq), 256)
moved = s.runqput(p, G(999))
eq("满后再放：搬走 128 + 新的 1 个 = 129", moved, 129)
eq("全局队列收到 129", len(s.globalq), 129)
eq("本地只剩 128", len(p.runq), 128)

# ---- 3. runnext：后就绪的先跑，旧的被踢到队尾 ----
s = Scheduler(nprocs=1)
p = s.allp[0]
g1, g2, g3 = G(1, "g1"), G(2, "g2"), G(3, "g3")
s.runqput(p, g1)
eq("第一个进本地队列", p.runq, [g1])
eq("put(next=True) 占住 runnext", (s.runqput(p, g2, next=True), p.runnext), ("runnext", g2))
eq("再 put(next=True)：旧的被踢进本地队尾", (s.runqput(p, g3, next=True), p.runnext), ("local", g3))
eq("被踢下来的排在 g1 之后", p.runq, [g1, g2])
eq("取的顺序：runnext 优先", s.runqget(p), g3)
eq("然后才是本地 FIFO", (s.runqget(p), s.runqget(p)), (g1, g2))

s_no_sysmon = Scheduler(nprocs=1)
s_no_sysmon.have_sysmon = False
p2 = s_no_sysmon.allp[0]
s_no_sysmon.runqput(p2, g1, next=True)
eq("没有 sysmon 时禁用 runnext（防止一对互唤醒的 G 饿死别人）", (p2.runnext, p2.runq), (None, [g1]))

# ---- 4. 全局队列每 61 个 schedtick 才查一次 ----
s = Scheduler(nprocs=1)
p = s.allp[0]
s.globalq.extend(G(1000 + i) for i in range(300))
checks = 0
for i in range(61):
    _, src = s.find_runnable(p)
    if src == "global":
        checks += 1
eq("61 次调度里只查了 1 次全局队列", checks, 1)
eq("一次拿走 128 个（批量上限）", len(s.globalq), 300 - 128)
eq("拿回的第一个直接运行，其余 127 个进本地", len(p.runq), 127)

# ---- 5. 工作窃取：偷靠头的一半，跑其中最新的那个 ----
s = Scheduler(nprocs=2)
victim, thief = s.allp[0], s.allp[1]
victim.runq = [G(i, "v%d" % i) for i in range(8)]
stolen = s.steal(thief)
eq("8 个偷走 ceil(8/2)=4 个", len(thief.runq) + 1, 4)
eq("被偷的是靠头的 v0..v3，小偷手里只剩 v0..v2", [g.name for g in thief.runq], ["v0", "v1", "v2"])
eq("先跑的是这批里最新的 v3", stolen.name, "v3")
eq("受害者手里剩 v4..v7", [g.name for g in victim.runq], ["v4", "v5", "v6", "v7"])

victim.runq = [G(i, "w%d" % i) for i in range(7)]
thief.runq = []
stolen = s.steal(thief)
eq("7 个也偷 4 个（ceil）", (len(thief.runq) + 1, stolen.name), (4, "w3"))
eq("受害者剩 3 个", len(victim.runq), 3)

victim.runq = []
victim.runnext = G(42, "runnext-g")
thief.runq = []
stolen = s.steal(thief)
eq("本地空时最后手段：偷 runnext", stolen.name, "runnext-g")
eq("被偷走后 runnext 归零", victim.runnext, None)

s = Scheduler(nprocs=2)
s.allp[0].runq = [G(0, "x")]
s.allp[1].runq = []
eq("不偷空闲 P（这里 P0 有活，故能偷到）", s.steal(s.allp[1]).name, "x")
eq("两边都空时偷不到", s.steal(s.allp[1]), None)

# ---- 6. sysmon 的休眠间隔：20us 起步，idle>50 后翻倍，封顶 10ms ----
sm = Sysmon()
seq = [sm.tick() for _ in range(200)]
eq("起步 20us", seq[0], 20)
eq("第 51 次仍是 20us（还没到 idle>50）", seq[50], 20)
eq("idle=51 时开始翻倍", seq[51], 40)
eq("第 60 次撞上 10ms 上限", seq[59], 10000)
eq("之后再也不超过 10ms", max(seq[59:]), 10000)
eq("封顶前的真实值本该是 10240", 20 * (2 ** 9), 10240)

# ---- 7. 抢占：schedtick 不变且超过 10ms ----
p = P(0)
p.schedtick = 7
p.smontick_sched = 7
p.smon_when = 0
ok("9.999ms 时还不抢占", not retake_preempt(p, FORCE_PREEMPT_NS - 1))
ok("到 10ms 就抢占", retake_preempt(p, FORCE_PREEMPT_NS))
p.schedtick = 8
ok("schedtick 变过就重新计时（换了个 G 在跑）", not retake_preempt(p, FORCE_PREEMPT_NS))
eq("重新计时后起点被刷新", p.smon_when, FORCE_PREEMPT_NS)

# ---- 8. syscall 中的 P 夺回 ----
p = P(0)
p.syscalltick = 1
p.smontick_syscall = 1
p.syscall_when = 0
ok("本地有活 → 立刻夺回 P", retake_syscall(p, 1000, runq_empty=False, idle_or_spinning=True))
p2 = P(1)
p2.syscalltick = 1
p2.smontick_syscall = 1
p2.syscall_when = 0
ok("本地没活 + 还有空闲线程 + 不到 10ms → 先不夺",
   not retake_syscall(p2, 1000, runq_empty=True, idle_or_spinning=True))
ok("超过 10ms 就一定要夺回", retake_syscall(p2, FORCE_PREEMPT_NS, runq_empty=True, idle_or_spinning=True))

# ---- 9. 协作式 vs 异步抢占 ----
tight_loop = ["add", "cmp", "jmp"] * 1000
eq("纯循环一个安全点都没有", cooperative_safe_points(tight_loop), 0)
eq("有函数调用时才有安全点", cooperative_safe_points(["call", "add", "call"]), 2)

g = G(1, "busy")
ok("协作式：无调用的循环永远等不到抢占", cooperative_safe_points(tight_loop) == 0)
ok("异步：跑满 10ms 就抢", async_preempt(g, FORCE_PREEMPT_NS, 0))
ok("异步：9.999ms 不抢", not async_preempt(g, FORCE_PREEMPT_NS - 1, 0))
g.unsafe = True       # unsafe.Pointer→uintptr 存活期间 / write barrier 中间
ok("异步也绕开 unsafe-point", not async_preempt(g, 50 * 1000 * 1000, 0))
g.unsafe = False
ok("离开 unsafe-point 后立刻可抢", async_preempt(g, 50 * 1000 * 1000, 0))

# ---- 10. M 进系统调用：P 被摘下来交给别的 M，并行度不变 ----
s = Scheduler(nprocs=4)
m0 = s.newm()
m0.p = s.allp[0]
s.allp[0].m = m0
old_p = m0.p
nm = entersyscall(s, m0)
ok("原 M 已经没有 P 了", m0.p is None and m0.in_syscall)
ok("P 被交给了新的 M", nm is not None and nm.p is old_p and old_p.m is nm)
eq("handoff 记了一次", s.handoffs, 1)
eq("并行度仍是 GOMAXPROCS", s.gomaxprocs, 4)
eq("syscalltick 递增（sysmon 靠它判断换没换过）", old_p.syscalltick, 1)

print("GMP 调度器自检通过：%d 项" % len(PASS))
for i, name in enumerate(PASS, 1):
    print("  %2d. %s" % (i, name))
