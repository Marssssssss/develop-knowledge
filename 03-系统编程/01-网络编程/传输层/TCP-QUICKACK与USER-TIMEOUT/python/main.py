"""演示入口:把 TCP_QUICKACK / TCP_USER_TIMEOUT 的几条关键口径跑成表。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dack_model import (                                  # noqa: E402
    DelackEngine, TCP_ATO_MIN, TCP_DELACK_MAX, TCP_DELACK_MIN, TCP_RTO_MIN,
    TCP_RTO_MAX, TCP_MAX_QUICKACKS, model_timeout, retransmits_timed_out,
    clamp_rto_to_user_timeout, clamp_probe0_to_user_timeout,
    probe_timer_decision,
)


def main():
    print("== 常量(HZ=1000,统一以 ms 计) ==")
    print("  TCP_ATO_MIN / TCP_DELACK_MIN = %d / %d ms" % (TCP_ATO_MIN, TCP_DELACK_MIN))
    print("  TCP_DELACK_MAX = %d ms   TCP_RTO_MIN = %d ms   TCP_RTO_MAX = %d ms"
          % (TCP_DELACK_MAX, TCP_RTO_MIN, TCP_RTO_MAX))
    print("  TCP_MAX_QUICKACKS = %d" % TCP_MAX_QUICKACKS)

    print("\n== ① tcp_incr_quickack:quick 配额 = rcv_wnd/(2*rcv_mss) ==")
    print("  %-12s %-10s %-8s" % ("rcv_wnd", "rcv_mss", "quick"))
    for wnd, mss in ((65536, 1460), (1460 * 6, 1460), (1460, 1460), (0, 1460)):
        e = DelackEngine(mss, wnd)
        print("  %-12d %-10d %-8d" % (wnd, mss, e.incr_quickack(TCP_MAX_QUICKACKS)))

    print("\n== ② ato 随到达间隔自适应(rcv_mss=1460, rcv_wnd=65536) ==")
    print("  %-8s %-8s %-8s %s" % ("到达", "m", "ato", "落进哪条分支"))
    e = DelackEngine(1460, 65536)
    prev = 0
    for t in (0, 1, 3, 8, 20, 41, 71, 101, 201, 500):
        m = t - prev
        old = e.ato
        ato = e.on_data_recv(t)
        if old == 0:
            br = "首包:ato=0 -> incr_quickack + ato=ATO_MIN"
        elif m <= TCP_ATO_MIN // 2:
            br = "m<=ATO_MIN/2:ato=ato/2+20"
        elif m < old:
            br = "m<ato:ato=min(ato/2+m, rto, DELACK_MAX)"
        elif m > e.rto:
            br = "m>rto:重新 incr_quickack"
        else:
            br = "空档(既不 <ato 也不 >rto):什么都不做"
        print("  %-8d %-8d %-8d %s" % (t, m, ato, br))
        prev = t

    print("\n== ③ delack 定时器取值:pingpong / srtt / PUSHED 三道上限 ==")
    print("  %-8s %-12s %-10s %-10s %-10s" % ("ato", "srtt(ms)", "pingpong", "未封顶", "最终"))
    for ato, srtt, pp, pushed in ((40, 0, False, False), (41, 0, False, False),
                                  (300, 0, False, False), (41, 8, False, False),
                                  (300, 2000, False, False), (300, 2000, True, False),
                                  (300, 0, False, True)):
        g = DelackEngine()
        g.ato, g.srtt_us, g.pingpong = ato, srtt * 1000, pp
        g.pending = DelackEngine.ACK_PUSHED if pushed else 0
        print("  %-8d %-12d %-10s %-10d %-10d"
              % (ato, srtt, pp, g.delack_timeout(False), g.delack_timeout()))

    print("\n== ④ retransmits_timed_out:默认 model_timeout ==")
    print("  %-10s %-14s %s" % ("boundary", "timeout(ms)", "折合"))
    for b in (0, 1, 5, 9, 10, 15):
        t = model_timeout(b)
        print("  %-10d %-14d %.1f s" % (b, t, t / 1000.0))
    print("  → tcp_retries2=15 的默认超时是 924.6 s ≈ 15.4 分钟(熟知的那个数字)")

    print("\n== ⑤ TCP_USER_TIMEOUT 如何改写上面这张表 ==")
    print("  %-14s %-10s %-10s %-12s" % ("user_timeout", "elapsed", "rto", "装填值"))
    for u, el in ((0, 5000), (10000, 0), (10000, 9900), (10000, 10000), (10000, 99999),
                  (1, 0)):
        print("  %-14d %-10d %-10d %-12d"
              % (u, el, TCP_RTO_MIN, clamp_rto_to_user_timeout(u, TCP_RTO_MIN, el)))
    print("  注:user_timeout=0 表示「用系统默认」,不是「立刻超时」")

    print("\n== ⑥ 零窗口探测:user_timeout 让 RFC 1122 的「无限等待」变成有界 ==")
    print("  %-14s %-10s %-10s %-10s %s" % ("user_timeout", "tstamp", "now", "probes", "判定"))
    for u, ts, now, po in ((0, 1000, 99999, 5), (0, 0, 2000, 0), (10000, 1000, 10999, 3),
                           (10000, 1000, 11000, 3), (10000, 1000, 10999, 15)):
        print("  %-14d %-10d %-10d %-10d %s"
              % (u, ts, now, po, probe_timer_decision(u, ts, now, po, 15, 0, True)))

    print("\n== ⑦ 零窗口探测定时器的钳制(另一条 user_timeout 路径) ==")
    print("  %-14s %-10s %-10s %-10s %s" % ("user_timeout", "tstamp", "now", "when", "结果"))
    for u, ts, now, when in ((0, 1000, 11000, 500), (10000, 1000, 2000, 500),
                             (10000, 1000, 11000, 9000), (10000, 1000, 500, 9000)):
        print("  %-14d %-10d %-10d %-10d %d"
              % (u, ts, now, when, clamp_probe0_to_user_timeout(u, when, ts, now)))

    print("\n== ⑧ 一句话对照 ==")
    print("  retransmits_timed_out:未重传过 -> %s;到点 -> %s"
          % (retransmits_timed_out(15, 0, 0, 924600, 0),
             retransmits_timed_out(15, 0, 0, 924600, 3)))
    print("  TCP_USER_TIMEOUT 不改成重传节奏(只钳制定时器装填值),")
    print("  但会在 write_timeout / probe0 到点时直接 tcp_write_err -> ETIMEDOUT。")


if __name__ == "__main__":
    main()
