"""MSG_ZEROCOPY 与 vmsplice 的行为模型。

全部口径来自实际读到的原文(见 README「参考资料」):
  * Documentation/networking/msg_zerocopy.rst
  * include/uapi/linux/errqueue.h
  * vmsplice(2) / splice(2) man page
文档没有定量说明的地方(例如合并时 ee_code 如何取值)一律不作数值断言,
只在 README 的「口径说明」里写明本 demo 的取法。
"""

IOV_MAX = 1024          # <limits.h>,vmsplice(2) 明确写「Currently, this limit is 1024」
PAGE_SIZE = 4096

# include/uapi/linux/errqueue.h
SO_EE_ORIGIN_NONE = 0
SO_EE_ORIGIN_LOCAL = 1
SO_EE_ORIGIN_ICMP = 2
SO_EE_ORIGIN_ICMP6 = 3
SO_EE_ORIGIN_TXSTATUS = 4
SO_EE_ORIGIN_ZEROCOPY = 5
SO_EE_ORIGIN_TXTIME = 6

SO_EE_CODE_ZEROCOPY_COPIED = 1

UINT32_MAX = 0xFFFFFFFF
UINT32_MOD = 0x100000000

ENOBUFS = 105
EBADF = 9
EINVAL = 22
EAGAIN = 11

SPLICE_F_MOVE = 1
SPLICE_F_NONBLOCK = 2
SPLICE_F_MORE = 4
SPLICE_F_GIFT = 8


class Range:
    """一条 outstanding 通知:[lo, hi] 闭区间。"""

    __slots__ = ("lo", "hi", "copied")

    def __init__(self, lo, hi, copied=False):
        self.lo = lo
        self.hi = hi
        self.copied = copied

    def as_extended_err(self):
        return {
            "ee_errno": 0,                       # 恒 0,为了不阻塞 send/recv
            "ee_origin": SO_EE_ORIGIN_ZEROCOPY,
            "ee_code": SO_EE_CODE_ZEROCOPY_COPIED if self.copied else 0,
            "ee_info": self.lo,
            "ee_data": self.hi,
        }


class ZerocopySocket:
    """一个开了 SO_ZEROCOPY 的 TCP socket 的通知记账。"""

    def __init__(self, zc_enabled=False, loopback=False):
        self.zc_enabled = zc_enabled
        self.loopback = loopback
        self.counter = 0
        self.queue = []

    def setsockopt_zerocopy(self, one):
        self.zc_enabled = bool(one)

    def send(self, length, zerocopy=True, enobufs=False):
        """返回 (返回值, 本次分配的 counter 或 None)。

        * 没开 SO_ZEROCOPY -> 内核**静默忽略** flag,走普通拷贝路径
        * length == 0     -> counter 不增
        * 失败(ENOBUFS)   -> counter 不增
        """
        if not (self.zc_enabled and zerocopy):
            return length, None
        if length == 0:
            return 0, None
        if enobufs:
            return -ENOBUFS, None
        self.counter = (self.counter + 1) % UINT32_MOD
        return length, self.counter

    def complete(self, value, copied=None, allow_extend=True):
        """内核在释放共享页时入队一条通知。

        返回 True 表示**新起了**一个通知包,False 表示被合并进了队尾。
        合并判据:新值是否恰好是队尾区间上界的下一个值(按 u32 回绕)。
        """
        if copied is None:
            copied = self.loopback       # loopback 一定走 deferred copy
        if self.queue and allow_extend:
            tail = self.queue[-1]
            if (tail.hi + 1) % UINT32_MOD == value:
                tail.hi = value
                tail.copied = tail.copied or copied
                return False
        self.queue.append(Range(value, value, copied))
        return True

    def recv_errqueue(self):
        """recvmsg(fd, &msg, MSG_ERRQUEUE);永远非阻塞。"""
        if not self.queue:
            return None
        return self.queue.pop(0).as_extended_err()

    def outstanding(self):
        return len(self.queue)


def vmsplice(fd_is_pipe, iovs, flags, would_block=False):
    """vmsplice() 的返回值与 errno 模型。

    iovs: [(base, length), ...],base 为虚拟地址(本 demo 只关心页对齐)。
    返回 (传输字节数, errno or 0)。
    """
    if not fd_is_pipe:
        return -1, EBADF
    if len(iovs) > IOV_MAX:
        return -1, EINVAL
    if would_block and (flags & SPLICE_F_NONBLOCK):
        return -1, EAGAIN
    if flags & SPLICE_F_GIFT:
        # 「Data must also be properly page aligned, both in memory and length.」
        for base, length in iovs:
            if base % PAGE_SIZE or length % PAGE_SIZE:
                return -1, EINVAL
    return sum(length for _, length in iovs), 0


def vmsplice_direction(fd_opened_for_write):
    """vmsplice 的语义方向:写端是真 splice,读端实际只是拷贝。"""
    return "splice" if fd_opened_for_write else "copy"


def zerocopy_is_worth_it(length_bytes):
    """文档原话:「MSG_ZEROCOPY is generally only effective at writes over
    around 10 KB」——只给量级,不给精确阈值,故这里只作 10 KiB 的数量级判定。"""
    return length_bytes > 10 * 1024
