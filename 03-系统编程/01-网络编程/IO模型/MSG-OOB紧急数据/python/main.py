"""演示入口:TCP 紧急数据的解释分歧、三条守卫与两个容易踩的接口语义。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from urg_model import (                                   # noqa: E402
    TcpUrgSock, TCP_URG_VALID, TCP_URG_NOTYET, TCP_URG_READ,
)


def show(name, s):
    print("  %-28s urg_seq=%-6d urg_data=0x%04x  atmark=%d  inq=%d"
          % (name, s.urg_seq, s.urg_data, s.sockatmark(), s.inq()))


def main():
    print("== ① 紧急指针的两种解释(urg_ptr=1, seq=100, payload=ABC) ==")
    for name, std in (("Linux 默认(BSD 解释)", False), ("tcp_stdurg=1(RFC 1122)", True)):
        s = TcpUrgSock(copied_seq=100, rcv_nxt=100, stdurg=std)
        r = s.on_segment(seq=100, urg_ptr=1, payload=b"ABC")
        s.rcv_nxt = 103
        print("  %-24s -> ptr=%-5d 取到 %r  result=%s"
              % (name, s.urg_seq, chr(r["byte"]), r["result"]))
        print("  %-24s    atmark=%d  SIOCINQ=%d  普通读上限=%d"
              % ("", s.sockatmark(), s.inq(), s.recv_limit(100, 3)))

    print("\n== ② tcp_check_urg 的三条守卫 ==")
    cases = (
        ("指针指向已收过的字节", dict(copied_seq=100, rcv_nxt=105), 100, 1, b"ABC"),
        ("重复/更旧的指针", None, 100, 3, b"ABCDE"),
        ("指针已被读过", dict(copied_seq=200, rcv_nxt=200), 100, 1, b"ABC"),
    )
    for name, kw, seq, up, pay in cases:
        if kw is None:
            s = TcpUrgSock(copied_seq=100, rcv_nxt=100)
            s.urg_data = TCP_URG_NOTYET
            s.urg_seq = 102
        else:
            s = TcpUrgSock(**kw)
        r = s.on_segment(seq=seq, urg_ptr=up, payload=pay)
        print("  %-22s -> %-24s sigurg=%s"
              % (name, r["result"], r["sigurg"]))
    s = TcpUrgSock(copied_seq=100, rcv_nxt=100)
    s.urg_data = TCP_URG_NOTYET
    s.urg_seq = 102
    print("  %-22s -> %-24s sigurg=%s"
          % ("指针更新一位(对照)", s.on_segment(seq=100, urg_ptr=4,
                                          payload=b"ABCDE")["result"], True))

    print("\n== ③ 'Double Dutch' 修正:copied_seq 会被悄悄 +1 ==")
    for name, inl in (("SO_OOBINLINE 关闭", False), ("SO_OOBINLINE 打开", True)):
        s = TcpUrgSock(copied_seq=100, rcv_nxt=103, oobinline=inl)
        s.urg_data = TCP_URG_NOTYET
        s.urg_seq = 100
        r = s.on_segment(seq=103, urg_ptr=1, payload=b"XY")
        print("  %-22s -> %-32s copied_seq=%d"
              % (name, r["result"], s.copied_seq))

    print("\n== ④ SIOCINQ/FIONREAD 在有紧急数据时被截断到标记处 ==")
    for name, inl in (("默认", False), ("SO_OOBINLINE", True)):
        s = TcpUrgSock(copied_seq=100, rcv_nxt=100, oobinline=inl)
        s.on_segment(seq=100, urg_ptr=1, payload=b"ABC")
        s.rcv_nxt = 103
        print("  %-16s 队列里有 3 字节 -> SIOCINQ 报 %d"
              % (name, s.inq()))

    print("\n== ⑤ MSG_OOB 读取的三态机 ==")
    s = TcpUrgSock(copied_seq=100, rcv_nxt=103)
    s.urg_data = TCP_URG_VALID | 0x41
    s.urg_seq = 100
    print("  初始             0x%04x  recv(MSG_OOB)=%r"
          % (s.urg_data, s.recv_oob()))
    print("  读后             0x%04x  recv(MSG_OOB)=%r  <- EINVAL"
          % (s.urg_data, s.recv_oob()))
    t = TcpUrgSock(copied_seq=100, rcv_nxt=103)
    t.urg_data = TCP_URG_NOTYET
    t.urg_seq = 100
    print("  字节还没到(NOTYET) 0x%04x  recv(MSG_OOB)=%r  <- 不是 EINVAL"
          % (t.urg_data, t.recv_oob()))

    print("\n== ⑥ epoll:EPOLLPRI 只在 TCP_URG_VALID 时给 ==")
    for name, ud in (("无紧急数据", 0), ("仅 NOTYET", TCP_URG_NOTYET),
                     ("VALID", TCP_URG_VALID | 0x41)):
        s = TcpUrgSock(copied_seq=100, rcv_nxt=103)
        s.urg_data = ud
        s.urg_seq = 100
        mask, target = s.epoll_mask(target=1)
        print("  %-12s mask=0x%02x  rcvlowat 目标=%d"
              % (name, mask, target))

    print("\n== ⑦ 一句话结论 ==")
    print("  * 「紧急数据」在 Linux/BSD 默认解释下只有 **1 个字节**。")
    print("  * sockatmark() 返回 1/0 且**不摘除**标记;实现就是 SIOCATMARK。")
    print("  * SIGURG 发给 socket owner(SIOCSPGRP / FIOSETOWN / fcntl F_SETOWN)。")
    print("  * select 报异常条件、poll 报 POLLPRI、epoll 报 EPOLLPRI。")


if __name__ == "__main__":
    main()
