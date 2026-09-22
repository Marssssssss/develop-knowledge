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

# ---------------------------------------------------------------- UBI
UBI_VERSION = 1
UBI_CRC32_INIT = 0xFFFFFFFF
UBI_EC_HDR_MAGIC = 0x55424923          # "UBI#"
UBI_VID_HDR_MAGIC = 0x55424921         # "UBI!"
UBI_VID_DYNAMIC = 1
UBI_VID_STATIC = 2
UBI_INTERNAL_VOL_START = 0x7FFFFFFF - 4096
UBI_LAYOUT_VOLUME_ID = UBI_INTERNAL_VOL_START
UBI_MAX_VOLUMES = 128
UBI_VOL_NAME_MAX = 127
EC_HDR_SIZE = 64
VID_HDR_SIZE = 64

UBI_FM_SB_MAGIC = 0x7B11D69F
UBI_FM_HDR_MAGIC = 0xD4B82EF7
UBI_FM_VHDR_MAGIC = 0xFA370ED1
UBI_FM_POOL_MAGIC = 0x67AF4D08
UBI_FM_EBA_MAGIC = 0xF0C040A8
UBI_FM_MAX_START = 64
UBI_FM_MAX_BLOCKS = 32
UBI_FM_MIN_POOL_SIZE = 8
UBI_FM_MAX_POOL_SIZE = 256


def _make_table():
    tbl = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ (0xEDB88320 if (c & 1) else 0)
        tbl.append(c)
    return tbl


_TABLE = _make_table()


def crc32(data, seed=0):
    crc = seed & 0xFFFFFFFF
    for b in data:
        crc = _TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc & 0xFFFFFFFF


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


# ---------------------------------------------------------------- UBI
class EcHeader(object):
    def __init__(self, ec=0, vid_hdr_offset=2048, data_offset=4096, image_seq=0,
                 hdr_crc=0, version=UBI_VERSION, magic=UBI_EC_HDR_MAGIC):
        self.magic = magic
        self.version = version
        self.ec = ec
        self.vid_hdr_offset = vid_hdr_offset
        self.data_offset = data_offset
        self.image_seq = image_seq
        self.hdr_crc = hdr_crc


def pack_ec(e):
    """struct ubi_ec_hdr 全部 __be；hdr_crc 覆盖前面 60 字节。"""
    b = struct.pack(">IB3xQIII32x", e.magic, e.version, e.ec,
                    e.vid_hdr_offset, e.data_offset, e.image_seq)
    crc = crc32(b, UBI_CRC32_INIT)
    return b + struct.pack(">I", crc)


def parse_ec(blob):
    magic, version, ec, voff, doff, iseq = struct.unpack(">IB3xQIII", blob[:28])
    (hcrc,) = struct.unpack(">I", blob[EC_HDR_SIZE - 4:EC_HDR_SIZE])
    e = EcHeader(ec, voff, doff, iseq, hcrc, version, magic)
    return e


def check_ec_crc(blob):
    return crc32(blob[:EC_HDR_SIZE - 4], UBI_CRC32_INIT) == parse_ec(blob).hdr_crc


class VidHeader(object):
    def __init__(self, vol_id=0, lnum=0, sqnum=0, hdr_crc=0,
                 vol_type=UBI_VID_DYNAMIC, copy_flag=0, compat=0,
                 data_size=0, used_ebs=0, data_pad=0, data_crc=0,
                 version=UBI_VERSION, magic=UBI_VID_HDR_MAGIC):
        self.magic = magic
        self.version = version
        self.vol_type = vol_type
        self.copy_flag = copy_flag
        self.compat = compat
        self.vol_id = vol_id
        self.lnum = lnum
        self.data_size = data_size
        self.used_ebs = used_ebs
        self.data_pad = data_pad
        self.data_crc = data_crc
        self.sqnum = sqnum
        self.hdr_crc = hdr_crc


def pack_vid(v):
    b = struct.pack(">IBBBBII4xIIII4xQ12x", v.magic, v.version, v.vol_type,
                    v.copy_flag, v.compat, v.vol_id, v.lnum, v.data_size,
                    v.used_ebs, v.data_pad, v.data_crc, v.sqnum)
    crc = crc32(b, UBI_CRC32_INIT)
    return b + struct.pack(">I", crc)


def parse_vid(blob):
    (magic, version, vol_type, copy_flag, compat, vol_id, lnum) = struct.unpack(
        ">IBBBBII", blob[:16])
    (data_size, used_ebs, data_pad, data_crc) = struct.unpack(">4I", blob[20:36])
    (sqnum,) = struct.unpack(">Q", blob[40:48])
    (hcrc,) = struct.unpack(">I", blob[VID_HDR_SIZE - 4:VID_HDR_SIZE])
    return VidHeader(vol_id, lnum, sqnum, hcrc, vol_type, copy_flag, compat,
                     data_size, used_ebs, data_pad, data_crc, version, magic)


def check_vid_crc(blob):
    return crc32(blob[:VID_HDR_SIZE - 4], UBI_CRC32_INIT) == parse_vid(blob).hdr_crc


def pick_peb(old, new, new_crc_ok):
    """UBI 在 (vol_id, lnum) 撞车时挑哪一块：

    old/new 是同一个 LEB 的两份 VID 头。new.sqnum 更大；若 new 的 copy_flag
    为 0 直接选 new，否则看它的 data_crc 是否正确——正确选 new，否则退回 old。
    """
    if not new.copy_flag:
        return new
    return new if new_crc_ok else old


def is_internal_vol(vol_id):
    return vol_id >= UBI_INTERNAL_VOL_START
