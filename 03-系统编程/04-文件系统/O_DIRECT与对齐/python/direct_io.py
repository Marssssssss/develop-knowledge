"""O_DIRECT 的对齐规则、历史演进与几处硬约束的可执行模型。

事实来源（全部实读）：

  * open(2) NOTES 的 O_DIRECT 小节 —— 对齐限制随文件系统与内核版本变化、
                                       未对齐可能 EINVAL 也可能退回 buffered、
                                       6.1 起可用 statx(STATX_DIOALIGN) 查询、
                                       2.4 与 2.6 的对齐口径差异、
                                       禁止与 fork() 并发（私有映射缓冲）、
                                       不要与 buffered I/O 或 mmap 混用、
                                       NFS 只在客户端绕过 page cache
  * open(2) 标志表 —— O_DIRECT 自身"尽力同步"但不给 O_SYNC 的保证；
                      O_DSYNC 等价于每次 write 后 fdatasync
  * statx(2) —— stx_dio_mem_align / stx_dio_offset_align 成对出现
"""

FS_BLOCK = 4096        # 2.4 时代：文件系统块大小（典型 4096）
LOGICAL_BLOCK = 512    # 2.6.0 起：块设备逻辑块大小（blockdev --getss）

# 数值取自 glibc sysdeps/unix/sysv/linux/bits/fcntl-linux.h：
#   O_DIRECT = __O_DIRECT = 040000、O_DSYNC = __O_DSYNC = 010000、
#   O_SYNC = 04010000（**天然包含 O_DSYNC 那一位**，见下面的坑）
O_DIRECT = 0o40000
O_SYNC = 0o4010000
O_DSYNC = 0o10000
# O_SYNC 去掉 O_DSYNC 位之后剩下的那一位，才是"元数据也同步"的真正标志
O_SYNC_METADATA = O_SYNC & ~O_DSYNC

STRICT = "EINVAL"        # 未对齐直接报错
FALLBACK = "buffered"    # 未对齐静默退回 buffered I/O


class Fs(object):
    """一个文件系统的 O_DIRECT 画像。"""

    def __init__(self, name, supports_direct=True, mem_align=0, off_align=0,
                 misaligned=STRICT, kernel=(6, 1), statx_dioalign=True,
                 nfs=False):
        self.name = name
        self.supports_direct = supports_direct
        self.mem_align = mem_align
        self.off_align = off_align
        self.misaligned = misaligned
        self.kernel = kernel
        self.statx_dioalign = statx_dioalign
        self.nfs = nfs


def statx_dioalign(fs):
    """6.1 起可用 STATX_DIOALIGN 查询；返回 (mem, offset)，不支持则为 (0, 0)。"""
    if fs.kernel < (6, 1) or not fs.statx_dioalign:
        return 0, 0
    return fs.mem_align, fs.off_align


def alignment_for(fs):
    """拿不到 statx 时只能按内核代际猜（open(2) 原文的两档）。

    2.4: 偏移量、长度、缓冲区地址都要是**文件系统块大小**（通常 4096）的倍数
    2.6.0 起: 放宽为**块设备逻辑块大小**（通常 512）
    """
    mem, off = statx_dioalign(fs)
    if mem:
        return mem, off
    if fs.kernel >= (2, 6, 0):
        return LOGICAL_BLOCK, LOGICAL_BLOCK
    return FS_BLOCK, FS_BLOCK


def open_direct(fs, flags):
    """打开带 O_DIRECT 的文件：文件系统没实现这个标志时 open() 直接 EINVAL。"""
    if not (flags & O_DIRECT):
        return "buffered"
    if not fs.supports_direct:
        raise OSError("EINVAL: 该文件系统未实现 O_DIRECT")
    return "direct"


def check_io(fs, buf_addr, offset, length):
    """一次 O_DIRECT 读写能否真的走直接 IO。返回 'direct' / 'buffered'。"""
    open_direct(fs, O_DIRECT)
    mem, off = alignment_for(fs)
    if mem == 0:
        raise OSError("EINVAL: 该文件系统不支持 O_DIRECT")
    if buf_addr % mem or offset % off or length % off:
        if fs.misaligned == STRICT:
            raise OSError("EINVAL: 缓冲区地址/偏移量/长度未对齐（要求 %d/%d）"
                          % (mem, off))
        return FALLBACK           # 手册：也可能静默退回 buffered I/O
    return "direct"


def fork_race(buffer_kind):
    """O_DIRECT 期间 fork() 的合法性。

    open(2)：只要缓冲区是**私有映射**（堆、静态区、MAP_PRIVATE 的 mmap），
    O_DIRECT 就绝不能与 fork() 并发，否则数据损坏与未定义行为。
    shmat / MAP_SHARED / MADV_DONTFORK 的缓冲区不受此限。
    """
    if buffer_kind == "private":
        raise RuntimeError("数据损坏风险：私有映射缓冲区上的 O_DIRECT 与 fork() 并发")
    if buffer_kind in ("shm", "map_shared", "dontfork"):
        return "ok"
    raise ValueError("未知缓冲区类型: %r" % buffer_kind)


def sync_guarantee(flags):
    """O_DIRECT 自己**不**等于同步 IO。

    open(2)：O_DIRECT 会"尽力"同步传输数据，但不给 O_SYNC 那种
    "数据与必要元数据都落地"的保证；要真同步必须 O_SYNC 一起给。
    """
    return {
        "data": bool(flags & (O_SYNC | O_DSYNC)),
        # 不能写 flags & O_SYNC —— glibc 里 O_SYNC 含 O_DSYNC 位，
        # 只给了 O_DSYNC 的 fd 也会被判成"元数据同步"
        "metadata": bool(flags & O_SYNC_METADATA),
    }


def io_cost(mode, size, switches=0, overlap=0):
    """等效 IO 量模型：混用时每次切换要"写回脏页 + 作废页缓存再重读"。

    这是 open(2) 那条建议的量化版本：*even when the filesystem correctly
    handles the coherency issues, overall I/O throughput is likely to be
    slower than using either mode alone*。
    """
    cost = size
    if mode == "mixed":
        cost += switches * 2 * overlap  # 1 次写回 + 1 次重读
    return cost
