"""demo592 演示入口：认出一个 squashfs superblock 并推算各表位置。"""

import struct

import squashfs as sq


def main():
    sb = sq.Superblock(s_magic=sq.SQUASHFS_MAGIC, inodes=430, mkfs_time=1700000000,
                       block_size=131072, fragments=64, compression=sq.XZ_COMPRESSION,
                       block_log=17, flags=(1 << sq.FLAG_NO_FRAG), no_ids=1,
                       s_major=4, s_minor=0, root_inode=sq.mkinode(1, 0x10),
                       bytes_used=0x3A0000, id_table_start=0x380000,
                       xattr_id_table_start=0xFFFFFFFFFFFFFFFF,
                       inode_table_start=0x300000, directory_table_start=0x320000,
                       fragment_table_start=0x340000, lookup_table_start=0x360000)
    blob = sb.pack()
    p = sq.parse_superblock(blob)
    print("== squashfs superblock ==")
    print("  magic=%#x version=%d.%d compress=%d block=%d(log %d)"
          % (p.s_magic, p.s_major, p.s_minor, p.compression, p.block_size, p.block_log))
    print("  inodes=%d fragments=%d bytes_used=%#x" % (p.inodes, p.fragments, p.bytes_used))
    print("  root inode = <block %d, offset %d>"
          % (sq.inode_blk(p.root_inode), sq.inode_offset(p.root_inode)))
    print("  uncompressed data=%d xattr present=%s"
          % (p.flag(sq.FLAG_NOD), p.xattr_id_table_start != 0xFFFFFFFFFFFFFFFF))
    for name in ("inode_table_start", "directory_table_start",
                 "fragment_table_start", "lookup_table_start"):
        print("  %-24s -> %#x" % (name, getattr(p, name)))
    print("  fragment 索引块数 = %d（每项 %d 字节）"
          % (sq.fragment_indexes(p.fragments), 8))


if __name__ == "__main__":
    main()
