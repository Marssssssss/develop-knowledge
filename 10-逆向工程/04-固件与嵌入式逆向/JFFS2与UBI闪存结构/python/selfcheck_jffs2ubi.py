"""demo593 自检：JFFS2 结点与 UBI EC/VID 头。"""

import struct
import sys

import jffs2ubi as ju

PASS = 0
FAIL = []


def ck(c, label):
    global PASS
    if c:
        PASS += 1
    else:
        FAIL.append(label)


def eq(got, want, label):
    ck(got == want, "%s (got=%r want=%r)" % (label, got, want))


# ---------------------------------------------------------------- A. JFFS2 常量
eq(ju.JFFS2_MAGIC_BITMASK, 0x1985, "A1 magic 0x1985")
eq(ju.JFFS2_OLD_MAGIC_BITMASK, 0x1984, "A2 旧版 0x1984")
eq(ju.KSAMTIB_CIGAM_2SFFJ, 0x8519, "A3 反字节序魔数")
eq(ju.JFFS2_EMPTY_BITMASK, 0xFFFF, "A4 擦除后的空位")
eq(ju.JFFS2_DIRTY_BITMASK, 0x0000, "A5 dirty")
eq(ju.JFFS2_SUM_MAGIC, 0x02851885, "A6 summary 魔数")
eq(ju.JFFS2_MAX_NAME_LEN, 254, "A7 名字最长 254")
eq(ju.JFFS2_MIN_DATA_LEN, 128, "A8 最短数据结点")
eq(ju.JFFS2_NODETYPE_DIRENT, 0xE001, "A9 DIRENT")
eq(ju.JFFS2_NODETYPE_INODE, 0xE002, "A10 INODE")
eq(ju.JFFS2_NODETYPE_CLEANMARKER, 0x2003, "A11 CLEANMARKER")
eq(ju.JFFS2_NODETYPE_PADDING, 0x2004, "A12 PADDING")
eq(ju.JFFS2_NODETYPE_SUMMARY, 0x2006, "A13 SUMMARY")
eq(ju.JFFS2_NODETYPE_XATTR, 0xE008, "A14 XATTR")
eq(ju.JFFS2_NODETYPE_XREF, 0xE009, "A15 XREF")
eq(ju.JFFS2_COMPR_NONE, 0, "A16 COMPR_NONE")
eq(ju.JFFS2_COMPR_ZLIB, 6, "A17 COMPR_ZLIB")
eq(ju.JFFS2_COMPR_LZO, 7, "A18 COMPR_LZO")

# ---------------------------------------------------------------- B. 兼容位
ck(ju.is_incompat(ju.JFFS2_NODETYPE_INODE), "B1 INODE 是 INCOMPAT")
ck(ju.is_rwcompat_delete(ju.JFFS2_NODETYPE_CLEANMARKER), "B2 CLEANMARKER 可删")
ck(ju.is_rwcompat_delete(ju.JFFS2_NODETYPE_PADDING), "B3 PADDING 可删")
ck(not ju.is_incompat(ju.JFFS2_NODETYPE_PADDING), "B4 PADDING 不是 INCOMPAT（对偶）")
ck(ju.node_accurate(ju.JFFS2_NODETYPE_DIRENT), "B5 ACCURATE 位置位")
eq(ju.node_compat(0xE001), 0xC000, "B6 兼容掩码取高两位")
eq(ju.node_compat(0x2003), 0x0000, "B7 DELETE 的高两位是 0")
ck(ju.swapped_endian(0x8519), "B8 0x8519 视为反字节序")
ck(not ju.swapped_endian(0x1985), "B9 0x1985 正常")
ck(ju.is_internal_vol(ju.UBI_LAYOUT_VOLUME_ID), "B10 layout 是内部卷")
ck(not ju.is_internal_vol(0), "B11 卷 0 是用户卷")

# ---------------------------------------------------------------- C. hdr_crc 覆盖
n = ju.RawNode(ju.JFFS2_NODETYPE_INODE, 100)
ju.seal_common(n)
blob = n.common() + b"\x00" * 88
p = ju.parse_common(blob)
eq(p.magic, ju.JFFS2_MAGIC_BITMASK, "C1 magic 回读")
eq(p.nodetype, ju.JFFS2_NODETYPE_INODE, "C2 nodetype 回读")
eq(p.totlen, 100, "C3 totlen 回读")
ck(ju.check_hdr_crc(p), "C4 hdr_crc 校验通过")
b2 = bytearray(blob)
b2[6] ^= 0xFF                      # 改 totlen 的低字节
ck(not ju.check_hdr_crc(ju.parse_common(bytes(b2))), "C5 改 totlen 后失败")
b3 = bytearray(blob)
b3[5] ^= 0xFF                      # 改 totlen 的高字节
ck(not ju.check_hdr_crc(ju.parse_common(bytes(b3))), "C6 totlen 两字节都在覆盖内")
b4 = bytearray(blob)
b4[4] ^= 0xFF                      # 改 nodetype
ck(not ju.check_hdr_crc(ju.parse_common(bytes(b4))), "C7 nodetype 在覆盖内")
b5 = bytearray(blob)
b5[0] ^= 0xFF                      # 改 magic
ck(not ju.check_hdr_crc(ju.parse_common(bytes(b5))), "C8 magic 也在覆盖内")
b6 = bytearray(blob)
b6[12] ^= 0xFF                     # 头之后的数据不在覆盖内
ck(ju.check_hdr_crc(ju.parse_common(bytes(b6))), "C9 头外数据不影响 hdr_crc（对偶）")
eq(ju.UNKNOWN_NODE_SIZE, 12, "C10 通用头 12 字节")

# ---------------------------------------------------------------- D. dirent
name = b"etc"
d = ju.build_dirent(pino=1, version=3, ino=42, mctime=1700000000,
                    nsize=len(name), dtype=4, name=name)
eq(len(d), ju.DIRENT_HDR_SIZE + len(name), "D1 dirent 总长")
eq(ju.parse_common(d).nodetype, ju.JFFS2_NODETYPE_DIRENT, "D2 类型")
ck(ju.check_hdr_crc(ju.parse_common(d)), "D3 hdr_crc")
ck(ju.check_dirent_node_crc(d), "D4 node_crc 覆盖到 name_crc 之前")
ck(ju.check_name_crc(d, 0, name), "D5 name_crc 只覆盖名字")
ck(not ju.check_name_crc(d, 0, name + b"x"), "D6 名字改了 name_crc 失败")
d2 = bytearray(d)
d2[20] ^= 0xFF                     # 动 pino
ck(not ju.check_dirent_node_crc(bytes(d2)), "D7 pino 在 node_crc 覆盖内")
d3 = bytearray(d)
d3[ju.DIRENT_HDR_SIZE] ^= 0xFF     # 动 name 的第一个字节
ck(not ju.check_name_crc(bytes(d3), 0, bytes(d3)[ju.DIRENT_HDR_SIZE:]),
   "D8 名字被改后与落盘的 name_crc 不符")
ck(ju.check_dirent_node_crc(bytes(d3)), "D9 name 不在 node_crc 内（对偶）")
eq(struct.unpack("<I", d[12:16])[0], 1, "D10 pino 在偏移 12")
eq(struct.unpack("<I", d[16:20])[0], 3, "D10b version 在偏移 16")
eq(struct.unpack("<I", d[20:24])[0], 42, "D11 ino 在偏移 20")
eq(d[28], len(name), "D12 nsize 在偏移 28")
eq(d[29], 4, "D13 type 在偏移 29")

# ---------------------------------------------------------------- E. raw inode
data = b"hello flash"
ri = ju.build_raw_inode(ino=42, version=7, mode=0o100644, uid=0, gid=0,
                        isize=len(data), offset=0, csize=len(data),
                        dsize=len(data), compr=ju.JFFS2_COMPR_ZLIB, data=data)
eq(len(ri), ju.INODE_HDR_SIZE + len(data), "E1 inode 总长")
eq(ju.parse_common(ri).nodetype, ju.JFFS2_NODETYPE_INODE, "E2 类型")
ck(ju.check_inode_node_crc(ri), "E3 node_crc")
ck(ju.check_inode_data_crc(ri, 0, data), "E4 data_crc 覆盖数据")
ck(not ju.check_inode_data_crc(ri, 0, data + b"!"), "E5 数据变了 data_crc 失败")
e2 = bytearray(ri)
e2[ju.INODE_HDR_SIZE] ^= 0xFF      # 改数据首字节
ck(not ju.check_inode_data_crc(bytes(e2), 0, bytes(e2)[ju.INODE_HDR_SIZE:]),
   "E6 数据被改后与落盘的 data_crc 不符")
ck(ju.check_inode_node_crc(bytes(e2)), "E7 数据不在 node_crc 覆盖内（对偶）")
eq(struct.unpack("<I", ri[12:16])[0], 42, "E8 ino 在偏移 12")
eq(struct.unpack("<I", ri[16:20])[0], 7, "E9 version 在偏移 16")
eq(struct.unpack("<H", ri[24:26])[0], 0, "E10 uid 是 16 位")
eq(struct.unpack("<I", ri[20:24])[0], 0o100644, "E11 mode")
eq(ri[ju.INODE_HDR_SIZE - 12], ju.JFFS2_COMPR_ZLIB, "E12 compr 在倒数第 12 字节")
eq(struct.unpack("<I", ri[60:64])[0], ju.crc32(data, 0), "E13 data_crc 落位")

# ---------------------------------------------------------------- F. UBI 常量
eq(ju.UBI_EC_HDR_MAGIC, 0x55424923, "F1 EC 魔数 UBI#")
eq(ju.UBI_VID_HDR_MAGIC, 0x55424921, "F2 VID 魔数 UBI!")
eq(struct.pack(">I", ju.UBI_EC_HDR_MAGIC), b"UBI#", "F3 大端即 'UBI#'")
eq(struct.pack(">I", ju.UBI_VID_HDR_MAGIC), b"UBI!", "F4 大端即 'UBI!'")
eq(ju.UBI_CRC32_INIT, 0xFFFFFFFF, "F5 CRC 种子是 ~0 而不是 0")
eq(ju.UBI_VERSION, 1, "F6 UBI 版本")
eq(ju.EC_HDR_SIZE, 64, "F7 EC 头 64 字节")
eq(ju.VID_HDR_SIZE, 64, "F8 VID 头 64 字节")
eq(ju.UBI_LAYOUT_VOLUME_ID, 0x7FFFEFFF, "F9 layout 卷 id")
eq(ju.UBI_MAX_VOLUMES, 128, "F10 最多 128 个卷")
eq(ju.UBI_VOL_NAME_MAX, 127, "F11 卷名最长 127")
eq(ju.UBI_FM_MAX_START, 64, "F12 fastmap 只在前 64 个 PEB 里找")
eq(ju.UBI_FM_MAX_BLOCKS, 32, "F13 fastmap 最多 32 块")
eq(ju.UBI_VID_DYNAMIC, 1, "F14 dynamic")
eq(ju.UBI_VID_STATIC, 2, "F15 static")

# ---------------------------------------------------------------- G. EC 头
ecb = ju.pack_ec(ju.EcHeader(ec=1234, vid_hdr_offset=2048, data_offset=4096,
                             image_seq=0xAABBCCDD))
eq(len(ecb), 64, "G1 EC 头长度")
eq(struct.unpack(">I", ecb[0:4])[0], ju.UBI_EC_HDR_MAGIC, "G2 魔数大端")
eq(struct.unpack(">Q", ecb[8:16])[0], 1234, "G3 ec 是 64 位")
eq(struct.unpack(">I", ecb[16:20])[0], 2048, "G4 vid_hdr_offset")
eq(struct.unpack(">I", ecb[20:24])[0], 4096, "G5 data_offset")
eq(struct.unpack(">I", ecb[24:28])[0], 0xAABBCCDD, "G6 image_seq")
ck(ju.check_ec_crc(ecb), "G7 EC CRC 校验通过")
g2 = bytearray(ecb)
g2[9] ^= 0xFF
ck(not ju.check_ec_crc(bytes(g2)), "G8 改 ec 后失败")
g3 = bytearray(ecb)
g3[60] ^= 0xFF                     # 改 hdr_crc 自身
ck(not ju.check_ec_crc(bytes(g3)), "G9 CRC 不覆盖自己（改它就失败）")
g4 = bytearray(ecb)
g4[4] ^= 0xFF                      # 改 version
ck(not ju.check_ec_crc(bytes(g4)), "G10 version 在覆盖内")

# ---------------------------------------------------------------- H. VID 头
v = ju.VidHeader(vol_id=0, lnum=5, sqnum=99, vol_type=ju.UBI_VID_STATIC,
                 copy_flag=0, compat=0, data_size=4096, used_ebs=3,
                 data_pad=0, data_crc=0x12345678)
vb = ju.pack_vid(v)
eq(len(vb), 64, "H1 VID 头长度")
vp = ju.parse_vid(vb)
eq(vp.magic, ju.UBI_VID_HDR_MAGIC, "H2 魔数")
eq(vp.vol_type, ju.UBI_VID_STATIC, "H3 卷类型")
eq(vp.lnum, 5, "H4 lnum")
eq(vp.sqnum, 99, "H5 sqnum 是 64 位")
eq(vp.data_size, 4096, "H6 data_size")
eq(vp.used_ebs, 3, "H7 used_ebs")
eq(vp.data_crc, 0x12345678, "H8 data_crc")
ck(ju.check_vid_crc(vb), "H9 VID CRC")
h2 = bytearray(vb)
h2[12] ^= 0xFF                     # vol_id
ck(not ju.check_vid_crc(bytes(h2)), "H10 改 vol_id 后失败")
h3 = bytearray(vb)
h3[5] ^= 0xFF                      # vol_type
ck(not ju.check_vid_crc(bytes(h3)), "H11 vol_type 在覆盖内")

# ---------------------------------------------------------------- I. 选哪一块
old = ju.VidHeader(vol_id=1, lnum=5, sqnum=10, copy_flag=0)
new_nocopy = ju.VidHeader(vol_id=1, lnum=5, sqnum=20, copy_flag=0)
new_copy_ok = ju.VidHeader(vol_id=1, lnum=5, sqnum=20, copy_flag=1)
new_copy_bad = ju.VidHeader(vol_id=1, lnum=5, sqnum=20, copy_flag=1)
ck(ju.pick_peb(old, new_nocopy, False) is new_nocopy, "I1 非副本：直接选 sqnum 大的")
ck(ju.pick_peb(old, new_copy_ok, True) is new_copy_ok, "I2 副本且 CRC 正确：选新的")
ck(ju.pick_peb(old, new_copy_bad, False) is old, "I3 副本但 CRC 错：退回旧的")
ck(ju.pick_peb(old, new_copy_bad, True) is new_copy_bad,
   "I4 同一个副本 CRC 修好了就选它（对偶）")

if __name__ == "__main__":
    print("PASS=%d FAIL=%d" % (PASS, len(FAIL)))
    for f in FAIL:
        print("  FAIL:", f)
    sys.exit(1 if FAIL else 0)
