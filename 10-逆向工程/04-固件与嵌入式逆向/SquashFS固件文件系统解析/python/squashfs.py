"""SquashFS 4.0 固件文件系统的骨架解析。

常量与结构体取自 torvalds/linux@master：
  - `fs/squashfs/squashfs_fs.h`：superblock、flags 位、压缩 id、inode 号编码
  - `Documentation/filesystems/squashfs.rst`：8K 元数据块、fragments、目录两级表
  - `fs/squashfs/dir.c`：`dir_count = le32_to_cpu(dirh.count) + 1`

固件逆向里的用途：路由器/摄像头固件几乎都把 rootfs 打成 squashfs，
先认出 superblock 才能拿到 inode/directory/fragment 三张表的偏移，
再用 <block, offset> 48 位编码还原目录树。
"""

import struct

SQUASHFS_MAGIC = 0x73717368          # "hsqs"，小端读出
SQUASHFS_MAJOR = 4
SQUASHFS_MINOR = 0
SQUASHFS_METADATA_SIZE = 8192
SQUASHFS_BLOCK_OFFSET = 2
SQUASHFS_NAME_LEN = 256
SQUASHFS_DIR_COUNT = 256
SQUASHFS_INVALID_FRAG = 0xFFFFFFFF
SQUASHFS_INVALID_XATTR = 0xFFFFFFFF
SQUASHFS_FILE_MAX_SIZE = 1048576
SQUASHFS_FILE_MAX_LOG = 20

# flags 位号（squashfs_fs.h）
FLAG_NOI = 0
FLAG_NOD = 1
FLAG_NOF = 3
FLAG_NO_FRAG = 4
FLAG_ALWAYS_FRAG = 5
FLAG_DUPLICATE = 6
FLAG_EXPORT = 7
FLAG_COMP_OPT = 10

# 压缩 id（squashfs_fs.h）
ZLIB_COMPRESSION = 1
LZMA_COMPRESSION = 2
LZO_COMPRESSION = 3
XZ_COMPRESSION = 4
LZ4_COMPRESSION = 5
ZSTD_COMPRESSION = 6

# inode 类型
SQUASHFS_DIR_TYPE = 1
SQUASHFS_REG_TYPE = 2
SQUASHFS_SYMLINK_TYPE = 3
SQUASHFS_BLKDEV_TYPE = 4
SQUASHFS_CHRDEV_TYPE = 5
SQUASHFS_FIFO_TYPE = 6
SQUASHFS_SOCKET_TYPE = 7
SQUASHFS_LDIR_TYPE = 8
SQUASHFS_LREG_TYPE = 9
SQUASHFS_LSYMLINK_TYPE = 10
SQUASHFS_LBLKDEV_TYPE = 11
SQUASHFS_LCHRDEV_TYPE = 12
SQUASHFS_LFIFO_TYPE = 13
SQUASHFS_LSOCKET_TYPE = 14
SQUASHFS_MAX_DIR_TYPE = 7

SQUASHFS_COMPRESSED_BIT = 1 << 15
SQUASHFS_COMPRESSED_BIT_BLOCK = 1 << 24

# superblock 字段顺序（全部小端）
SB_FIELDS = [
    ("s_magic", "I"), ("inodes", "I"), ("mkfs_time", "I"), ("block_size", "I"),
    ("fragments", "I"), ("compression", "H"), ("block_log", "H"), ("flags", "H"),
    ("no_ids", "H"), ("s_major", "H"), ("s_minor", "H"), ("root_inode", "Q"),
    ("bytes_used", "Q"), ("id_table_start", "Q"), ("xattr_id_table_start", "Q"),
    ("inode_table_start", "Q"), ("directory_table_start", "Q"),
    ("fragment_table_start", "Q"), ("lookup_table_start", "Q"),
]

SUPERBLOCK_SIZE = 96


def squashfs_bit(flags, bit):
    """squashfs_fs.h: #define SQUASHFS_BIT(flag, bit) ((flag >> bit) & 1)"""
    return (flags >> bit) & 1


def compressed(b):
    """2 字节元数据块头：bit15 置位表示**未**压缩。"""
    return not (b & SQUASHFS_COMPRESSED_BIT)


def compressed_size(b):
    """squashfs_fs.h 的 SQUASHFS_COMPRESSED_SIZE：
    ((B) & ~BIT) ? (B) & ~BIT : BIT
    即低 15 位为 0 时整个取 32768，而不是 0。"""
    low = b & ~SQUASHFS_COMPRESSED_BIT
    return low if low else SQUASHFS_COMPRESSED_BIT


def compressed_block(b):
    return not (b & SQUASHFS_COMPRESSED_BIT_BLOCK)


def compressed_size_block(b):
    return b & ~SQUASHFS_COMPRESSED_BIT_BLOCK


def inode_blk(a):
    return (a >> 16) & 0xFFFFFFFF


def inode_offset(a):
    return a & 0xFFFF


def mkinode(blk, off):
    """inode 号 = 元数据块号 << 16 | 块内偏移，共 48 位。"""
    return ((blk << 16) + off) & 0xFFFFFFFFFFFF


def fragment_bytes(n):
    """struct squashfs_fragment_entry { __le64 start_block; __le32 size;
    unsigned int unused; } —— 小端且紧排，共 16 字节。"""
    return n * 16


def fragment_index(n):
    return fragment_bytes(n) // SQUASHFS_METADATA_SIZE


def fragment_index_offset(n):
    return fragment_bytes(n) % SQUASHFS_METADATA_SIZE


def fragment_indexes(n):
    return (fragment_bytes(n) + SQUASHFS_METADATA_SIZE - 1) // SQUASHFS_METADATA_SIZE


def fragment_index_bytes(n):
    return fragment_indexes(n) * 8          # 每个索引项是一个 __le64


def lookup_bytes(n):
    return n * 8


def lookup_blocks(n):
    return (lookup_bytes(n) + SQUASHFS_METADATA_SIZE - 1) // SQUASHFS_METADATA_SIZE


def lookup_block(n):
    return lookup_bytes(n) // SQUASHFS_METADATA_SIZE


def lookup_block_offset(n):
    return lookup_bytes(n) % SQUASHFS_METADATA_SIZE


def id_blocks(n):
    idb = n * 4
    return (idb + SQUASHFS_METADATA_SIZE - 1) // SQUASHFS_METADATA_SIZE


def xattr_blk(a):
    """xattr 引用也是 <block, offset> 编码。"""
    return (a >> 16) & 0xFFFF


def xattr_offset(a):
    return a & 0xFFFF


class Superblock(object):
    def __init__(self, **kw):
        for name, _ in SB_FIELDS:
            setattr(self, name, kw.get(name, 0))

    def pack(self):
        fmt = "<" + "".join(f for _, f in SB_FIELDS)
        return struct.pack(fmt, *[getattr(self, n) for n, _ in SB_FIELDS])

    def flag(self, bit):
        return squashfs_bit(self.flags, bit)


def parse_superblock(blob, offset=0):
    raw = blob[offset:offset + SUPERBLOCK_SIZE]
    if len(raw) < SUPERBLOCK_SIZE:
        return None
    fmt = "<" + "".join(f for _, f in SB_FIELDS)
    vals = struct.unpack(fmt, raw[:struct.calcsize(fmt)])
    return Superblock(**dict(zip([n for n, _ in SB_FIELDS], vals)))


def sb_field_offsets():
    """逐字段在 superblock 内的字节偏移，用于直接对着 hexdump 找表。"""
    offs = {}
    cur = 0
    for name, f in SB_FIELDS:
        offs[name] = cur
        cur += struct.calcsize("<" + f)
    return offs


def metadata_block_at(blob, start):
    """读一个元数据块：2 字节头 + 数据。返回 (是否压缩, 长度, 数据起点)。"""
    (hdr,) = struct.unpack("<H", blob[start:start + 2])
    return compressed(hdr), compressed_size(hdr), start + 2


def walk_metadata(blob, table_start, count):
    """顺序扫一张表：每个 8K 元数据块前有一个 2 字节块头。"""
    pos = table_start
    seen = []
    for _ in range(count):
        is_c, size, data = metadata_block_at(blob, pos)
        seen.append({"compressed": is_c, "size": size, "data_off": data})
        pos = data + size
    return seen


def dir_count(raw_count):
    """fs/squashfs/dir.c: dir_count = le32_to_cpu(dirh.count) + 1"""
    return raw_count + 1


def dir_count_ok(raw_count):
    return dir_count(raw_count) <= SQUASHFS_DIR_COUNT


def datablock_count(file_size, block_log, has_fragment):
    """普通文件的数据块表项数：尾部进了 fragment 就不再单列一个整块。"""
    bs = 1 << block_log
    full, rem = divmod(file_size, bs)
    if rem and not has_fragment:
        full += 1
    return full


def reg_inode_blocks(block_list, file_size, block_log):
    """按块长表还原每个数据块解压后的长度。"""
    bs = 1 << block_log
    sizes = [bs] * (file_size // bs)
    rem = file_size % bs
    if rem:
        sizes.append(rem)
    return list(zip(block_list, sizes))
