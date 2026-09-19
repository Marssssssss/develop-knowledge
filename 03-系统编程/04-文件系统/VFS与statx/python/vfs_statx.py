"""statx(2) 请求/返回掩码语义 + struct statx 字段映射的可执行模型。

常量值全部来自实际读过的内核头文件：

  * include/uapi/linux/stat.h   —— STATX_* 请求位、STATX_ATTR_* 属性位、
                                   四种"请求/返回"组合语义的权威注释
  * include/uapi/linux/fcntl.h  —— AT_* 路径解析标志与 AT_STATX_* 同步三态

模型刻意**不**假设 mask == stx_mask：内核可能返回没请求的字段（顺手可得），
也可能清掉请求了的字段（文件系统不支持），手册原话是 "stx_mask will not be
equal mask"。
"""

# ---------------------------------------------------------------- 请求掩码位
STATX_TYPE = 0x00000001
STATX_MODE = 0x00000002
STATX_NLINK = 0x00000004
STATX_UID = 0x00000008
STATX_GID = 0x00000010
STATX_ATIME = 0x00000020
STATX_MTIME = 0x00000040
STATX_CTIME = 0x00000080
STATX_INO = 0x00000100
STATX_SIZE = 0x00000200
STATX_BLOCKS = 0x00000400
STATX_BASIC_STATS = 0x000007FF  # 普通 stat 结构里的那些字段
STATX_BTIME = 0x00000800
STATX_MNT_ID = 0x00001000
STATX_DIOALIGN = 0x00002000
STATX_MNT_ID_UNIQUE = 0x00004000
STATX_SUBVOL = 0x00008000
STATX_WRITE_ATOMIC = 0x00010000
STATX_DIO_READ_ALIGN = 0x00020000
STATX__RESERVED = 0x80000000  # 唯一会触发 EINVAL 的保留位

# STATX_ALL 已废弃：头文件注释明确写 "the same as STATX_BASIC_STATS|STATX_BTIME，
# to avoid confusion please use the equivalent"。
STATX_ALL = 0x00000FFF

# ------------------------------------------------------------------ 属性位
STATX_ATTR_COMPRESSED = 0x00000004
STATX_ATTR_IMMUTABLE = 0x00000010
STATX_ATTR_APPEND = 0x00000020
STATX_ATTR_NODUMP = 0x00000040
STATX_ATTR_ENCRYPTED = 0x00000800
STATX_ATTR_AUTOMOUNT = 0x00001000
STATX_ATTR_MOUNT_ROOT = 0x00002000
STATX_ATTR_VERITY = 0x00100000
STATX_ATTR_DAX = 0x00200000
STATX_ATTR_WRITE_ATOMIC = 0x00400000

# ------------------------------------------------------------- AT_* 标志
AT_SYMLINK_NOFOLLOW = 0x100
AT_NO_AUTOMOUNT = 0x800
AT_EMPTY_PATH = 0x1000
AT_STATX_SYNC_TYPE = 0x6000
AT_STATX_SYNC_AS_STAT = 0x0000
AT_STATX_FORCE_SYNC = 0x2000
AT_STATX_DONT_SYNC = 0x4000

# ------------------------------------------------------- st_mode 文件类型
S_IFMT = 0o170000
S_IFSOCK = 0o140000
S_IFLNK = 0o120000
S_IFREG = 0o100000
S_IFBLK = 0o060000
S_IFDIR = 0o040000
S_IFCHR = 0o020000
S_IFIFO = 0o010000


def s_isreg(m):
    return (m & S_IFMT) == S_IFREG


def s_isdir(m):
    return (m & S_IFMT) == S_IFDIR


def s_islnk(m):
    return (m & S_IFMT) == S_IFLNK


MASK_NAMES = [
    (STATX_TYPE, "TYPE"), (STATX_MODE, "MODE"), (STATX_NLINK, "NLINK"),
    (STATX_UID, "UID"), (STATX_GID, "GID"), (STATX_ATIME, "ATIME"),
    (STATX_MTIME, "MTIME"), (STATX_CTIME, "CTIME"), (STATX_INO, "INO"),
    (STATX_SIZE, "SIZE"), (STATX_BLOCKS, "BLOCKS"), (STATX_BTIME, "BTIME"),
    (STATX_MNT_ID, "MNT_ID"), (STATX_DIOALIGN, "DIOALIGN"),
    (STATX_MNT_ID_UNIQUE, "MNT_ID_UNIQUE"), (STATX_SUBVOL, "SUBVOL"),
    (STATX_WRITE_ATOMIC, "WRITE_ATOMIC"),
    (STATX_DIO_READ_ALIGN, "DIO_READ_ALIGN"),
]

ATTR_NAMES = [
    (STATX_ATTR_COMPRESSED, "COMPRESSED"), (STATX_ATTR_IMMUTABLE, "IMMUTABLE"),
    (STATX_ATTR_APPEND, "APPEND"), (STATX_ATTR_NODUMP, "NODUMP"),
    (STATX_ATTR_ENCRYPTED, "ENCRYPTED"), (STATX_ATTR_AUTOMOUNT, "AUTOMOUNT"),
    (STATX_ATTR_MOUNT_ROOT, "MOUNT_ROOT"), (STATX_ATTR_VERITY, "VERITY"),
    (STATX_ATTR_DAX, "DAX"), (STATX_ATTR_WRITE_ATOMIC, "WRITE_ATOMIC"),
]


def mask_names(mask):
    return [n for b, n in MASK_NAMES if mask & b]


def attr_names(attrs):
    return [n for b, n in ATTR_NAMES if attrs & b]


class StatxBuf(object):
    """struct statx 的用户态镜像。未填充字段保持 0/None。"""

    def __init__(self):
        self.stx_mask = 0
        self.stx_blksize = 0
        self.stx_attributes = 0
        self.stx_attributes_mask = 0
        self.stx_nlink = 0
        self.stx_uid = 0
        self.stx_gid = 0
        self.stx_mode = 0
        self.stx_ino = 0
        self.stx_size = 0
        self.stx_blocks = 0
        self.stx_btime = None
        self.stx_mtime = 0
        self.stx_mnt_id = 0
        self.stx_dio_mem_align = 0
        self.stx_dio_offset_align = 0


class Inode(object):
    """一个被统计的 inode。blocks512 是 512B 单位，带洞文件会小于 size/512。"""

    def __init__(self, ino, mode, size, blocks512, nlink=1, uid=0, gid=0,
                 mtime=0, btime=None, mnt_id=0, dio_mem=0, dio_off=0,
                 link_target=None):
        self.ino = ino
        self.mode = mode
        self.size = size
        self.blocks512 = blocks512
        self.nlink = nlink
        self.uid = uid
        self.gid = gid
        self.mtime = mtime
        self.btime = btime
        self.mnt_id = mnt_id
        self.dio_mem = dio_mem
        self.dio_off = dio_off
        self.link_target = link_target


class Filesystem(object):
    """模拟一个具体文件系统的 statx 能力。

    supported : 能填出来的 STATX_* 位（其余请求位会被清掉）
    free_bits : 没请求也顺手填的位（"available in approximate form without
                any effort" —— 头注释原文）
    attr_mask : stx_attributes_mask，只有落在这里的属性位才有意义
    dummy_uid : 不支持 UID 时编造的兼容值（CIFS 挂载就是这种情形）
    """

    def __init__(self, name, supported, free_bits=0, attr_mask=0,
                 attributes=0, blksize=4096, dummy_uid=None):
        self.name = name
        self.supported = supported
        self.free_bits = free_bits
        self.attr_mask = attr_mask
        self.attributes = attributes
        self.blksize = blksize
        self.dummy_uid = dummy_uid


def sync_mode(flags):
    """解析 AT_STATX_* 三态；同时置 FORCE|DONT 是非法组合 → EINVAL。"""
    sel = flags & AT_STATX_SYNC_TYPE
    if sel == AT_STATX_FORCE_SYNC:
        return "force_sync"
    if sel == AT_STATX_DONT_SYNC:
        return "dont_sync"
    if sel == AT_STATX_SYNC_AS_STAT:
        return "as_stat"
    raise ValueError("EINVAL: AT_STATX_SYNC_TYPE 非法 (FORCE 与 DONT 同时置位)")


def statx(fs, inode, mask, flags=0, on_field=None):
    """按 stat.h 头注释的四条规则填充 StatxBuf。返回 (buf, sync_mode)。

    on_field(bit) 会在"填完一个字段后"被调用，用来模拟手册里那句提醒：
    出于性能与实现简单的考虑，struct statx 的不同字段可能来自**同一趟系统调用
    里的不同时刻**（另一进程并发 chmod/chown 时，可能拿到旧 mode + 新 uid）。
    """
    if mask & STATX__RESERVED:
        raise ValueError("EINVAL: mask 里带了 STATX__RESERVED (0x80000000)")
    sync_mode(flags)  # 非法组合同样 EINVAL

    buf = StatxBuf()
    buf.stx_blksize = fs.blksize  # [uncond]
    buf.stx_attributes_mask = fs.attr_mask  # [uncond]
    buf.stx_attributes = fs.attributes & fs.attr_mask

    got = 0

    # 规则 1/2：显式请求位 —— 支持就填并置位；不支持就清位并给编造值
    for bit in [b for b, _ in MASK_NAMES]:
        if not (mask & bit):
            continue
        if not (fs.supported & bit):
            _fill_dummy(buf, inode, fs, bit)  # 位保持清除
            continue
        _fill(buf, inode, fs, bit)
        got |= bit
        if on_field is not None:
            on_field(bit)

    # 规则 3：没请求但顺手可得 → 照样填并置位（stx_mask 会超出请求范围）
    for bit in [b for b, _ in MASK_NAMES]:
        if mask & bit:
            continue
        if (fs.free_bits & bit) and (fs.supported & bit):
            _fill(buf, inode, fs, bit)
            got |= bit

    buf.stx_mask = got
    return buf, sync_mode(flags)


def _fill(buf, inode, fs, bit):
    if bit == STATX_TYPE or bit == STATX_MODE:
        buf.stx_mode = inode.mode
    elif bit == STATX_NLINK:
        buf.stx_nlink = inode.nlink
    elif bit == STATX_UID:
        buf.stx_uid = inode.uid
    elif bit == STATX_GID:
        buf.stx_gid = inode.gid
    elif bit == STATX_INO:
        buf.stx_ino = inode.ino
    elif bit == STATX_SIZE:
        buf.stx_size = _stx_size(inode)
    elif bit == STATX_BLOCKS:
        buf.stx_blocks = inode.blocks512
    elif bit == STATX_MTIME:
        buf.stx_mtime = inode.mtime
    elif bit == STATX_BTIME:
        buf.stx_btime = inode.btime
    elif bit == STATX_MNT_ID:
        buf.stx_mnt_id = inode.mnt_id
    elif bit == STATX_DIOALIGN:
        buf.stx_dio_mem_align = inode.dio_mem
        buf.stx_dio_offset_align = inode.dio_off


def _fill_dummy(buf, inode, fs, bit):
    """不支持的字段：为兼容 stat() 编造一个值，但对应的 mask 位**不**置位。"""
    if bit == STATX_UID and fs.dummy_uid is not None:
        buf.stx_uid = fs.dummy_uid
    elif bit == STATX_SIZE:
        buf.stx_size = _stx_size(inode)  # 尺寸总能编出来，但位仍清除


def _stx_size(inode):
    # 符号链接的 stx_size 是它装的**路径长度**，不含结尾 NUL（statx(2) 原文）
    if s_islnk(inode.mode) and inode.link_target is not None:
        return len(inode.link_target)
    return inode.size


def allocated_bytes(buf):
    """stx_blocks 的单位是 512 字节（不是 blksize，也不是 1024）。"""
    return buf.stx_blocks * 512


def apparent_ratio(inode):
    """有洞的文件：实际分配 < size。返回 (size, 实际占用字节)。"""
    return inode.size, inode.blocks512 * 512


def dio_align_consistent(buf):
    """statx(2)：offset align 只在 mem align 非 0 时才非 0，反之亦然。"""
    if buf.stx_dio_mem_align == 0:
        return buf.stx_dio_offset_align == 0
    return buf.stx_dio_offset_align != 0


def supported_attrs(buf):
    """只有落在 stx_attributes_mask 里的属性位才是可用值。"""
    return buf.stx_attributes & buf.stx_attributes_mask
