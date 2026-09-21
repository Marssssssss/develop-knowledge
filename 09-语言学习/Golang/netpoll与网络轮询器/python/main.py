"""Go 网络轮询器演示：rg/wg 两个二值信号量的状态迁移。"""

from netpollmodel import (
    PD_NIL, PD_READY, PD_WAIT, MODE_R, MODE_W, MODE_RW, PollDesc, NetpollRuntime,
    netpollblock, netpollready, netpollcheckerr, poll_runtime_poll_wait,
    poll_runtime_poll_set_deadline, netpolldeadlineimpl, poll_runtime_poll_unblock,
)

NAME = {PD_NIL: "pdNil", PD_READY: "pdReady", PD_WAIT: "pdWait"}


def sem(v):
    return NAME.get(v, "G(%d)" % v)


def main():
    print("== 1. 一个 fd 上的读阻塞 → IO 就绪唤醒 ==")
    rt, pd = NetpollRuntime(), PollDesc(7)
    print("  初始 rg=%s" % sem(pd.rg))
    r = netpollblock(rt, pd, MODE_R)
    print("  netpollblock -> %s ; rg=%s ; waiters=%d" % (r, sem(pd.rg), rt.waiters))
    to_run, delta = netpollready(rt, pd, MODE_R)
    print("  netpollready -> toRun=%s delta=%d ; rg=%s ; waiters=%d"
          % (to_run, delta, sem(pd.rg), rt.waiters))

    print("\n== 2. 先收到通知再等：通知被消费，不阻塞 ==")
    rt2, pd2 = NetpollRuntime(), PollDesc(8)
    pd2.rg = PD_READY
    print("  (内核已把 fd 标记就绪) rg=%s" % sem(pd2.rg))
    print("  netpollblock -> %s ; rg=%s ; parked=%d"
          % (netpollblock(rt2, pd2, MODE_R), sem(pd2.rg), len(pd2.parked)))

    print("\n== 3. 超时唤醒不是「就绪」：信号量是 pdNil 而不是 pdReady ==")
    rt3, pd3 = NetpollRuntime(), PollDesc(9)
    netpollblock(rt3, pd3, MODE_R)
    poll_runtime_poll_set_deadline(rt3, pd3, 5, MODE_R, now=1000)
    netpolldeadlineimpl(rt3, pd3, pd3.rseq, True, False)
    print("  deadline 到点后 rg=%s ; checkerr=%d ; pollWait=%d"
          % (sem(pd3.rg), netpollcheckerr(pd3, MODE_R),
             poll_runtime_poll_wait(rt3, pd3, MODE_R)))
    print("  ↑ checkerr=2 是 ErrTimeout、pollWait=2 说明不再阻塞而是报错")

    print("\n== 4. 关闭：两边一起解阻塞 ==")
    rt4, pd4 = NetpollRuntime(), PollDesc(10)
    netpollblock(rt4, pd4, MODE_R)
    netpollblock(rt4, pd4, MODE_W)
    print("  阻塞中 rg=%s wg=%s waiters=%d" % (sem(pd4.rg), sem(pd4.wg), rt4.waiters))
    rg, wg = poll_runtime_poll_unblock(rt4, pd4)
    print("  Unblock -> rg=%s wg=%s closing=%s waiters=%d"
          % ("G" if rg else "nil", "G" if wg else "nil", pd4.closing, rt4.waiters))


if __name__ == "__main__":
    main()
