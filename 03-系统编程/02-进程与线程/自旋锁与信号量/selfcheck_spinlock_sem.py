"""自旋锁与信号量自检：断言能证伪的语义（公平性、单核死锁、优先级反转、值不变式）。"""

from spinlock_sem import (
    TASLock, TicketLock, Semaphore, NamedSemTable, Raised,
    contest, single_cpu, priority_inversion, valid_named_sem, bounded_buffer,
    EAGAIN, EINTR, ENOENT, EINVAL, NAME_MAX,
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


# ---- 1. 自旋锁不排队：晚到的可能先拿到 ----
order = [3, 2, 1, 0]                      # 调度顺序与到达顺序相反（完全合法）
acq, over, spins = contest("tas", 4, order)
eq("TAS 的获取顺序就是被调度的顺序", acq, [3, 2, 1, 0])
eq("TAS 的插队次数", over, 6)
ok("TAS 有人空转等锁", spins > 0)

acq, over, _ = contest("ticket", 4, order)
eq("ticket 锁严格按到达顺序放行", acq, [0, 1, 2, 3])
eq("ticket 锁零插队", over, 0)

# ---- 2. 递归上锁是 undefined，不是「返回 EDEADLK」 ----
lock = TASLock()
eq("首次上锁成功", lock.lock(7), True)
eq("持有者再次上锁 → 自旋到死", lock.lock(7, budget=50), "deadlock")
eq("trylock 被持有时失败而非排队", lock.trylock(8), False)
eq("释放后别人能拿到", (lock.unlock(7), lock.trylock(8)), (True, True))

# ---- 3. 单核 + 不可抢占 = 自旋死锁 ----
done, ticks, spins = single_cpu(quantum=0, hold_ticks=3, max_ticks=500)
ok("不可抢占时持有者永远出不来", not done)
eq("自旋者白烧满整段 tick", spins, 500)
done, ticks, spins = single_cpu(quantum=5, hold_ticks=3)
ok("有轮转时持有者能完成", done)
eq("3 tick 的活被 5 tick 的自旋白白垫掉", spins, 5)
ok("总耗时大于实际工作量", ticks == 8 and ticks > 3)

# ---- 4. 优先级反转：自旋锁不提升持有者 ----
wait_spin, m_ran = priority_inversion(pi_aware=False, hold_ticks=3, medium_ticks=100)
eq("自旋锁下 H 要等 M 全部跑完", wait_spin, 103)
eq("M 确实跑满了", m_ran, 100)
wait_pi, m_ran_pi = priority_inversion(pi_aware=True, hold_ticks=3, medium_ticks=100)
eq("PI 提升后 H 只等临界区本身", wait_pi, 3)
eq("PI 提升后 M 一次都没跑", m_ran_pi, 0)
ok("差距就是反转代价", wait_spin - wait_pi == 100)

# ---- 5. 信号量：值永不为负 ----
s = Semaphore(0)
eq("初值 0", s.getvalue(), 0)
b = s.wait(tid=1)
ok("值为 0 时阻塞", b.is_err() is False and not b.waiter.woken)
eq("阻塞不把值改成负数", s.getvalue(), 0)
eq("trywait 在 0 上失败", s.trywait().errno, EAGAIN)
eq("失败后值仍是 0", s.getvalue(), 0)

r = Semaphore(2)
ok("计数信号量允许 2 个并发", r.wait().is_err() is False and r.wait().is_err() is False)
eq("两次 wait 后值归零", r.getvalue(), 0)
blk = r.wait(tid=9)
ok("第三次阻塞", blk.is_err() is False)
eq("post 唤醒等待者", r.post().value, 9)
ok("等待者被标记唤醒", blk.waiter.woken)
eq("被唤醒者拿走了那一份，值仍为 0", r.getvalue(), 0)
eq("无等待者时 post 才真正累加", (r.post().value, r.getvalue()), (None, 1))

# ---- 6. EINTR：出错时值必须保持不变 ----
s2 = Semaphore(0)
before = s2.getvalue()
res = s2.wait(tid=4, signal=True)
ok("被信号打断返回 EINTR", res.is_err() and res.errno == EINTR)
eq("EINTR 后值不变（手册明文）", s2.getvalue(), before)

s3 = Semaphore(1)
res = s3.wait(tid=4, signal=True)
ok("值大于 0 时信号也不影响 wait", res.is_err() is False)
eq("正常 wait 减 1", s3.getvalue(), 0)

# ---- 7. 命名信号量的名字规则与持久性 ----
ok("必须以斜杠开头", not valid_named_sem("somename"))
ok("名字里不能再有斜杠", not valid_named_sem("/a/b"))
ok("最长 251 字符", valid_named_sem("/" + "a" * 250) and not valid_named_sem("/" + "a" * 251))
eq("NAME_MAX-4", NAME_MAX - 4, 251)

tbl = NamedSemTable()
ok("非法名字打不开", tbl.open("noslash").errno == EINVAL)
sem = tbl.open("/jobs", 2).value
eq("首次 open 建立初值", sem.getvalue(), 2)
eq("同名再开拿到同一个对象", tbl.open("/jobs", 99).value is sem, True)
eq("落到 /dev/shm 下的文件名", tbl.shm_path("/jobs"), "/dev/shm/sem.jobs")
tbl.unlink("/jobs")
ok("unlink 后按同名打开失败", tbl.open("/jobs").errno == ENOENT)
eq("但已打开的句柄照常能用", sem.getvalue(), 2)   # 内核持久 + 引用语义

# ---- 8. 有界缓冲：不溢不欠 ----
produced, consumed, left, maxlen, bp, bc = bounded_buffer(2, list("PPPCC"))
eq("生产 2 次后满", produced, 2)
eq("填满时被挡住 1 次（而不是越界写）", bp, 1)
eq("历史最大长度不超过容量", maxlen, 2)
eq("消费 2 次后清空", (consumed, left), (2, 0))
produced, consumed, left, maxlen, bp, bc = bounded_buffer(2, list("CCPP"))
eq("空时消费被挡住 2 次", bc, 2)
eq("空消费没有把值弄成负数", (produced, left), (2, 2))
ok("任意时刻余量守恒", consumed <= produced)

print("自旋锁与信号量自检通过：%d 项" % len(PASS))
for i, name in enumerate(PASS, 1):
    print("  %2d. %s" % (i, name))
