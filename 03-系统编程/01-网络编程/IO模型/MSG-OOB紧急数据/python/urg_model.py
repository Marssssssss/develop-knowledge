"""TCP 紧急数据(MSG_OOB / SIGURG / SIOCATMARK)的行为模型。

口径全部来自实际读到的源码(见 README「参考资料」):
  net/ipv4/tcp_input.c  tcp_check_urg() / tcp_urg()
  net/ipv4/tcp.c        tcp_recv_urg() / tcp_ioctl(SIOCATMARK) / tcp_poll
  include/net/tcp.h     TCP_URG_VALID / TCP_URG_NOTYET / TCP_URG_READ / tcp_inq
  tcp(7), recv(2), sockatmark(3)

序列号一律按 32 位无符号回绕处理;before()/after() 是内核的
`(s32)(a - b)` 比较,本模块用「取模后看最高位」实现。
"""

MASK = 0xFFFFFFFF
SIGN_BIT = 0x80000000

# include/net/tcp.h —— urg_data 三态,高 8 位是状态、低 8 位是那个字节本身
TCP_URG_VALID = 0x0100
TCP_URG_NOTYET = 0x0200
TCP_URG_READ = 0x0400

EINVAL = 22
ENOTCONN = 107
MSG_OOB = 0x01
MSG_PEEK = 0x02
MSG_TRUNC = 0x20

EPOLLPRI = 0x002


def before(a, b):
    return ((a - b) & MASK) >= SIGN_BIT


def after(a, b):
    return before(b, a)


class TcpUrgSock:
    """一个 TCP 接收端的紧急数据状态。"""

    def __init__(self, copied_seq=0, rcv_nxt=0, oobinline=False, stdurg=False,
                 sock_done=False, state="ESTABLISHED"):
        self.copied_seq = copied_seq & MASK
        self.rcv_nxt = rcv_nxt & MASK
        self.urg_seq = 0
        self.urg_data = 0
        self.oobinline = oobinline
        self.stdurg = stdurg            # sysctl_tcp_stdurg / RFC 1122 解释
        self.sock_done = sock_done
        self.state = state
        self.sigurg = 0
        self.recv_shutdown = False

    # -------------------------------------------------------------- 收包
    def on_segment(self, seq, urg_ptr, payload, doff=5, syn=0):
        """转写 tcp_check_urg() + tcp_urg()。

        返回 dict: result 表示落进哪条守卫,sigurg 表示是否发了 SIGURG。
        """
        out = {"sigurg": False, "result": "no_urg", "byte": None}
        if urg_ptr:
            ptr = urg_ptr
            # 默认(BSD 解释)把紧急指针减 1,使其指向紧急数据的**最后一个**字节
            if ptr and not self.stdurg:
                ptr -= 1
            ptr = (ptr + seq) & MASK
            if after(self.copied_seq, ptr):
                out["result"] = "ignored_already_read"
                return out
            if before(ptr, self.rcv_nxt):
                out["result"] = "ignored_replay"
                return out
            if self.urg_data and not after(ptr, self.urg_seq):
                out["result"] = "ignored_duplicate"
                return out
            self.sigurg += 1
            out["sigurg"] = True
            # 内核注释里那段 "Double Dutch":上一个紧急字节刚被读过,
            # 此时又来一个新的,要把 copied_seq 往前推一格,否则
            # SIOCATMARK 的语义会被打破(该字节会被当成普通数据再读一遍)。
            if (self.urg_seq == self.copied_seq and self.urg_data
                    and not self.oobinline and self.copied_seq != self.rcv_nxt):
                self.copied_seq = (self.copied_seq + 1) & MASK
                out["result"] = "accepted_advance_copied_seq"
            else:
                out["result"] = "accepted"
            self.urg_data = TCP_URG_NOTYET
            self.urg_seq = ptr

        # tcp_urg() 的快路径:紧急指针落在本段的 payload 内才取出那个字节
        if self.urg_data == TCP_URG_NOTYET:
            hdr = doff * 4
            p = self.urg_seq - seq + hdr - syn          # 相对 TCP 头的偏移
            skb_len = hdr + len(payload)
            if 0 <= p < skb_len:
                off = p - hdr + syn                     # = urg_seq - seq
                b = payload[off]
                self.urg_data = TCP_URG_VALID | b
                out["byte"] = b
        return out

    # --------------------------------------------------------- SIOCATMARK
    def sockatmark(self):
        """tcp_ioctl():answ = urg_data && urg_seq == copied_seq。"""
        return 1 if (self.urg_data and self.urg_seq == self.copied_seq) else 0

    # ---------------------------------------------------------- SIOCINQ
    def inq(self):
        """tcp_inq():有未处理的紧急数据时,可读字节数被**截断到标记处**。"""
        if (self.oobinline or not self.urg_data
                or before(self.urg_seq, self.copied_seq)
                or not before(self.urg_seq, self.rcv_nxt)):
            answ = (self.rcv_nxt - self.copied_seq) & MASK
            if answ and self.sock_done:
                answ -= 1
        else:
            answ = (self.urg_seq - self.copied_seq) & MASK
        return answ

    def recv_limit(self, seq, avail):
        """tcp_recvmsg() 的 `used = urg_offset` 截断。

        注意这条截断**不看** SO_OOBINLINE —— 源码里 `if (urg_offset < used)
        used = urg_offset;` 在判断 URGINLINE 之前。URGINLINE 只影响
        urg_offset == 0 那一格(是当普通数据拷贝,还是跳过去)。
        """
        if self.urg_data:
            off = (self.urg_seq - seq) & MASK
            if off < avail:
                return off
        return avail

    def read_chunk(self, seq, avail):
        """返回 (拷贝到用户态的字节数, urg_hole)。

        urg_hole 是 tcp_recvmsg 里那个「紧急字节被跳过、不计入 copied」的洞。
        """
        if not self.urg_data:
            return avail, 0
        off = (self.urg_seq - seq) & MASK
        if off >= avail:
            return avail, 0
        if off == 0:
            if self.oobinline:
                return avail, 0        # 紧急字节作为普通数据交给用户
            return avail - 1, 1        # seq++/offset++/used--/urg_hole++
        return off, 0

    def recv_oob(self, length=1, peek=False):
        """tcp_recv_urg():返回 (返回值, msg_flags);失败返回 (-errno, 0)。"""
        if self.oobinline or not self.urg_data or self.urg_data == TCP_URG_READ:
            return -EINVAL, 0                      # 原文注释:"Yes this is right !"
        if self.state == "CLOSE" and not self.sock_done:
            return -ENOTCONN, 0
        if self.urg_data & TCP_URG_VALID:
            c = self.urg_data & 0xFF               # char c = tp->urg_data
            if not peek:
                self.urg_data = TCP_URG_READ
            flags = MSG_OOB
            if length > 0:
                return 1, flags
            return 0, flags | MSG_TRUNC
        if self.state == "CLOSE" or self.recv_shutdown:
            return 0, 0
        return 0, 0

    def epoll_mask(self, target=1):
        """tcp_poll():EPOLLPRI 只在 TCP_URG_VALID 时给;在标记处 target 要 +1。"""
        mask = 0
        t = target
        if (self.urg_data and self.urg_seq == self.copied_seq
                and not self.oobinline):
            t += 1
        if self.urg_data & TCP_URG_VALID:
            mask |= EPOLLPRI
        return mask, t
