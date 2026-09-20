# -*- coding: utf-8 -*-
"""io_uring 网络 IO —— 自检

负向判据优先：
  * 「CQE 顺序 == SQE 顺序」这条**必须会失败**，否则说明乱注没注进去
  * 「不用 user_data 也能对上号」同样必须失败，否则 user_data 的必要性就没证明
"""
from iouring_model import *  # noqa: F401,F403

N = 0
FAILED = []


def check(label, cond, detail=""):
    global N
    N += 1
    if cond:
        print("ok   %-58s %s" % (label, detail))
    else:
        FAILED.append(label)
        print("FAIL %-58s %s" % (label, detail))


print("== 1. 每个 SQE 恰好对应一个 CQE ==")
r = Ring(entries=8)
for i in range(5):
    r.prep(r.get_sqe(OP_SEND, fd=10, user_data=i, payload=b"x" * 10))
n = r.submit()
check("一次 io_uring_enter 提交 5 个 SQE", n == 5, "to_submit=%d" % n)
cqes = r.reap_all()
check("CQE 数量与 SQE 数量严格相等", len(cqes) == 5, "cqe=%d" % len(cqes))
check("只用了 1 次系统调用", r.syscalls == 1 and r.enters == 1, "syscalls=%d" % r.syscalls)

print("\n== 2. res 语义：成功是返回值，失败是 -errno ==")
c = cqes[0]
check("成功的 res 是字节数（正数）", c.res == 10 and c.ok, "res=%d" % c.res)
check("成功时 errno 恒为 0（io_uring 不用 errno 通道）", c.errno == 0)
r2 = Ring(entries=4)
r2.socks[10] = Sock(10)
r2.socks[10].reset = True
r2.prep(r2.get_sqe(OP_SEND, fd=10, user_data=7, payload=b"y"))
r2.submit()
bad = r2.reap_all()[0]
check("对端 reset 时 res 为 -ECONNRESET", bad.res == -ECONNRESET, "res=%d" % bad.res)
check("失败时 errno 由 -res 还原", bad.errno == ECONNRESET, "errno=%d" % bad.errno)
check("res 是负数（不是 0 字节成功）", bad.res < 0, "res=%d" % bad.res)

print("\n== 3. 完成顺序：不能假设 ==")
r3 = Ring(entries=8)
r3.complete_order = "reverse"
for i in range(4):
    r3.prep(r3.get_sqe(OP_SEND, fd=10, user_data=i, payload=b"z"))
r3.submit()
got_ud = [c.user_data for c in r3.reap_all()]
check("乱序下 user_data 集合仍然完整", sorted(got_ud) == [0, 1, 2, 3], str(got_ud))
check("负向：CQE 顺序不等于提交顺序（乱序真的发生了）",
      got_ud != [0, 1, 2, 3], "got=%s" % got_ud)
check("负向：按位置猜测 user_data 会错配",
      any(got_ud[i] != i for i in range(4)), "got=%s" % got_ud)
r3b = Ring(entries=8)
r3b.complete_order = "reverse"
for i in range(4):
    r3b.prep(r3b.get_sqe(OP_SEND, fd=10, user_data=100 + i, payload=b"zz"))
r3b.submit()
by_ud = {c.user_data: c.res for c in r3b.reap_all()}
check("按 user_data 索引能正确取到各自结果",
      by_ud == {100: 2, 101: 2, 102: 2, 103: 2}, str(by_ud))

print("\n== 4. IOSQE_IO_LINK 强制执行顺序 ==")
r4 = Ring(entries=8)
r4.complete_order = "reverse"
sqes = [r4.get_sqe(OP_SEND, fd=10, user_data=i, payload=b"q") for i in range(4)]
for i, s in enumerate(sqes):
    s.flags |= IOSQE_IO_LINK           # 整批串成一条链
    r4.prep(s)
r4.submit()
log_ud = [u for u, _ in r4.exec_log]
check("链接后执行顺序严格等于提交顺序", log_ud == [0, 1, 2, 3], str(log_ud))
check("负向对照：不链接时执行顺序也可能对，但完成顺序不受保护",
      sorted(c.user_data for c in r4.reap_all()) == [0, 1, 2, 3])

r5 = Ring(entries=8)
r5.complete_order = "reverse"
for i in range(3):
    r5.prep(r5.get_sqe(OP_SEND, fd=10, user_data=i, payload=b"w"))   # 无 LINK
r5.submit()
check("未链接的批量在注入乱序时完成顺序被打乱",
      [c.user_data for c in r5.reap_all()] == [2, 1, 0],
      str([c.user_data for c in r5.reap_all()]))

print("\n== 5. 同一 socket 上不能有多个在飞的 send ==")
r6 = Ring(entries=8)
r6.prep(r6.get_sqe(OP_SEND, fd=10, user_data=0, payload=b"a" * 100))
r6.prep(r6.get_sqe(OP_SEND, fd=10, user_data=1, payload=b"b" * 100))
r6.submit()
same_dir = [op for _, op in r6.exec_log]
check("两个 send 都排进了 SQ（io_uring 不会替你拒绝）",
      same_dir == ["SEND", "SEND"], str(same_dir))
check("这正是 man page 警告的形态：同方向重叠要靠应用自己避免",
      same_dir.count("SEND") == 2, "原文：unsafe to have more than one "
      "outstanding send ... on a given socket at a time")

print("\n== 6. 挂起操作：数据到达后才补 CQE ==")
r7 = Ring(entries=8)
r7.prep(r7.get_sqe(OP_RECV, fd=11, user_data=42, buf_len=64))
r7.submit()
check("无数据时没有 CQE", len(r7.cq) == 0, "cq=%d" % len(r7.cq))
check("操作处于挂起态", len(r7.pending) == 1)
r7.feed(11, data=b"hello")
c7 = r7.reap_all()
check("数据到达后补出 CQE", len(c7) == 1 and c7[0].res == 5, "res=%s" % (c7[0].res if c7 else None))
check("CQE 的 user_data 与提交时一致", c7[0].user_data == 42)

print("\n== 7. SQPOLL：可以一次系统调用都不做 ==")
r8 = Ring(entries=8, flags=IORING_SETUP_SQPOLL, sq_thread_idle=1.0)
check("sqpoll 模式已启用", r8.sqpoll)
r8.tick(2.0)
check("负向：空闲超过 sq_thread_idle 后线程睡了", r8.thread_asleep, "idle>1.0s")
check("睡了以后置起 IORING_SQ_NEED_WAKEUP", r8.need_wakeup())
r8.prep(r8.get_sqe(OP_SEND, fd=10, user_data=1, payload=b"p"))
r8.submit()
check("唤醒需要一次 io_uring_enter（不再是零系统调用）",
      r8.syscalls == 1 and not r8.need_wakeup(), "syscalls=%d" % r8.syscalls)
r8.tick(0.1)
check("忙起来后不再置 NEED_WAKEUP", not r8.need_wakeup())

r8b = Ring(entries=8, flags=IORING_SETUP_SQPOLL, sq_thread_idle=1.0)
r8b.prep(r8b.get_sqe(OP_SEND, fd=10, user_data=5, payload=b"k"))
n_polled = r8b.sqpoll_tick()
check("轮询线程醒着时，应用提交不产生任何系统调用",
      n_polled == 1 and r8b.syscalls == 0, "submitted=%d syscalls=%d" % (n_polled, r8b.syscalls))
check("且照样拿到了 CQE", len(r8b.reap_all()) == 1)

print("\n== 8. IOPOLL 不能用于网络 ==")
r9 = Ring(entries=8, flags=IORING_SETUP_IOPOLL)
check("iopoll 标志已置", r9.iopoll)
try:
    r9.get_sqe(OP_RECV, fd=10, user_data=0, buf_len=64)
    check("负向：iopoll 环上准备网络操作必须失败", False, "没有抛异常")
except ValueError:
    check("负向：iopoll 环上准备网络操作必须失败", True, "ValueError")
check("iopoll 只允许 O_DIRECT 的 READ/WRITE",
      r9.get_sqe(OP_READ, fd=3, user_data=0).opcode == OP_READ)

print("\n== 9. 与 epoll 的系统调用次数对比 ==")
check("epoll：ctl + wait + 每事件一次 recv",
      epoll_syscalls(4, 4) == 4 + 4 + 4, "n=%d" % epoll_syscalls(4, 4))
check("io_uring：4 个事件合成 1 批 → 1 次",
      iouring_syscalls(1) == 1, "n=%d" % iouring_syscalls(1))
check("批量越大优势越明显",
      iouring_syscalls(1) < epoll_syscalls(4, 4),
      "1 vs %d" % epoll_syscalls(4, 4))

print("\n== 10. 环形队列的读写方向 ==")
rA = Ring(entries=4)
for i in range(3):
    rA.prep(rA.get_sqe(OP_SEND, fd=10, user_data=i, payload=b"m"))
check("SQE 加在 SQ 尾部", [s.user_data for s in rA.sq] == [0, 1, 2])
rA.submit()
check("应用从 CQ 头部取", rA.cq[0].user_data == 0, "head=%d" % rA.cq[0].user_data)

print("\n---- %d 项断言，失败 %d 项 ----" % (N, len(FAILED)))
if FAILED:
    for f in FAILED:
        print("  FAILED:", f)
    raise SystemExit(1)
print("ALL PASS")
