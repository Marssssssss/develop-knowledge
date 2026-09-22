"""demo592 自检：SquashFS superblock / 元数据块 / inode 编码 / 目录表。"""

import struct
import sys

import squashfs as sq

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


# ---------------------------------------------------------------- A. 常量
eq(sq.SQUASHFS_MAGIC, 0x73717368, "A1 magic 'hsqs'")
eq(struct.pack("<I", sq.SQUASHFS_MAGIC), b"hsqs", "A2 小端字节序即 'hsqs'")
eq(sq.SUPERBLOCK_SIZE, 96, "A3 superblock 96 字节")
eq(sq.SQUASHFS_METADATA_SIZE, 8192, "A4 元数据块 8K")
eq(sq.SQUASHFS_BLOCK_OFFSET, 2, "A5 块头 2 字节")
eq(sq.SQUASHFS_DIR_COUNT, 256, "A6 目录头 count 上限")
eq(sq.SQUASHFS_INVALID_FRAG, 0xFFFFFFFF, "A7 INVALID_FRAG")
eq(sq.SQUASHFS_FILE_MAX_SIZE, 1048576, "A8 块上限 1M")
eq(sq.SQUASHFS_MAX_DIR_TYPE, 7, "A9 目录项里 type 只有 7 种")
eq(sq.SQUASHFS_LREG_TYPE, 9, "A10 LREG=9")
eq(sq.ZLIB_COMPRESSION, 1, "A11 zlib id 1")
eq(sq.ZSTD_COMPRESSION, 6, "A12 zstd id 6")

# ---------------------------------------------------------------- B. flags
eq(sq.squashfs_bit(0x0001, 0), 1, "B1 NOI")
eq(sq.squashfs_bit(0x0002, 1), 1, "B2 NOD")
eq(sq.squashfs_bit(0x0008, 3), 1, "B3 NOF")
eq(sq.squashfs_bit(0x0010, 4), 1, "B4 NO_FRAG")
eq(sq.squashfs_bit(0x0400, 10), 1, "B5 COMP_OPT")
eq(sq.squashfs_bit(0x0001, 1), 0, "B6 取错位得 0（对偶）")
sb = sq.Superblock(flags=0x0402)
ck(sb.flag(sq.FLAG_NOD) == 1 and sb.flag(sq.FLAG_COMP_OPT) == 1, "B7 两个 flag 同时置位")
ck(sb.flag(sq.FLAG_NOI) == 0, "B8 未置位的 flag 为 0")

# ---------------------------------------------------------------- C. 元数据块头
eq(sq.compressed(0x0003), True, "C1 bit15 未置位 = 压缩")
eq(sq.compressed(0x8003), False, "C2 bit15 置位 = 未压缩")
eq(sq.compressed_size(0x0003), 3, "C3 压缩长度取低 15 位")
eq(sq.compressed_size(0x8003), 3, "C4 未压缩时长度仍是低 15 位")
eq(sq.compressed_size(0x8000), 0x8000, "C5 低 15 位为 0 时取 32768 而非 0")
eq(sq.compressed_size(0x0000), 0x8000, "C6 全 0 同样取 32768")
eq(sq.compressed_size_block(0x01000080), 0x80, "C7 数据块长用 bit24")
eq(sq.compressed_block(0x01000080), False, "C8 bit24 置位 = 未压缩")

# ---------------------------------------------------------------- D. inode 号编码
for blk, off in ((0, 0), (1, 5), (12345, 65535), (0xFFFFFF, 0xFFFF)):
    ref = sq.mkinode(blk, off)
    eq((sq.inode_blk(ref), sq.inode_offset(ref)), (blk, off),
       "D@%d/%d inode 号往返" % (blk, off))
eq(sq.mkinode(1, 0) >> 16, 1, "D5 块号在高位")
eq(sq.inode_offset(sq.mkinode(7, 0x1234)), 0x1234, "D6 偏移在低位")
ck(sq.mkinode(0xFFFFFF, 0) >> 16 == 0xFFFFFF, "D7 48 位够放块号")

# ---------------------------------------------------------------- E. fragment 表
eq(sq.fragment_bytes(1), 16, "E1 fragment_entry 16 字节")
eq(sq.fragment_bytes(512), 8192, "E2 512 个条目正好一个元数据块")
eq(sq.fragment_index(512), 1, "E3 索引号")
eq(sq.fragment_index_offset(512), 0, "E4 索引内偏移")
eq(sq.fragment_indexes(512), 1, "E5 索引项数")
eq(sq.fragment_indexes(513), 2, "E6 多一个条目就跨块")
eq(sq.fragment_index_bytes(513), 16, "E7 每项 8 字节")
eq(sq.fragment_index_offset(513), 16 % 8192, "E8 偏移取余")

# ---------------------------------------------------------------- F. lookup 表
eq(sq.lookup_bytes(1), 8, "F1 每项 8 字节")
eq(sq.lookup_blocks(1024), 1, "F2 1024 项 = 8K = 1 块")
eq(sq.lookup_block(1024), 1, "F3 块号")
eq(sq.lookup_block_offset(1024), 0, "F4 块内偏移")
eq(sq.lookup_blocks(1025), 2, "F5 跨块")
eq(sq.id_blocks(2048), 1, "F6 id 表每项 4 字节")
eq(sq.id_blocks(2049), 2, "F7 id 表跨块")

# ---------------------------------------------------------------- G. superblock
sb2 = sq.Superblock(s_magic=sq.SQUASHFS_MAGIC, inodes=1234, mkfs_time=1700000000,
                    block_size=131072, fragments=7, compression=sq.XZ_COMPRESSION,
                    block_log=17, flags=0x0000, no_ids=1, s_major=4, s_minor=0,
                    root_inode=sq.mkinode(2, 0x30), bytes_used=987654,
                    id_table_start=0x1000, xattr_id_table_start=0xFFFFFFFFFFFFFFFF,
                    inode_table_start=0x2000, directory_table_start=0x3000,
                    fragment_table_start=0x4000, lookup_table_start=0x5000)
blob = sb2.pack() + b"\x11" * 200
p = sq.parse_superblock(blob)
eq(p.s_magic, sq.SQUASHFS_MAGIC, "G1 magic 回读")
eq(p.inodes, 1234, "G2 inodes")
eq(p.block_size, 131072, "G3 block_size")
eq(p.block_log, 17, "G4 block_log 与 block_size 一致")
eq(1 << p.block_log, p.block_size, "G5 2^block_log = block_size")
eq(p.compression, sq.XZ_COMPRESSION, "G6 压缩 id")
eq(p.root_inode, sq.mkinode(2, 0x30), "G7 root inode 用 <block,offset>")
eq(p.bytes_used, 987654, "G8 bytes_used")
eq(p.xattr_id_table_start, 0xFFFFFFFFFFFFFFFF, "G9 无 xattr 时是全 f")
offs = sq.sb_field_offsets()
eq(offs["s_magic"], 0, "G10 magic 在偏移 0")
eq(offs["compression"], 20, "G11 compression 在偏移 20")
eq(offs["block_log"], 22, "G12 block_log 在偏移 22")
eq(offs["flags"], 24, "G13 flags 在偏移 24")
eq(offs["s_major"], 28, "G14 s_major 在偏移 28")
eq(offs["root_inode"], 32, "G15 root_inode 在偏移 32")
eq(offs["bytes_used"], 40, "G16 bytes_used 在偏移 40")
eq(offs["lookup_table_start"], 88, "G17 lookup_table_start 在偏移 88")
eq(struct.unpack("<I", blob[0:4])[0], sq.SQUASHFS_MAGIC, "G18 裸读 magic")
eq(struct.unpack("<H", blob[28:30])[0], 4, "G19 裸读 major")
eq(sq.parse_superblock(b"\x00" * 10), None, "G20 长度不足返回 None")
bad = bytearray(blob)
bad[0] = 0x00
ck(sq.parse_superblock(bytes(bad)).s_magic != sq.SQUASHFS_MAGIC, "G21 改首字节破坏 magic")

# ---------------------------------------------------------------- H. 元数据块流
# 同一张表里的元数据块是**首尾相接**的，块之间不补对齐：
# hdr(2)+data(n) 之后紧跟下一个 hdr(2)
tbl = struct.pack("<H", 0x8004) + b"abcd"          # 未压缩，长 4
tbl += struct.pack("<H", 0x0003) + b"xyz"          # 压缩，长 3
tbl += struct.pack("<H", 0x8001) + b"Q" + b"\0" * 3
blocks = sq.walk_metadata(tbl, 0, 3)
eq([b["compressed"] for b in blocks], [False, True, False], "H1 三个块头的压缩位")
eq([b["size"] for b in blocks], [4, 3, 1], "H2 三个块长")
eq([b["data_off"] for b in blocks], [2, 8, 13], "H3 数据起点含 2 字节头")
eq(tbl[blocks[0]["data_off"]:blocks[0]["data_off"] + 4], b"abcd", "H4 第一块数据")

# ---------------------------------------------------------------- I. 目录
eq(sq.dir_count(0), 1, "I1 count 存的是 n-1")
eq(sq.dir_count(255), 256, "I2 上限 256")
ck(sq.dir_count_ok(255), "I3 255 合法")
ck(not sq.dir_count_ok(256), "I4 256 会让 dir_count=257 越界")
ck(sq.dir_count_ok(0), "I5 0 合法（对偶）")

# ---------------------------------------------------------------- J. 文件块表
eq(sq.datablock_count(0, 17, False), 0, "J1 空文件 0 块")
eq(sq.datablock_count(1, 17, False), 1, "J2 1 字节也要一整块")
eq(sq.datablock_count(131072, 17, False), 1, "J3 正好一块")
eq(sq.datablock_count(131073, 17, False), 2, "J4 多一字节多一块")
eq(sq.datablock_count(131073, 17, True), 1, "J5 尾部进 fragment 就少一块（对偶）")
eq(sq.datablock_count(131072, 17, True), 1, "J6 整除时不受 fragment 影响")
eq(len(sq.reg_inode_blocks([0x2000, 0x2100], 131073, 17)), 2, "J7 块表与长度对齐")
eq(sq.reg_inode_blocks([0x2000, 0x2100], 131073, 17)[1][1], 1, "J8 末块只 1 字节")

if __name__ == "__main__":
    print("PASS=%d FAIL=%d" % (PASS, len(FAIL)))
    for f in FAIL:
        print("  FAIL:", f)
    sys.exit(1 if FAIL else 0)
