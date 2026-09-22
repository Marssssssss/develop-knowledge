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
