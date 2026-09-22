"""JFFS2 与 UBI 的闪存结构解析。

来源（torvalds/linux@master）：
  - `include/uapi/linux/jffs2.h`：magic、nodetype 位组合、兼容标志、结点结构
  - `fs/jffs2/summary.c` / `readinode.c`：CRC 的覆盖范围
        hdr_crc  = crc32(0, node, sizeof(struct jffs2_unknown_node) - 4)
        node_crc = crc32(0, rd,   sizeof(*rd) - 8)
        data_crc = crc32(0, buf,  len)
  - `drivers/mtd/ubi/ubi-media.h`：EC/VID 头、CRC 种子、内部卷 id、fastmap 魔数
  - `drivers/mtd/ubi/io.c`：crc = crc32(UBI_CRC32_INIT, hdr, UBI_EC_HDR_SIZE_CRC)

CRC 一律是 Linux `crc32(seed, ...)`：LSB-first、多项式 0xEDB88320、首尾不取反
（`include/linux/crc32.h`）。JFFS2 用 seed 0，UBI 用 seed 0xFFFFFFFF。
"""

import struct

# ---------------------------------------------------------------- JFFS2
JFFS2_OLD_MAGIC_BITMASK = 0x1984
JFFS2_MAGIC_BITMASK = 0x1985
KSAMTIB_CIGAM_2SFFJ = 0x8519          # 用来识别字节序反了的文件系统
JFFS2_EMPTY_BITMASK = 0xFFFF
JFFS2_DIRTY_BITMASK = 0x0000
JFFS2_SUM_MAGIC = 0x02851885
JFFS2_MAX_NAME_LEN = 254
JFFS2_MIN_DATA_LEN = 128

JFFS2_COMPR_NONE = 0x00
JFFS2_COMPR_ZERO = 0x01
JFFS2_COMPR_RTIME = 0x02
JFFS2_COMPR_RUBINMIPS = 0x03
JFFS2_COMPR_COPY = 0x04
JFFS2_COMPR_DYNRUBIN = 0x05
JFFS2_COMPR_ZLIB = 0x06
JFFS2_COMPR_LZO = 0x07

JFFS2_COMPAT_MASK = 0xC000
JFFS2_NODE_ACCURATE = 0x2000
JFFS2_FEATURE_INCOMPAT = 0xC000
JFFS2_FEATURE_ROCOMPAT = 0x8000
JFFS2_FEATURE_RWCOMPAT_COPY = 0x4000
JFFS2_FEATURE_RWCOMPAT_DELETE = 0x0000

JFFS2_NODETYPE_DIRENT = JFFS2_FEATURE_INCOMPAT | JFFS2_NODE_ACCURATE | 1   # 0xE001
JFFS2_NODETYPE_INODE = JFFS2_FEATURE_INCOMPAT | JFFS2_NODE_ACCURATE | 2    # 0xE002
JFFS2_NODETYPE_CLEANMARKER = JFFS2_FEATURE_RWCOMPAT_DELETE | JFFS2_NODE_ACCURATE | 3  # 0x2003
JFFS2_NODETYPE_PADDING = JFFS2_FEATURE_RWCOMPAT_DELETE | JFFS2_NODE_ACCURATE | 4      # 0x2004
JFFS2_NODETYPE_SUMMARY = JFFS2_FEATURE_RWCOMPAT_DELETE | JFFS2_NODE_ACCURATE | 6      # 0x2006
JFFS2_NODETYPE_XATTR = JFFS2_FEATURE_INCOMPAT | JFFS2_NODE_ACCURATE | 8    # 0xE008
JFFS2_NODETYPE_XREF = JFFS2_FEATURE_INCOMPAT | JFFS2_NODE_ACCURATE | 9     # 0xE009

JFFS2_XPREFIX_USER = 1
JFFS2_XPREFIX_SECURITY = 2
JFFS2_XPREFIX_ACL_ACCESS = 3
JFFS2_XPREFIX_ACL_DEFAULT = 4
JFFS2_XPREFIX_TRUSTED = 5

# struct jffs2_unknown_node {jint16 magic; jint16 nodetype; jint32 totlen; jint32 hdr_crc;}
UNKNOWN_NODE_SIZE = 12
DIRENT_HDR_SIZE = 40          # 到 name_crc 为止（不含 name[]）
INODE_HDR_SIZE = 68           # 到 node_crc 为止（不含 data[]）

from fwcrc import crc32
from ubi import (  # 兼容 `jffs2ubi.xxx` 的旧调用：UBI 段已拆到 ubi.py
    UBI_VERSION, UBI_CRC32_INIT, UBI_EC_HDR_MAGIC, UBI_VID_HDR_MAGIC,
    UBI_VID_DYNAMIC, UBI_VID_STATIC, UBI_INTERNAL_VOL_START, UBI_LAYOUT_VOLUME_ID,
    UBI_MAX_VOLUMES, UBI_VOL_NAME_MAX, EC_HDR_SIZE, VID_HDR_SIZE,
    UBI_FM_SB_MAGIC, UBI_FM_HDR_MAGIC, UBI_FM_VHDR_MAGIC, UBI_FM_POOL_MAGIC,
    UBI_FM_EBA_MAGIC, UBI_FM_MAX_START, UBI_FM_MAX_BLOCKS, UBI_FM_MIN_POOL_SIZE,
    UBI_FM_MAX_POOL_SIZE,
    EcHeader, pack_ec, parse_ec, check_ec_crc,
    VidHeader, pack_vid, parse_vid, check_vid_crc, pick_peb, is_internal_vol,
)


# ---------------------------------------------------------------- JFFS2 结点
class RawNode(object):
    def __init__(self, nodetype, totlen, magic=JFFS2_MAGIC_BITMASK, hdr_crc=0):
        self.magic = magic
        self.nodetype = nodetype
        self.totlen = totlen
        self.hdr_crc = hdr_crc

    def common(self):
        return struct.pack("<HHII", self.magic, self.nodetype, self.totlen, self.hdr_crc)


def seal_common(n):
    """hdr_crc 覆盖 magic + nodetype + totlen（12 - 4 = 8 字节），seed 0。"""
    body = struct.pack("<HHI", n.magic, n.nodetype, n.totlen)
    n.hdr_crc = crc32(body, 0)
    return n


def check_hdr_crc(n):
    body = struct.pack("<HHI", n.magic, n.nodetype, n.totlen)
    return crc32(body, 0) == n.hdr_crc


def parse_common(blob, off=0):
    magic, nodetype, totlen, hdr_crc = struct.unpack("<HHII", blob[off:off + UNKNOWN_NODE_SIZE])
    return RawNode(nodetype, totlen, magic, hdr_crc)


def node_compat(nodetype):
    return nodetype & JFFS2_COMPAT_MASK


def node_accurate(nodetype):
    return bool(nodetype & JFFS2_NODE_ACCURATE)


def is_incompat(nodetype):
    return node_compat(nodetype) == JFFS2_FEATURE_INCOMPAT


def is_rompat(nodetype):
    return node_compat(nodetype) == JFFS2_FEATURE_ROCOMPAT


def is_rwcompat_copy(nodetype):
    return node_compat(nodetype) == JFFS2_FEATURE_RWCOMPAT_COPY


def is_rwcompat_delete(nodetype):
    return node_compat(nodetype) == JFFS2_FEATURE_RWCOMPAT_DELETE


def swapped_endian(magic):
    """读到 0x8519 说明这份镜像是反字节序写的。"""
    return magic == KSAMTIB_CIGAM_2SFFJ


def build_dirent(pino, version, ino, mctime, nsize, dtype, name):
    """struct jffs2_raw_dirent（全小端、紧排）：

        magic(2) nodetype(2) totlen(4) hdr_crc(4)
        pino(4) version(4) ino(4) mctime(4) nsize(1) type(1) unused(2)
        node_crc(4) name_crc(4) name[]

    node_crc 覆盖结点头的前 32 字节（= DIRENT_HDR_SIZE - 8），即不含
    node_crc 与 name_crc 本身；name_crc 只覆盖 name。
    """
    n = RawNode(JFFS2_NODETYPE_DIRENT, DIRENT_HDR_SIZE + len(name))
    seal_common(n)          # hdr_crc 必须先落位，它本身也在 node_crc 的覆盖里
    out = n.common()
    out += struct.pack("<IIII", pino, version, ino, mctime)
    out += struct.pack("<BB", nsize, dtype)
    out += b"\0\0"
    out += struct.pack("<II", crc32(out, 0), crc32(name, 0))
    out += name
    return _finish(out, n)


def _finish(out, n):
    out = bytearray(out)
    out[0:UNKNOWN_NODE_SIZE] = seal_common(RawNode(n.nodetype, n.totlen)).common()
    return bytes(out)


def check_dirent_node_crc(blob, off=0):
    """node_crc 覆盖结点头（不含 data_crc/node_crc 两个尾部字段）。"""
    hdr = blob[off:off + DIRENT_HDR_SIZE - 8]
    (stored,) = struct.unpack("<I", blob[off + DIRENT_HDR_SIZE - 8:off + DIRENT_HDR_SIZE - 4])
    return crc32(hdr, 0) == stored


def check_name_crc(blob, off, name):
    (stored,) = struct.unpack("<I", blob[off + DIRENT_HDR_SIZE - 4:off + DIRENT_HDR_SIZE])
    return crc32(name, 0) == stored


def build_raw_inode(ino, version, mode, uid, gid, isize, offset, csize, dsize,
                    compr, data):
    n = RawNode(JFFS2_NODETYPE_INODE, INODE_HDR_SIZE + len(data))
    seal_common(n)
    out = n.common()
    out += struct.pack("<IIIHH", ino, version, mode, uid, gid)
    out += struct.pack("<IIIII", isize, 0, 0, 0, offset)   # isize/atime/mtime/ctime/offset
    out += struct.pack("<II", csize, dsize)
    out += struct.pack("<BBH", compr, 0, 0)                # compr/usercompr/flags
    out += struct.pack("<I", crc32(data, 0))               # data_crc
    assert len(out) == INODE_HDR_SIZE - 4, len(out)
    out += struct.pack("<I", crc32(out[:INODE_HDR_SIZE - 8], 0))  # 不含 data_crc
    out += data
    return _finish(out, n)


def check_inode_node_crc(blob, off=0):
    (stored,) = struct.unpack("<I", blob[off + INODE_HDR_SIZE - 4:off + INODE_HDR_SIZE])
    return crc32(blob[off:off + INODE_HDR_SIZE - 8], 0) == stored


def check_inode_data_crc(blob, off, data):
    (stored,) = struct.unpack("<I", blob[off + INODE_HDR_SIZE - 8:off + INODE_HDR_SIZE - 4])
    return crc32(data, 0) == stored

