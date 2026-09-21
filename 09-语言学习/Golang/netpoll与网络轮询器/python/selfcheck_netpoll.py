"""Go netpoll 模型的自检：断言来自官方 src/runtime/netpoll.go 的实读结论。"""

import sys

from netpollmodel import (
    PD_NIL, PD_READY, PD_WAIT, G_BASE,
    MODE_R, MODE_W, MODE_RW,
    POLL_NO_ERROR, POLL_ERR_CLOSING, POLL_ERR_TIMEOUT,
    MAX_DEADLINE,
    PollDesc, NetpollRuntime, NetpollError,
    netpollcheckerr, netpollblock, netpollunblock, netpollready,
    poll_runtime_poll_reset, poll_runtime_poll_wait,
    poll_runtime_poll_set_deadline, netpolldeadlineimpl,
    poll_runtime_poll_unblock,
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
    except NetpollError:
        PASS[0] += 1
        return
    except Exception as e:  # noqa
        FAIL[0] += 1
        print("FAIL: %s (raised %r)" % (label, e))
        return
    FAIL[0] += 1
    print("FAIL: %s (no exception)" % label)


def fresh():
    return NetpollRuntime(), PollDesc(7)


# ---------------------------------------------------------------- E1 常量
def e1_constants():
    eq((PD_NIL, PD_READY, PD_WAIT), (0, 1, 2), "E1 pdNil/pdReady/pdWait = 0/1/2")
    ok(G_BASE > PD_WAIT, "E1 G 指针取值大于 pdWait")
    eq((MODE_R, MODE_W, MODE_RW), (114, 119, 233), "E1 mode 取 'r'/'w'/'r'+'w'")
    eq(MAX_DEADLINE, (1 << 63) - 1, "E1 溢出后的 deadline 上限")
    eq((POLL_NO_ERROR, POLL_ERR_CLOSING, POLL_ERR_TIMEOUT), (0, 1, 2), "E1 错误码")


# ---------------------------------------------------------------- E2 消费已就绪通知
def e2_consume_ready():
    rt, pd = fresh()
    pd.rg = PD_READY
    eq(netpollblock(rt, pd, MODE_R), True, "E2 就绪时 block 立即返回 true")
    eq(pd.rg, PD_NIL, "E2 通知被消费，信号量回到 pdNil")
    eq(len(pd.parked), 0, "E2 没有 park")
    eq(rt.waiters, 0, "E2 netpollWaiters 不变")


# ---------------------------------------------------------------- E3 未就绪则 park
def e3_park():
    rt, pd = fresh()
    eq(netpollblock(rt, pd, MODE_R), False, "E3 未就绪时 block 返回 false")
    ok(pd.rg > PD_WAIT, "E3 信号量变成 G 指针")
    eq(len(pd.parked), 1, "E3 挂起了 1 个 goroutine")
    eq(rt.waiters, 1, "E3 netpollWaiters +1（commit 里加的）")
    g = pd.rg
    got, delta = netpollunblock(pd, MODE_R, True, 0)
    eq(got, g, "E3 IO 就绪把 G 换出")
    eq(delta, -1, "E3 换出的是 G 指针 → delta = -1")
    eq(pd.rg, PD_READY, "E3 信号量变成 pdReady（等消费者取走）")


# ---------------------------------------------------------------- E4 pdWait 中间态
def e4_pdwait_race():
    rt, pd = fresh()
    # 手动停在「已置 pdWait 但还没 commit」这一瞬间
    pd.rg = PD_WAIT
    got, delta = netpollunblock(pd, MODE_R, True, 0)
    eq(got, None, "E4 pdWait 状态下 unblock 不返回 goroutine")
    eq(delta, 0, "E4 delta 不变（waiters 还没加过）")
    eq(pd.rg, PD_READY, "E4 信号量直接变成 pdReady")
    eq(netpollblock(rt, pd, MODE_R), True,
       "E4 之后 block 直接消费掉通知，不再阻塞")
    eq(len(pd.parked), 0, "E4 全程没有真正 park")


# ---------------------------------------------------------------- E5 非 IO 就绪的 unblock
def e5_not_ioready():
    rt, pd = fresh()
    got, delta = netpollunblock(pd, MODE_R, False, 0)
    eq(got, None, "E5 pdNil 且非 ioready：什么都不做")
    eq(pd.rg, PD_NIL, "E5 不会凭空设 pdReady")
    # 超时路径：G 被换出但信号量是 pdNil，不是 pdReady
    netpollblock(rt, pd, MODE_R)
    eq(rt.waiters, 1, "E5 阻塞后 waiters=1")
    g = pd.rg
    got, delta = netpollunblock(pd, MODE_R, False, 0)
    eq(got, g, "E5 超时也能唤醒 G")
    eq(pd.rg, PD_NIL, "E5 超时唤醒后信号量是 pdNil（不是 pdReady）")
    eq(delta, -1, "E5 同样 delta=-1")
    rt.adjust_waiters(delta)
    eq(rt.waiters, 0, "E5 唤醒后 waiters 归零")
    # 已就绪时再 unblock 直接返回，不重复
    rt2, pd2 = fresh()
    pd2.rg = PD_READY
    got, delta = netpollunblock(pd2, MODE_R, True, 0)
    eq((got, delta), (None, 0), "E5 已是 pdReady 时 unblock 是 no-op")


# ---------------------------------------------------------------- E6 double wait
def e6_double_wait():
    rt, pd = fresh()
    netpollblock(rt, pd, MODE_R)          # rg 已是 G 指针
    ok(pd.rg > PD_WAIT, "E6 第一次阻塞后是 G 指针")
    raises(lambda: netpollblock(rt, pd, MODE_R), "E6 同一方向二次等待 → double wait")
    eq(netpollblock(rt, pd, MODE_W), False, "E6 换另一个方向不受影响")


# ---------------------------------------------------------------- E7 pollWait 的错误优先
def e7_poll_wait_error_first():
    rt, pd = fresh()
    pd.closing = True
    eq(poll_runtime_poll_wait(rt, pd, MODE_R), POLL_ERR_CLOSING,
       "E7 closing 时 pollWait 返回 ErrClosing")
    eq(len(pd.parked), 0, "E7 出错不阻塞")
    rt2, pd2 = fresh()
    pd2.expired_read = True
    eq(poll_runtime_poll_wait(rt2, pd2, MODE_R), POLL_ERR_TIMEOUT,
       "E7 读 deadline 已过期返回 ErrTimeout")
    eq(len(pd2.parked), 0, "E7 超时已过期也不阻塞")
    # 正常路径：先阻塞，再被 IO 唤醒
    rt3, pd3 = fresh()


# ---------------------------------------------------------------- E8 阻塞后被唤醒
def e8_wake_after_park():
    rt, pd = fresh()
    # 用 non-blocking 的方式看：先手动 park
    netpollblock(rt, pd, MODE_R)
    g = pd.rg
    to_run, delta = netpollready(rt, pd, MODE_R)
    eq(to_run, [g], "E8 netpollready 摘出被阻塞的 goroutine")
    eq(delta, -1, "E8 delta=-1")
    eq(rt.waiters, 0, "E8 waiters 归零")
    eq(pd.rg, PD_READY, "E8 信号量是 pdReady")
    # 'r'+'w' 一次摘两个方向
    rt2, pd2 = fresh()
    netpollblock(rt2, pd2, MODE_R)
    netpollblock(rt2, pd2, MODE_W)
    gr, gw = pd2.rg, pd2.wg
    to_run, delta = netpollready(rt2, pd2, MODE_RW)
    eq(sorted(to_run), sorted([gr, gw]), "E8 'r'+'w' 同时摘出读写两方")
    eq(delta, -2, "E8 两个 G → delta=-2")
    eq(rt2.waiters, 0, "E8 waiters 归零")


# ---------------------------------------------------------------- E9 reset 先查错
def e9_reset():
    rt, pd = fresh()
    pd.rg = PD_READY
    eq(poll_runtime_poll_reset(pd, MODE_R), POLL_NO_ERROR, "E9 正常 reset")
    eq(pd.rg, PD_NIL, "E9 reset 把信号量清回 pdNil")
    pd.closing = True
    pd.rg = PD_READY
    eq(poll_runtime_poll_reset(pd, MODE_R), POLL_ERR_CLOSING, "E9 closing 时先返回错误")
    eq(pd.rg, PD_READY, "E9 出错就不清信号量")


# ---------------------------------------------------------------- E10 deadline
def e10_deadline():
    rt, pd = fresh()
    poll_runtime_poll_set_deadline(rt, pd, 100, MODE_R, now=1000)
    eq(pd.rd, 1100, "E10 deadline 是绝对时刻 now+d")
    ok(pd.rrun, "E10 读 timer 在跑")
    seq = pd.rseq
    # 旧 seq 的 timer 到点会被忽略
    eq(netpolldeadlineimpl(rt, pd, seq - 1, True, False), False,
       "E10 seq 不匹配时 timer 事件被丢弃")
    eq(pd.expired_read, False, "E10 被丢弃后不会标记过期")
    # 改 deadline 会让 seq 前进
    poll_runtime_poll_set_deadline(rt, pd, 200, MODE_R, now=1000)
    eq(pd.rseq, seq + 1, "E10 改 deadline 时 rseq++")
    eq(pd.rd, 1200, "E10 新的绝对时刻")
    # 匹配的 seq 才生效
    eq(netpolldeadlineimpl(rt, pd, pd.rseq, True, False), True, "E10 seq 匹配才生效")
    eq(pd.rd, -1, "E10 到点后 rd 置 -1")
    ok(pd.expired_read, "E10 标记读已过期")
    # 溢出：极大相对时长 → 上限
    rt2, pd2 = fresh()
    poll_runtime_poll_set_deadline(rt2, pd2, MAX_DEADLINE, MODE_R, now=1000)
    eq(pd2.rd, MAX_DEADLINE, "E10 溢出时取 1<<63-1")


# ---------------------------------------------------------------- E11 超时唤醒是「假就绪」
def e11_timeout_wake_is_not_ready():
    rt, pd = fresh()
    netpollblock(rt, pd, MODE_R)
    g = pd.rg
    poll_runtime_poll_set_deadline(rt, pd, 5, MODE_R, now=1000)
    netpolldeadlineimpl(rt, pd, pd.rseq, True, False)
    eq(g in pd.ready, True, "E11 超时把 goroutine 唤醒")
    eq(pd.rg, PD_NIL, "E11 信号量是 pdNil 而不是 pdReady")
    eq(netpollcheckerr(pd, MODE_R), POLL_ERR_TIMEOUT, "E11 再去读会看到 ErrTimeout")
    eq(poll_runtime_poll_wait(rt, pd, MODE_R), POLL_ERR_TIMEOUT,
       "E11 pollWait 返回超时错误而不是阻塞")


# ---------------------------------------------------------------- E12 Unblock
def e12_unblock():
    rt, pd = fresh()
    netpollblock(rt, pd, MODE_R)
    g = pd.rg
    poll_runtime_poll_set_deadline(rt, pd, 5, MODE_R, now=1000)
    rg, wg = poll_runtime_poll_unblock(rt, pd)
    eq(rg, g, "E12 Unblock 摘出读方向的 goroutine")
    eq(wg, None, "E12 写方向没人等")
    ok(pd.closing, "E12 置 closing")
    eq(pd.rrun, False, "E12 停掉读 timer")
    eq(rt.waiters, 0, "E12 waiters 归零")
    raises(lambda: poll_runtime_poll_unblock(rt, pd), "E12 重复 unblock 抛错")
    eq(netpollcheckerr(pd, MODE_R), POLL_ERR_CLOSING, "E12 之后读写都返回 ErrClosing")


# ---------------------------------------------------------------- E13 netpollBreak 去重
def e13_break_dedup():
    rt, pd = fresh()
    eq(rt.netpoll_break(), True, "E13 第一次 netpollBreak 生效")
    eq(rt.wake_sig, 1, "E13 WakeSig 置 1")
    eq(rt.netpoll_break(), False, "E13 未消费前第二次被 CAS 拦掉")
    rt.wake_sig = 0
    eq(rt.netpoll_break(), True, "E13 清零后可再次 break")


def main():
    for fn in (e1_constants, e2_consume_ready, e3_park, e4_pdwait_race,
               e5_not_ioready, e6_double_wait, e7_poll_wait_error_first,
               e8_wake_after_park, e9_reset, e10_deadline,
               e11_timeout_wake_is_not_ready, e12_unblock, e13_break_dedup):
        fn()
    print("PASS=%d FAIL=%d" % (PASS[0], FAIL[0]))
    return 1 if FAIL[0] else 0


if __name__ == "__main__":
    sys.exit(main())
