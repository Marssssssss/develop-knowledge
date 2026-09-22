"""io_uring 的 SQ/CQ 环与注册资源（从 ioring.py 拆出）。

SQ 是间接的（array 里存下标）、CQ 是直接的（直接就是 CQE）——
这个不对称是本模块最想钉住的性质。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ioring import *  # noqa: E402,F401,F403

class Ring:
    """SQ/CQ 环的最小模型：head/tail + mask，以及 SQPOLL / CQ 溢出。

    SQ 是**间接**的：sq->array[tail & mask] = sqe 下标（除非 NO_SQARRAY）。
    CQ 是**直接**的：cqes[head & mask] 直接就是 CQE。
    """

    def __init__(self, entries, cq_entries=None, flags=0, sqpoll=False,
                 cq_overflow=False):
        self.sq_entries = min(clamp_entries(entries), IORING_MAX_ENTRIES)
        if cq_entries is None:
            cq_entries = 2 * self.sq_entries
        self.cq_entries = min(cq_entries, IORING_MAX_CQ_ENTRIES)
        self.flags = flags
        self.sqpoll = sqpoll
        self.allow_overflow = cq_overflow
        self.sq_head = 0
        self.sq_tail = 0
        self.cq_head = 0
        self.cq_tail = 0
        self.sq_array = [0] * self.sq_entries
        self.sqes = [SQE() for _ in range(self.sq_entries)]
        self.cqes = [None] * self.cq_entries
        self.overflow = []
        self.dropped = 0

    @property
    def sq_mask(self):
        return self.sq_entries - 1

    @property
    def cq_mask(self):
        return self.cq_entries - 1

    def sq_pending(self):
        return self.sq_tail - self.sq_head

    def cq_pending(self):
        return self.cq_tail - self.cq_head

    def sq_full(self):
        return self.sq_pending() >= self.sq_entries

    def cq_full(self):
        return self.cq_pending() >= self.cq_entries

    def get_sqe(self):
        """拿到一个 SQE 槽位；SQ 满返回 None。"""
        if self.sq_full():
            return None
        idx = self.sq_tail & self.sq_mask
        self.sq_array[self.sq_tail & self.sq_mask] = idx
        self.sq_tail += 1
        return self.sqes[idx]

    def fill_cq(self, cqe):
        """内核投递一个 CQE。CQ 满时的行为取决于是否允许 overflow。"""
        if self.cq_full():
            if self.allow_overflow:
                self.overflow.append(cqe)
                return True
            self.dropped += 1
            return False
        self.cqes[self.cq_tail & self.cq_mask] = cqe
        self.cq_tail += 1
        return True

    def reap(self, n=None):
        """应用侧收割，返回 CQE 列表；随后尝试回填 overflow。"""
        out = []
        limit = n if n is not None else self.cq_pending()
        while self.cq_head < self.cq_tail and len(out) < limit:
            cqe = self.cqes[self.cq_head & self.cq_mask]
            self.cqes[self.cq_head & self.cq_mask] = None
            self.cq_head += 1
            out.append(cqe)
        # 腾出空间后把 overflow 里的补进来
        while self.overflow and not self.cq_full():
            cqe = self.overflow.pop(0)
            self.cqes[self.cq_tail & self.cq_mask] = cqe
            self.cq_tail += 1
        return out

    def need_syscall(self, min_complete=0):
        """是否需要 io_uring_enter。

        SQPOLL 模式下提交通常不需要系统调用；只有等待完成（GETEVENTS /
        min_complete > 0）才必须进内核。
        """
        if min_complete > 0:
            return True
        return not self.sqpoll


def clamp_entries(entries, flags=0):
    """io_uring_setup 对 entries 的钳制（io_uring/io_uring.c）。

    * 非 CLAMP 时超过 IORING_MAX_ENTRIES 直接 EINVAL
    * CLAMP 时钳到 IORING_MAX_ENTRIES
    * CQSIZE 时 cq_entries 超过 IORING_MAX_CQ_ENTRIES 钳到上限
    """
    if entries <= 0:
        raise ValueError("entries must be > 0")
    if entries > IORING_MAX_ENTRIES:
        if flags & IORING_SETUP_CLAMP:
            return IORING_MAX_ENTRIES
        raise ValueError("EINVAL: entries > IORING_MAX_ENTRIES without CLAMP")
    return entries


def setup(entries, cq_entries=None, flags=0):
    """模拟 io_uring_setup 的返回参数。"""
    sq = clamp_entries(entries, flags)
    if cq_entries is None:
        cq = 2 * sq
    else:
        # 文档：CQSIZE 时 cq_entries 必须大于 entries，且可能被向上取整
        if not (flags & IORING_SETUP_CQSIZE):
            raise ValueError("EINVAL: cq_entries 需要 IORING_SETUP_CQSIZE")
        if cq_entries <= entries:
            raise ValueError("EINVAL: cq_entries 必须大于 entries")
        cq = min(cq_entries, IORING_MAX_CQ_ENTRIES)
    return {
        "sq_entries": sq,
        "cq_entries": min(cq, IORING_MAX_CQ_ENTRIES),
        "sqpoll": bool(flags & IORING_SETUP_SQPOLL),
        "sqe_size": SQE_SIZE_128 if flags & IORING_SETUP_SQE128 else SQE_SIZE,
        "cqe_size": CQE_SIZE_32 if flags & IORING_SETUP_CQE32 else CQE_SIZE,
    }


def mmap_length(kind, sq_entries, cq_entries, cqe_size=CQE_SIZE,
                sqe_size=SQE_SIZE):
    """三块映射的长度。

    SQ ring:  sq_off.array + sq_entries * sizeof(u32)
    SQEs:     sq_entries * sizeof(struct io_uring_sqe)
    CQ ring:  cq_off.cqes + cq_entries * sizeof(struct io_uring_cqe)
    """
    if kind == "sq_ring":
        return 64 + sq_entries * 4
    if kind == "sqes":
        return sq_entries * sqe_size
    if kind == "cq_ring":
        return 64 + cq_entries * cqe_size
    raise ValueError(kind)


class RegisteredBuffers:
    """IORING_REGISTER_BUFFERS / BUFFERS2 / BUFFERS_UPDATE 的模型。

    注册是一次性的：内核在注册时 pin 住页，之后 SQE 里只带索引。
    UPDATE 只能替换**已有槽位**（越界返回 EINVAL），不能扩容。
    """

    def __init__(self, buffers=None, sparse=False, tags=None):
        self.buffers = list(buffers or [])
        self.sparse = sparse
        self.tags = list(tags or [])

    def register(self, buffers, sparse=False):
        if self.buffers:
            raise ValueError("EBUSY: 已注册，需先 UNREGISTER")
        self.buffers = list(buffers)
        self.sparse = sparse
        self.tags = [0] * len(self.buffers)
        return len(self.buffers)

    def update(self, index, buf, tag=None):
        if index < 0 or index >= len(self.buffers):
            raise ValueError("EINVAL: 索引越界，UPDATE 不能扩容")
        self.buffers[index] = buf
        if tag is not None:
            self.tags[index] = tag

    def get(self, index):
        if index < 0 or index >= len(self.buffers):
            raise ValueError("EINVAL: 缓冲区索引越界")
        return self.buffers[index]


def prep_read(rb, fd, index, length, off, user_data, fixed_file=False):
    """构造一个 READ_FIXED 的 SQE。

    关键差异：addr 字段放的是**注册缓冲区索引**，不是地址。
    """
    flags = IOSQE_FIXED_FILE if fixed_file else 0
    return SQE(opcode=IORING_OP_READ_FIXED, fd=fd, addr=index, length=length,
               off=off, flags=flags, user_data=user_data, buf_index=index)


def prep_read_plain(fd, addr, length, off, user_data):
    """普通 IORING_OP_READ：addr 是真正的用户态地址。"""
    return SQE(opcode=IORING_OP_READ, fd=fd, addr=addr, length=length,
               off=off, user_data=user_data)
