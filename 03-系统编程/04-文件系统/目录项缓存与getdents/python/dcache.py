"""dcache 与 getdents64 的可计算模型。

来源（全部实际读过，见 README）：
* ``include/linux/dcache.h``  —— struct dentry / struct qstr / enum dentry_flags
* ``getdents(2)``             —— struct linux_dirent64 布局、DT_* 常量
* ``Documentation/filesystems/vfs.html`` —— dentry 状态与 dcache 语义
"""

import struct

# ---- include/linux/dcache.h：dentry->d_flags 位 ----------------------------
DCACHE_OP_HASH = 1 << 0
DCACHE_OP_COMPARE = 1 << 1
DCACHE_OP_REVALIDATE = 1 << 2
DCACHE_OP_DELETE = 1 << 3
DCACHE_OP_PRUNE = 1 << 4
DCACHE_DISCONNECTED = 1 << 5
DCACHE_REFERENCED = 1 << 6
DCACHE_DONTCACHE = 1 << 7
DCACHE_CANT_MOUNT = 1 << 8
DCACHE_LOOKUP_WAITERS = 1 << 9
DCACHE_SHRINK_LIST = 1 << 10
DCACHE_OP_WEAK_REVALIDATE = 1 << 11
DCACHE_NFSFS_RENAMED = 1 << 12
DCACHE_FSNOTIFY_PARENT_WATCHED = 1 << 13
DCACHE_DENTRY_KILLED = 1 << 14
DCACHE_MOUNTED = 1 << 15
DCACHE_NEED_AUTOMOUNT = 1 << 16
DCACHE_MANAGE_TRANSIT = 1 << 17
DCACHE_LRU_LIST = 1 << 18

# 位 19..21 存"dentry 类型"，是个 3 位字段而不是独立标志位
DCACHE_ENTRY_TYPE = 7 << 19
DCACHE_MISS_TYPE = 0 << 19          # negative dentry
DCACHE_WHITEOUT_TYPE = 1 << 19
DCACHE_DIRECTORY_TYPE = 2 << 19
DCACHE_AUTODIR_TYPE = 3 << 19
DCACHE_REGULAR_TYPE = 4 << 19
DCACHE_SPECIAL_TYPE = 5 << 19
DCACHE_SYMLINK_TYPE = 6 << 19

DCACHE_NOKEY_NAME = 1 << 22         # 旧名 DCACHE_ENCRYPTED_NAME / DCACHE_NOKEY_NAME
DCACHE_OP_REAL = 1 << 23
DCACHE_PAR_LOOKUP = 1 << 24
DCACHE_DENTRY_CURSOR = 1 << 25
DCACHE_NORCU = 1 << 26
DCACHE_PERSISTENT = 1 << 27

DCACHE_MANAGED_DENTRY = DCACHE_MOUNTED | DCACHE_NEED_AUTOMOUNT | \
    DCACHE_MANAGE_TRANSIT

TYPE_NAME = {
    DCACHE_MISS_TYPE: "MISS(negative)",
    DCACHE_WHITEOUT_TYPE: "WHITEOUT",
    DCACHE_DIRECTORY_TYPE: "DIRECTORY",
    DCACHE_AUTODIR_TYPE: "AUTODIR",
    DCACHE_REGULAR_TYPE: "REGULAR",
    DCACHE_SPECIAL_TYPE: "SPECIAL",
    DCACHE_SYMLINK_TYPE: "SYMLINK",
}

# ---- <dirent.h> 的 d_type --------------------------------------------------
DT_UNKNOWN = 0
DT_FIFO = 1
DT_CHR = 2
DT_DIR = 4
DT_BLK = 6
DT_REG = 8
DT_LNK = 10
DT_SOCK = 12
DT_WHT = 14

DT_NAME = {
    DT_UNKNOWN: "DT_UNKNOWN", DT_FIFO: "DT_FIFO", DT_CHR: "DT_CHR",
    DT_DIR: "DT_DIR", DT_BLK: "DT_BLK", DT_REG: "DT_REG",
    DT_LNK: "DT_LNK", DT_SOCK: "DT_SOCK", DT_WHT: "DT_WHT",
}

# dcache 内部类型 → getdents 暴露给用户的 d_type
DCACHE_TO_DT = {
    DCACHE_MISS_TYPE: DT_UNKNOWN,
    DCACHE_WHITEOUT_TYPE: DT_WHT,
    DCACHE_DIRECTORY_TYPE: DT_DIR,
    DCACHE_AUTODIR_TYPE: DT_DIR,
    DCACHE_REGULAR_TYPE: DT_REG,
    DCACHE_SPECIAL_TYPE: DT_UNKNOWN,
    DCACHE_SYMLINK_TYPE: DT_LNK,
}

# ---- struct linux_dirent64 -------------------------------------------------
# ino64_t d_ino(8)  loff_t d_off(8)  unsigned short d_reclen(2)
# unsigned char d_type(1)  char d_name[]
DIRENT64_HEADER = 8 + 8 + 2 + 1      # 19 字节（未对齐到 8）
DIRENT64_FMT = "<QQHB"               # 不含 d_name

# ---- struct linux_dirent（老接口，32 位 ino）--------------------------------
# unsigned long d_ino(8)  unsigned long d_off(8)  unsigned short d_reclen(2)
# char d_name[] ... 末尾一个字节是 d_type
DIRENT_HEADER = 8 + 8 + 2


def dentry_type(flags: int) -> int:
    """d_flags 里 19..21 位的类型字段。"""
    return flags & DCACHE_ENTRY_TYPE


def d_is_miss(flags: int) -> bool:
    return dentry_type(flags) == DCACHE_MISS_TYPE


def d_is_whiteout(flags: int) -> bool:
    return dentry_type(flags) == DCACHE_WHITEOUT_TYPE


def d_is_directory(flags: int) -> bool:
    return dentry_type(flags) == DCACHE_DIRECTORY_TYPE


def d_is_autodir(flags: int) -> bool:
    return dentry_type(flags) == DCACHE_AUTODIR_TYPE


def d_is_symlink(flags: int) -> bool:
    return dentry_type(flags) == DCACHE_SYMLINK_TYPE


def d_is_reg(flags: int) -> bool:
    return dentry_type(flags) == DCACHE_REGULAR_TYPE


def d_is_special(flags: int) -> bool:
    return dentry_type(flags) == DCACHE_SPECIAL_TYPE


def d_is_file(flags: int) -> bool:
    return d_is_reg(flags) or d_is_special(flags)


def d_is_negative(flags: int) -> bool:
    """negative dentry：d_inode 为 NULL，但类型字段是 MISS。"""
    return dentry_type(flags) == DCACHE_MISS_TYPE


def d_is_positive(flags: int) -> bool:
    return not d_is_negative(flags)


def d_can_lookup(flags: int) -> bool:
    return d_is_directory(flags) or d_is_autodir(flags)


def is_managed(flags: int) -> bool:
    return bool(flags & DCACHE_MANAGED_DENTRY)


class Dentry:
    """struct dentry 的模型：只保留 dcache 语义相关的字段。

    refcount 是 d_lockref.count；0 表示"未使用（unused）"，可以被回收；
    负数（dentry 被杀）在本模型里用 killed 表示。
    """

    __slots__ = ("name", "flags", "refcount", "inode", "parent", "children",
                 "lru", "killed")

    def __init__(self, name, flags=DCACHE_MISS_TYPE, inode=None, parent=None):
        self.name = name
        self.flags = flags
        self.refcount = 1
        self.inode = inode
        self.parent = parent
        self.children = []
        self.lru = False
        self.killed = False

    def set_type(self, t):
        self.flags = (self.flags & ~DCACHE_ENTRY_TYPE) | t
        return self

    def __repr__(self):
        return "Dentry(%r, type=%s, ref=%d)" % (
            self.name, TYPE_NAME.get(dentry_type(self.flags), "?"), self.refcount)


class DCache:
    """哈希 + LRU 的最小模型。

    内核里 dentry 有四种状态：in-use（refcount>0，挂在父目录的 d_children）、
    unused（refcount==0，仍在哈希里，挂在 LRU）、negative（d_inode==NULL）、
    dying/killed（已从哈希摘除，等待 RCU 释放）。
    """

    def __init__(self):
        self.table = {}          # (parent, name) -> Dentry
        self.lru = []            # 未使用 dentry 的 LRU 顺序（尾部最近使用）

    def lookup(self, parent, name):
        return self.table.get((id(parent), name))

    def insert(self, parent, name, dentry):
        dentry.parent = parent
        self.table[(id(parent), name)] = dentry
        if parent is not None:
            parent.children.append(dentry)
        return dentry

    def dget(self, dentry):
        dentry.refcount += 1
        if dentry.refcount == 1 and dentry in self.lru:
            self.lru.remove(dentry)
            dentry.lru = False
        return dentry

    def dput(self, dentry):
        dentry.refcount -= 1
        if dentry.refcount == 0 and not dentry.lru and not dentry.killed:
            self.lru.append(dentry)
            dentry.lru = True
        return dentry.refcount

    def touch_lru(self, dentry):
        """dentry 被访问：置 DCACHE_REFERENCED 并移到 LRU 尾部。"""
        dentry.flags |= DCACHE_REFERENCED
        if dentry in self.lru:
            self.lru.remove(dentry)
            self.lru.append(dentry)

    def shrink_one(self):
        """收缩一个未使用 dentry。返回被回收的名字。

        有 DCACHE_REFERENCED 的先**清标志再给第二次机会**（内核
        shrink_dentry_list 的两轮策略），而不是直接回收。
        """
        while self.lru:
            d = self.lru.pop(0)
            if d.refcount != 0:
                continue
            if d.flags & DCACHE_REFERENCED:
                d.flags &= ~DCACHE_REFERENCED
                self.lru.append(d)
                continue
            d.killed = True
            d.lru = False
            d.flags |= DCACHE_DENTRY_KILLED
            key = (id(d.parent), d.name)
            if self.table.get(key) is d:
                del self.table[key]
            return d.name
        return None

    def negative_lookup(self, parent, name):
        """查不到的路径也建 dentry——negative dentry，避免反复下探文件系统。"""
        d = Dentry(name, DCACHE_MISS_TYPE, inode=None, parent=parent)
        return self.insert(parent, name, d)


# --------------------------------------------------------------------------
# getdents64
# --------------------------------------------------------------------------
def dirent64_record(ino, off, name, dtype):
    """打包一条 linux_dirent64（d_name 以 NUL 结尾）。"""
    nb = name.encode() + b"\x00"
    reclen = DIRENT64_HEADER + len(nb)
    # d_reclen 必须对齐到 8 字节（最后一条除外也要对齐到缓冲末尾）
    pad = (-reclen) % 8
    body = struct.pack(DIRENT64_FMT, ino, off, reclen + pad, dtype) + nb
    return body + b"\x00" * pad, reclen + pad


def unpack_dirent64(buf, offset=0):
    """解出一条记录，返回 (ino, off, reclen, d_type, name, next_offset)。"""
    if offset + DIRENT64_HEADER > len(buf):
        return None
    ino, off, reclen, dtype = struct.unpack_from(DIRENT64_FMT, buf, offset)
    if reclen == 0 or offset + reclen > len(buf):
        return None
    raw = buf[offset + DIRENT64_HEADER:offset + reclen]
    name = raw.split(b"\x00", 1)[0].decode()
    return ino, off, reclen, dtype, name, offset + reclen


EINVAL = -22


def emit_getdents(entries, buffer_size):
    """模拟一次 getdents64 调用（fs/readdir.c 的 filldir64 + SYSCALL）。

    entries: [(ino, name, dtype), ...]

    返回 (buf, 写入条数, 剩余条数, 返回值)。
    内核的两个关键行为：

    1. ``if (reclen > ctx->count) return false`` —— 装不下就停，**不截断**
    2. 只要发出过至少一条，最后就 ``error = count - buf.ctx.count``，
       把先前预设的 ``buf.error = -EINVAL`` **覆盖掉**。
       所以 EINVAL 只在**一条都装不下**时才对用户可见
       （man page 的 "EINVAL: Result buffer is too small"）。
    3. 最后一条的 d_off 会被覆写成 ``buf.ctx.pos``（下一次的续读位置），
       这正是 man page 说 d_off "对用户空间没有明确含义"的原因。
    """
    out = bytearray()
    count = buffer_size
    prev_reclen = 0
    n = 0
    for i, (ino, name, dtype) in enumerate(entries):
        rec, size = dirent64_record(ino, i + 1, name, dtype)
        if size > count:
            break
        out += rec
        count -= size
        prev_reclen = size
        n += 1
    if n == 0:
        return b"", 0, len(entries), EINVAL
    # 最后一条的 d_off 覆写成 ctx.pos（下一条的位置）
    struct.pack_into("<Q", out, len(out) - prev_reclen + 8, n + 1)
    return bytes(out), n, len(entries) - n, len(out)


def walk(buf):
    """遍历一次 getdents64 返回的缓冲区。"""
    off, res = 0, []
    while True:
        r = unpack_dirent64(buf, off)
        if r is None:
            break
        res.append(r)
        off = r[5]
    return res


def dirent64_size(name):
    """名字长度 n 的记录占多少字节（含 NUL 与 8 字节对齐填充）。"""
    rec, size = dirent64_record(1, 1, name, DT_REG)
    return size
