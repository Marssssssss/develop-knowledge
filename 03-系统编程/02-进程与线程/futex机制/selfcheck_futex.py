"""futex 模型自检：只断言**能证伪**的事实（误报/漏报、位编码、唤醒计数、继承传递性）。"""

from futex_model import (
    FutexWord, FutexKernel, Raised, Blocked,
    futex_op_encode, futex_op_decode, futex_op_apply, futex_op_cmp,
    futex_up, futex_down,
    MASK32, FUTEX_WAITERS, FUTEX_OWNER_DIED, FUTEX_TID_MASK,
    EAGAIN, EPERM, ETIMEDOUT,
    OP_ADD, OP_SET, OP_OR, OP_ANDN, OP_XOR, CMP_EQ, CMP_GE,
)
from futex_pi import PIFutex, PIManager, pi_state_valid

PASS = []


def ok(name, cond):
    if not cond:
        raise AssertionError("FAIL: " + name)
    PASS.append(name)


def eq(name, got, want):
    if got != want:
        raise AssertionError("FAIL: %s got=%r want=%r" % (name, got, want))
    PASS.append("%s (= %r)" % (name, got))


# ---- 1. FUTEX_OP 编码：逐位对齐 uapi/linux/futex.h ----
enc = futex_op_encode(OP_ADD, 1, CMP_EQ, 3)
eq("FUTEX_OP(ADD,1,EQ,3) 位布局", hex(enc), "0x10001003")
eq("FUTEX_OP 手工复核", enc, (1 << 28) | (0 << 24) | (1 << 12) | 3)

# 内核里 op 字段只有低 3 位是操作码，最高位(0x8)是「oparg 当移位数」标志
enc_shift = futex_op_encode(OP_ADD | 0x8, 3, CMP_EQ, 5)
eq("OPARG_SHIFT 位解出 op 仍是 ADD", futex_op_decode(enc_shift)[0], OP_ADD)
eq("OPARG_SHIFT 后 oparg=1<<3", futex_op_decode(enc_shift)[1], 8)
eq("OPARG_SHIFT 解出 cmparg", futex_op_decode(enc_shift)[3], 5)

enc_neg = futex_op_encode(OP_ADD, -1, CMP_EQ, 1)
eq("负 oparg 按 sign_extend32(11) 还原", futex_op_decode(enc_neg)[1], -1)

# 五种原子操作（arch_futex_atomic_op_inuser）
eq("OP_SET 覆盖", futex_op_apply(OP_SET, 7, 0xDEAD), 7)
eq("OP_ADD 回绕 32 位", futex_op_apply(OP_ADD, 1, 0xFFFFFFFF), 0)
eq("OP_OR 置位", futex_op_apply(OP_OR, 0xF0, 0x0F), 0xFF)
eq("OP_ANDN 清位", futex_op_apply(OP_ANDN, 0x0F, 0xFF), 0xF0)
eq("OP_XOR 翻转", futex_op_apply(OP_XOR, 0xFF, 0x0F), 0xF0)
ok("CMP_GE 边界取等", futex_op_cmp(CMP_GE, 4, 4) and not futex_op_cmp(CMP_GE, 3, 4))

# ---- 2. 「比较-并-阻塞」正是防丢失唤醒的那一步 ----
w = FutexWord(1, "lock")
k = FutexKernel()
w.set(0)                       # 持有者先释放、再唤醒（此时还没有等待者）
eq("释放方 WAKE 无等待者", k.futex_wake(w, 1), 0)
r = k.futex_wait(w, 1, tid=2)  # 迟到者拿旧值 1 来等 —— 立刻 EAGAIN 而不是睡死
ok("值不符立刻 EAGAIN", r.is_err() and r.errno == EAGAIN)
eq("EAGAIN 不进等待队列", k.pending(w), 0)

naive = k.futex_wait_naive(w, tid=3)  # 对照组：没有比较步骤就睡死了
ok("朴素阻塞会丢失唤醒", naive.is_err() is False and not naive.waiter.woken)
eq("朴素等待者永远留在队列里", k.pending(w), 1)

# ---- 3. FUTEX_WAKE 返回**实际唤醒数** ----
k = FutexKernel()
w = FutexWord(0, "q")
blocks = [k.futex_wait(w, 0, tid) for tid in (1, 2, 3)]
ok("三个等待者都睡下了", all(b.is_err() is False for b in blocks))
eq("WAKE(1) 只醒 1 个", k.futex_wake(w, 1), 1)
eq("WAKE(5) 醒剩下 2 个而非 5", k.futex_wake(w, 5), 2)
eq("队列空后 WAKE 返回 0", k.futex_wake(w, 5), 0)
eq("累计唤醒数", k.wakeups, 3)

# ---- 4. 返回 0 也可能是假唤醒，必须用 word 值复判 ----
k = FutexKernel()
w = FutexWord(0, "spurious")
b = k.futex_wait(w, 0, tid=7)
eq("无关代码(如 pthread mutex 复用同一 word)也能唤醒", k.futex_wake(w, 1), 1)
ok("被唤醒但 word 仍是 0", b.waiter.woken and w.value == 0)
b2 = k.futex_wait(w, 0, tid=7)      # 正确实现：回环再等一次
ok("复判后重新等待", b2.is_err() is False and not b2.waiter.woken)

# ---- 5. FUTEX_WAIT 的 timeout 是相对值，其它操作是绝对值 ----
k = FutexKernel()
w = FutexWord(0, "t")
b = k.futex_wait(w, 0, tid=1, rel_timeout=5)
eq("相对 5ms：推进 4 还不超时", len(k.advance(4)), 0)
eq("相对 5ms：推进到 5 才超时", len(k.advance(1)), 1)
ok("超时等待者被置位", b.waiter.timedout)

k = FutexKernel()
k.advance(10)                        # 时钟已在 10
r = k.futex_wait(w, 0, tid=1, rel_timeout=5, absolute=True)
ok("绝对 deadline=5 < now=10 → 立即 ETIMEDOUT", r.is_err() and r.errno == ETIMEDOUT)

# ---- 6. FUTEX_WAKE_OP：uaddr1 无条件唤醒，比较只看 uaddr2 的旧值 ----
k = FutexKernel()
u1, u2 = FutexWord(0, "u1"), FutexWord(3, "u2")
k.futex_wait(u1, 0, 1)
k.futex_wait(u1, 0, 2)
k.futex_wait(u2, 3, 3)  # u2 初值就是 3，等待者必须拿当前值来等
enc = futex_op_encode(OP_ADD, 1, CMP_EQ, 4)
woken1, woken2, old = k.futex_wake_op(u1, 1, 1, u2, enc)
eq("WAKE_OP 取到的旧值", old, 3)
eq("比较失败时 uaddr1 仍被唤醒 1 个", woken1, 1)
eq("比较失败不唤醒 uaddr2", woken2, 0)
eq("但原子操作照做(3+1)", u2.value, 4)
woken1, woken2, old = k.futex_wake_op(u1, 1, 1, u2, enc)
eq("第二次旧值", old, 4)
eq("比较成立才唤醒 uaddr2", woken2, 1)
eq("uaddr2 被原子加为 5", u2.value, 5)

# ---- 7. REQUEUE 与 CMP_REQUEUE ----
k = FutexKernel()
a, bword = FutexWord(0, "a"), FutexWord(0, "b")
for tid in range(4):
    k.futex_wait(a, 0, tid)
woken, moved = k.futex_requeue(a, 1, 2, bword)
eq("REQUEUE 唤醒数", woken, 1)
eq("REQUEUE 搬运数", moved, 2)
eq("源队列剩 1", k.pending(a), 1)
eq("目标队列有 2", k.pending(bword), 2)
eq("唤醒目标队列", k.futex_wake(bword, 5), 2)
eq("唤醒源队列残余", k.futex_wake(a, 5), 1)

k = FutexKernel()
a2, b2 = FutexWord(7, "a2"), FutexWord(0, "b2")
k.futex_wait(a2, 7, 1)
k.futex_wait(a2, 7, 2)
r = k.futex_cmp_requeue(a2, 1, 2, b2, 9)
ok("CMP_REQUEUE 值不符 → EAGAIN", r.is_err() and r.errno == EAGAIN)
eq("EAGAIN 时一个都不搬", (k.pending(a2), k.pending(b2)), (2, 0))
eq("CMP_REQUEUE 值相符", k.futex_cmp_requeue(a2, 1, 2, b2, 7), (1, 1))

# ---- 8. futex word 恒为 32 位 ----
w = FutexWord(0, "w32")
eq("-1 存成 0xFFFFFFFF", w.set(-1), 0xFFFFFFFF)
eq("超出 32 位被截断", w.set(0x100000000), 0)
eq("掩码常量", MASK32, 0xFFFFFFFF)

# ---- 9. futex(7) 的裸计数协议 ----
k = FutexKernel()
c = FutexWord(0, "counter")
eq("up: 0→1 走快路径", futex_up(k, c), ("fast", 0))
eq("up 快路径零系统调用", k.syscalls, 0)
eq("down: 1→0 走快路径", futex_down(k, c, 1), ("fast", None))
kind, res = futex_down(k, c, 2)
eq("down 无可用计数 → 等待", kind, "wait")
eq("等待时 word 被置 -1", hex(c.value), "0xffffffff")
ok("down 走的是阻塞路径", res.is_err() is False)
eq("up 唤醒 1 个并把计数恢复为 1", futex_up(k, c), ("wake", 1))
eq("唤醒后计数", c.value, 1)

# ---- 10. PI futex 的字面量编码 ----
k = FutexKernel()
pi = PIFutex(k)
ok("无竞争：用户态取锁成功", pi.trylock_userspace(11).is_err() is False)
eq("无竞争全程零系统调用", k.syscalls, 0)
eq("已锁时 word 就是 TID", pi.w.value, 11)
blk = pi.lock_pi(12)
ok("竞争路径进入内核阻塞", blk.is_err() is False)
eq("置位后 word = FUTEX_WAITERS|TID", hex(pi.w.value), hex(FUTEX_WAITERS | 11))
eq("TID 字段仍是可解析的", pi.w.tid, 11)
ok("WAITERS 标志生效", pi.w.has_waiters)
ok("有主有等待者是合法状态", pi_state_valid(pi.w))
ok("无主却有 WAITERS 是非法状态", not pi_state_valid(FutexWord(FUTEX_WAITERS)))
ok("非持有者 unlock → EPERM", pi.unlock_pi(13).errno == EPERM)
eq("持有者 unlock 把锁交给等待者", pi.unlock_pi(11).value, 12)
eq("交接后无等待者 → 不置 WAITERS", pi.w.value, 12)
eq("等待队列已空", k.pending(pi.w), 0)

k = FutexKernel()
pi = PIFutex(k)
pi.trylock_userspace(21)
dead = pi.lock_pi(22)
eq("持有者猝死后交给下一位", pi.owner_dies(), 22)
eq("OWNER_DIED 位被置起", hex(pi.w.value & FUTEX_OWNER_DIED), hex(FUTEX_OWNER_DIED))
ok("内核同时唤醒了新主人", dead.waiter.woken)
eq("TID 掩码", FUTEX_TID_MASK, 0x3FFFFFFF)

# ---- 11. 优先级继承必须传递 ----
m = PIManager()
m.add("H", 10)
m.add("M", 5)
m.add("L", 1)
m.add("R", 7)
m.acquire("L", "lock1")
m.acquire("M", "lock2")
m.block("H", "lock1")
eq("低优先级持有者被提到 10", m.eff["L"], 10)
m.block("L", "lock2")
eq("继承沿锁链传递到中间优先级", m.eff["M"], 10)
eq("无关任务不被提升", m.eff["R"], 7)
eq("被提升者压过中等优先级任务", m.highest_runnable(), "M")

print("futex 模型自检通过：%d 项" % len(PASS))
for i, name in enumerate(PASS, 1):
    print("  %2d. %s" % (i, name))
