# -*- coding: utf-8 -*-
"""io_uring 网络 IO —— 模型层

原文依据（均已实读 man7 页面）：

io_uring(7)
  * "io_uring gets its name from ring buffers which are shared between
     user space and kernel space."
  * "The kernel places exactly one matching CQE in the CQ for every SQE you
     submit on the SQ."
  * "res will contain what the equivalent system call would have returned in
     case of success, and in case of error res will contain -errno ... Given
     that io_uring is an async interface, errno is never used for passing
     back error information."
  * "I/O requests submitted to the kernel can complete in any order ... you
     should always check which submitted request it corresponds to. The most
     common method for doing so is utilizing the user_data field"
  * "for sends and receives on a stream-oriented TCP socket, it is generally
     unsafe to have more than one outstanding send, or more than one
     outstanding receive (the two directions are independent) on a given
     socket at a time, as the kernel may reorder their execution"
  * "If the requests are submitted in a single batch, the application may
     use IOSQE_IO_LINK to enforce an execution order in the kernel."
  * "You add SQEs to the tail of the SQ. The kernel reads SQEs off the head"
  * "The kernel adds CQEs to the tail of the CQ. You read CQEs off the head"

io_uring_setup(2)
  * IORING_SETUP_SQPOLL: "a kernel thread is created to perform submission
    queue polling ... the application can submit and reap I/Os without doing
    a single system call."
  * "If the kernel thread is idle for more than sq_thread_idle milliseconds,
    it will set the IORING_SQ_NEED_WAKEUP bit in the flags field"
  * IORING_SETUP_IOPOLL: "usable only on a file descriptor opened using the
    O_DIRECT flag (if using the IORING_OP_{READ,WRITE}(V)(_FIXED) opcodes)"
  * IORING_SETUP_CQSIZE: "The value must be greater than entries, and may be
    rounded up to the next power-of-two."

io_uring_enter(2)
  * IORING_ENTER_GETEVENTS: "the system call will wait for the specified
    number of events in min_complete before returning"
"""

from collections import deque

EAGAIN = 11
ECONNRESET = 104
EINVAL = 22
ECANCELED = 125

OP_ACCEPT, OP_RECV, OP_SEND, OP_POLL_ADD, OP_READ = 0, 1, 2, 3, 4
OP_NAMES = {OP_ACCEPT: "ACCEPT", OP_RECV: "RECV", OP_SEND: "SEND",
            OP_POLL_ADD: "POLL_ADD", OP_READ: "READ"}

IOSQE_IO_LINK = 1 << 0
IORING_SETUP_SQPOLL = 1 << 1
IORING_SETUP_IOPOLL = 1 << 0
IORING_SQ_NEED_WAKEUP = 1 << 0
IORING_ENTER_GETEVENTS = 1


class SQE:
    __slots__ = ("opcode", "fd", "user_data", "flags", "buf_len", "payload")

    def __init__(self, opcode, fd, user_data, flags=0, buf_len=0, payload=b""):
        self.opcode = opcode
        self.fd = fd
        self.user_data = user_data
        self.flags = flags
        self.buf_len = buf_len
        self.payload = payload


class CQE:
    __slots__ = ("user_data", "res", "opcode")

    def __init__(self, user_data, res, opcode):
        self.user_data = user_data
        self.res = res
        self.opcode = opcode

    @property
    def ok(self):
        return self.res >= 0

    @property
    def errno(self):
        return -self.res if self.res < 0 else 0


class Sock:
    """被建模的 socket：接收缓冲、已完成队列、是否已被 reset。"""

    def __init__(self, fd):
        self.fd = fd
        self.rcv = bytearray()
        self.backlog = deque()
        self.reset = False


class Ring:
    """io_uring 实例：SQ / CQ 两个环形队列 + 可选的内核轮询线程。"""

    def __init__(self, entries=8, flags=0, sq_thread_idle=0, cq_entries=None):
        self.sq_entries = entries
        self.cq_entries = cq_entries if cq_entries else entries * 2
        self.setup_flags = flags
        self.sq_thread_idle = sq_thread_idle
        self.sq = deque()                 # 待内核消费（head 在左）
        self.cq = deque()                 # 待应用消费（head 在左）
        self.pending = []                 # 已提交但尚未具备完成条件
        self.sq_flags = 0                 # struct io_sq_ring.flags
        self.thread_idle_since = 0.0
        self.thread_asleep = False
        self.now = 0.0
        self.syscalls = 0
        self.enters = 0
        self.exec_log = []                # 内核实际执行顺序
        self.complete_order = "fifo"      # 可注入成 "reverse" 演示乱序
        self.socks = {}

    # ---------------------------------------------------------- 用户态
    @property
    def sqpoll(self):
        return bool(self.setup_flags & IORING_SETUP_SQPOLL)

    @property
    def iopoll(self):
        return bool(self.setup_flags & IORING_SETUP_IOPOLL)

    def get_sqe(self, opcode, fd, user_data, flags=0, buf_len=0, payload=b""):
        if self.iopoll and opcode in (OP_RECV, OP_SEND, OP_ACCEPT, OP_POLL_ADD):
            raise ValueError("IORING_SETUP_IOPOLL 只用于 O_DIRECT 的 READ/WRITE")
        return SQE(opcode, fd, user_data, flags, buf_len, payload)

    def prep(self, sqe):
        """把 SQE 放到 SQ 尾部（纯用户态内存写，不算系统调用）。"""
        self.sq.append(sqe)
        return sqe

    def submit(self, min_complete=0, flags=0):
        """io_uring_enter(2)：一次系统调用同时提交并（可选）等待完成。"""
        self.syscalls += 1
        self.enters += 1
        # 提交动作本身让轮询线程重新忙碌，空闲计时归零
        self.thread_idle_since = 0.0
        self.thread_asleep = False
        self.sq_flags &= ~IORING_SQ_NEED_WAKEUP
        return self._run_kernel(min_complete, flags)

    def sqpoll_tick(self):
        """内核轮询线程自己消费 SQ —— 应用不进内核，系统调用计数不变。"""
        if not (self.sqpoll and not self.thread_asleep):
            return 0
        return self._run_kernel(0, 0)

    def _run_kernel(self, min_complete, flags):
        batch = []
        while self.sq:
            batch.append(self.sq.popleft())
        chains = self._split_chains(batch)
        groups = []
        for chain in chains:
            results = []
            for sqe in chain:                # 链内严格按提交顺序执行
                self.exec_log.append((sqe.user_data, OP_NAMES[sqe.opcode]))
                cqe = self._do_io(sqe)
                if cqe is not None:
                    results.append(cqe)
            groups.append(results)
        if self.complete_order == "reverse":
            groups = groups[::-1]            # 未链接的组之间允许乱序
        for g in groups:
            for cqe in g:
                self.cq.append(cqe)          # 内核把 CQE 加到 CQ 尾部
        if flags & IORING_ENTER_GETEVENTS and min_complete > len(self.cq):
            self._flush_pending()            # 内核阻塞直到凑够 min_complete
        return len(batch)

    @staticmethod
    def _split_chains(batch):
        """IOSQE_IO_LINK 把 SQE 与**下一个**绑成一条链；未链接的自成一组。"""
        chains, cur = [], []
        for i, sqe in enumerate(batch):
            cur.append(sqe)
            if (sqe.flags & IOSQE_IO_LINK) and i != len(batch) - 1:
                continue
            chains.append(cur)
            cur = []
        if cur:
            chains.append(cur)
        return chains

    def _do_io(self, sqe):
        s = self.socks.setdefault(sqe.fd, Sock(sqe.fd))
        if s.reset:
            return CQE(sqe.user_data, -ECONNRESET, sqe.opcode)
        if sqe.opcode == OP_RECV:
            if not s.rcv:
                self.pending.append(sqe)     # 数据未到，暂不完成
                return None
            n = min(sqe.buf_len, len(s.rcv))
            del s.rcv[:n]
            return CQE(sqe.user_data, n, sqe.opcode)
        if sqe.opcode == OP_SEND:
            return CQE(sqe.user_data, len(sqe.payload), sqe.opcode)
        if sqe.opcode == OP_ACCEPT:
            if not s.backlog:
                self.pending.append(sqe)
                return None
            return CQE(sqe.user_data, s.backlog.popleft(), sqe.opcode)
        return CQE(sqe.user_data, -EINVAL, sqe.opcode)

    def _flush_pending(self):
        """数据/连接到达后，把挂起的操作补完成。"""
        still = []
        for sqe in self.pending:
            cqe = self._do_io(sqe)
            if cqe is None:
                still.append(sqe)
            else:
                self.cq.append(cqe)
        self.pending = still

    def feed(self, fd, data=None, conn=None):
        """模拟网络侧到达：写数据 / 放一个已完成连接。"""
        s = self.socks.setdefault(fd, Sock(fd))
        if data:
            s.rcv += data
        if conn is not None:
            s.backlog.append(conn)
        self._flush_pending()

    def wait_cqe(self, min_complete=1):
        """取一个 CQE（从 CQ 头部）。没有就再进一次内核。"""
        if not self.cq:
            self.submit(min_complete=min_complete, flags=IORING_ENTER_GETEVENTS)
        return self.cq.popleft() if self.cq else None

    def reap_all(self):
        out = []
        while self.cq:
            out.append(self.cq.popleft())
        return out

    # ---------------------------------------------------- SQPOLL 空闲管理
    def tick(self, dt):
        """推进时间，模拟内核轮询线程空闲超时。"""
        self.now += dt
        if not self.sqpoll:
            return
        if not self.sq and not self.pending:
            self.thread_idle_since += dt
            if self.thread_idle_since > self.sq_thread_idle:
                self.thread_asleep = True
                self.sq_flags |= IORING_SQ_NEED_WAKEUP
        else:
            self.thread_idle_since = 0.0
            self.thread_asleep = False
            self.sq_flags &= ~IORING_SQ_NEED_WAKEUP

    def need_wakeup(self):
        return bool(self.sq_flags & IORING_SQ_NEED_WAKEUP)


# ------------------------------------------------------- 与 epoll 的对比
def epoll_syscalls(n_conn, n_events):
    """epoll 路径：每新连接一次 epoll_ctl，每轮一次 epoll_wait，每事件一次 recv。"""
    return n_conn + n_events + n_events


def iouring_syscalls(n_batches):
    """io_uring 路径：每批一次 io_uring_enter。"""
    return n_batches
