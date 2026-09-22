"""io_uring 文件 IO：ring 布局、SQE/CQE 语义、注册缓冲区。

来源（全部实际读过，见 README）：
* ``include/uapi/linux/io_uring.h``       —— 结构体、opcode、flags、mmap 偏移
* ``io_uring/io_uring.h``                 —— IORING_MAX_ENTRIES / IORING_MAX_CQ_ENTRIES
* ``io_uring/io_uring.c``                 —— entries 的钳制逻辑
* ``io_uring_setup(2)`` / ``io_uring_register(2)`` / ``io_uring_enter(2)``
"""

import struct

# ---- include/uapi/linux/io_uring.h：mmap 偏移 ------------------------------
IORING_OFF_SQ_RING = 0x0
IORING_OFF_CQ_RING = 0x8000000
IORING_OFF_SQES = 0x10000000
IORING_OFF_PBUF_RING = 0x80000000
IORING_OFF_PBUF_SHIFT = 16
IORING_OFF_MMAP_MASK = 0xF8000000

# ---- io_uring/io_uring.h ---------------------------------------------------
IORING_MAX_ENTRIES = 32768
IORING_MAX_CQ_ENTRIES = 2 * IORING_MAX_ENTRIES     # 65536

# ---- setup flags -----------------------------------------------------------
IORING_SETUP_IOPOLL = 1 << 0
IORING_SETUP_SQPOLL = 1 << 1
IORING_SETUP_SQ_AFF = 1 << 2
IORING_SETUP_CQSIZE = 1 << 3
IORING_SETUP_CLAMP = 1 << 4
IORING_SETUP_ATTACH_WQ = 1 << 5
IORING_SETUP_R_DISABLED = 1 << 6
IORING_SETUP_SUBMIT_ALL = 1 << 7
IORING_SETUP_COOP_TASKRUN = 1 << 8
IORING_SETUP_TASKRUN_FLAG = 1 << 9
IORING_SETUP_SQE128 = 1 << 10
IORING_SETUP_CQE32 = 1 << 11
IORING_SETUP_SINGLE_ISSUER = 1 << 12
IORING_SETUP_DEFER_TASKRUN = 1 << 13
IORING_SETUP_NO_MMAP = 1 << 14
IORING_SETUP_REGISTERED_FD_ONLY = 1 << 15
IORING_SETUP_NO_SQARRAY = 1 << 16
IORING_SETUP_HYBRID_IOPOLL = 1 << 17
IORING_SETUP_CQE_MIXED = 1 << 18
IORING_SETUP_SQE_MIXED = 1 << 19
IORING_SETUP_SQ_REWIND = 1 << 20

# ---- sqe->flags ------------------------------------------------------------
IOSQE_FIXED_FILE_BIT = 0
IOSQE_IO_DRAIN_BIT = 1
IOSQE_IO_LINK_BIT = 2
IOSQE_IO_HARDLINK_BIT = 3
IOSQE_ASYNC_BIT = 4
IOSQE_BUFFER_SELECT_BIT = 5
IOSQE_CQE_SKIP_SUCCESS_BIT = 6

IOSQE_FIXED_FILE = 1 << IOSQE_FIXED_FILE_BIT
IOSQE_IO_DRAIN = 1 << IOSQE_IO_DRAIN_BIT
IOSQE_IO_LINK = 1 << IOSQE_IO_LINK_BIT
IOSQE_IO_HARDLINK = 1 << IOSQE_IO_HARDLINK_BIT
IOSQE_ASYNC = 1 << IOSQE_ASYNC_BIT
IOSQE_BUFFER_SELECT = 1 << IOSQE_BUFFER_SELECT_BIT
IOSQE_CQE_SKIP_SUCCESS = 1 << IOSQE_CQE_SKIP_SUCCESS_BIT

# ---- io_uring_enter flags --------------------------------------------------
IORING_ENTER_GETEVENTS = 1 << 0
IORING_ENTER_SQ_WAKEUP = 1 << 1
IORING_ENTER_SQ_WAIT = 1 << 2
IORING_ENTER_EXT_ARG = 1 << 3
IORING_ENTER_REGISTERED_RING = 1 << 4

# ---- opcode ----------------------------------------------------------------
IORING_OP_NOP = 0
IORING_OP_READV = 1
IORING_OP_WRITEV = 2
IORING_OP_FSYNC = 3
IORING_OP_READ_FIXED = 4
IORING_OP_WRITE_FIXED = 5
IORING_OP_SYNC_FILE_RANGE = 8
IORING_OP_FALLOCATE = 17
IORING_OP_OPENAT = 18
IORING_OP_CLOSE = 19
IORING_OP_STATX = 21
IORING_OP_READ = 22
IORING_OP_WRITE = 23
IORING_OP_FADVISE = 24
IORING_OP_OPENAT2 = 28
IORING_OP_SPLICE = 29
IORING_OP_FTRUNCATE = 56
IORING_OP_READV_FIXED = 62
IORING_OP_WRITEV_FIXED = 63

OP_NAME = {
    IORING_OP_NOP: "NOP", IORING_OP_READV: "READV", IORING_OP_WRITEV: "WRITEV",
    IORING_OP_FSYNC: "FSYNC", IORING_OP_READ_FIXED: "READ_FIXED",
    IORING_OP_WRITE_FIXED: "WRITE_FIXED", IORING_OP_SYNC_FILE_RANGE: "SYNC_FILE_RANGE",
    IORING_OP_FALLOCATE: "FALLOCATE", IORING_OP_OPENAT: "OPENAT",
    IORING_OP_CLOSE: "CLOSE", IORING_OP_STATX: "STATX",
    IORING_OP_READ: "READ", IORING_OP_WRITE: "WRITE",
    IORING_OP_FADVISE: "FADVISE", IORING_OP_OPENAT2: "OPENAT2",
    IORING_OP_SPLICE: "SPLICE", IORING_OP_FTRUNCATE: "FTRUNCATE",
    IORING_OP_READV_FIXED: "READV_FIXED", IORING_OP_WRITEV_FIXED: "WRITEV_FIXED",
}

# ---- register opcode -------------------------------------------------------
IORING_REGISTER_BUFFERS = 0
IORING_UNREGISTER_BUFFERS = 1
IORING_REGISTER_FILES = 2
IORING_UNREGISTER_FILES = 3
IORING_REGISTER_EVENTFD = 4
IORING_REGISTER_BUFFERS2 = 15
IORING_REGISTER_BUFFERS_UPDATE = 16
IORING_REGISTER_RING_FDS = 20
IORING_RSRC_REGISTER_SPARSE = 1 << 0

# ---- 结构尺寸 --------------------------------------------------------------
SQE_SIZE = 64           # struct io_uring_sqe（无 SQE128）
CQE_SIZE = 16           # struct io_uring_cqe（无 CQE32）
SQE_SIZE_128 = 128
CQE_SIZE_32 = 32

# struct io_uring_cqe { u64 user_data; s32 res; u32 flags; }
CQE_FMT = "<Q i I"
# struct io_uring_sqe 的前 48 字节（到 user_data 之后）
SQE_HEAD_FMT = "<B B H i Q Q I I Q H H i"


class SQE:
    """struct io_uring_sqe 的字段子集（够表达文件 IO）。"""

    __slots__ = ("opcode", "flags", "ioprio", "fd", "off", "addr", "len",
                 "rw_flags", "user_data", "buf_index", "personality", "file_index")

    def __init__(self, opcode=IORING_OP_NOP, fd=-1, addr=0, length=0, off=0,
                 flags=0, user_data=0, buf_index=0, rw_flags=0, file_index=0):
        self.opcode = opcode
        self.flags = flags
        self.ioprio = 0
        self.fd = fd
        self.off = off
        self.addr = addr
        self.len = length
        self.rw_flags = rw_flags
        self.user_data = user_data
        self.buf_index = buf_index
        self.personality = 0
        self.file_index = file_index

    def uses_fixed_file(self):
        return bool(self.flags & IOSQE_FIXED_FILE)

    def uses_fixed_buffer(self):
        """READ_FIXED / WRITE_FIXED 的 addr 是**索引**而非指针。"""
        return self.opcode in (IORING_OP_READ_FIXED, IORING_OP_WRITE_FIXED)

    def needs_buffer(self):
        """哪些 opcode 必须配合已注册缓冲区（否则 addr 会被当成指针）。"""
        return self.uses_fixed_buffer()

    def pack_head(self):
        return struct.pack(
            SQE_HEAD_FMT, self.opcode, self.flags, self.ioprio, self.fd,
            self.off & 0xFFFFFFFFFFFFFFFF, self.addr & 0xFFFFFFFFFFFFFFFF,
            self.len, self.rw_flags, self.user_data, self.buf_index,
            self.personality, self.file_index)


class CQE:
    """struct io_uring_cqe。res 为负时是 -errno。"""

    __slots__ = ("user_data", "res", "flags")

    def __init__(self, user_data, res, flags=0):
        self.user_data = user_data
        self.res = res
        self.flags = flags

    @property
    def errno(self):
        return -self.res if self.res < 0 else 0

    def ok(self):
        return self.res >= 0

    def pack(self):
        return struct.pack(CQE_FMT, self.user_data, self.res, self.flags)

    @staticmethod
    def unpack(b):
        ud, res, flags = struct.unpack(CQE_FMT, b)
        return CQE(ud, res, flags)
