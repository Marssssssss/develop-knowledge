"""demo593 演示入口：扫一串 JFFS2 结点，再看 UBI 的 EC/VID 头。"""

import jffs2ubi as ju


def main():
    data = b"\xde\xad\xbe\xef"
    stream = b""
    stream += ju.build_dirent(1, 2, 42, 1700000000, 3, 4, b"etc")
    stream += ju.build_raw_inode(42, 5, 0o100644, 0, 0, len(data), 0,
                                 len(data), len(data), ju.JFFS2_COMPR_ZLIB, data)

    print("== JFFS2 结点流 ==")
    off = 0
    while off + ju.UNKNOWN_NODE_SIZE <= len(stream):
        n = ju.parse_common(stream, off)
        kind = "INODE" if n.nodetype == ju.JFFS2_NODETYPE_INODE else \
            ("DIRENT" if n.nodetype == ju.JFFS2_NODETYPE_DIRENT else "?")
        print("  off=%-4d type=%#06x %-6s totlen=%-4d hdr_crc=%s"
              % (off, n.nodetype, kind, n.totlen, ju.check_hdr_crc(n)))
        off += n.totlen

    print("== UBI EC/VID ==")
    ec = ju.pack_ec(ju.EcHeader(ec=7, vid_hdr_offset=2048, data_offset=4096,
                                image_seq=0x11223344))
    e = ju.parse_ec(ec)
    print("  EC magic=%#x ec=%d vid_off=%d data_off=%d crc_ok=%s"
          % (e.magic, e.ec, e.vid_hdr_offset, e.data_offset, ju.check_ec_crc(ec)))
    vid = ju.pack_vid(ju.VidHeader(vol_id=0, lnum=3, sqnum=11,
                                   vol_type=ju.UBI_VID_DYNAMIC))
    v = ju.parse_vid(vid)
    print("  VID magic=%#x vol_id=%d lnum=%d sqnum=%d crc_ok=%s"
          % (v.magic, v.vol_id, v.lnum, v.sqnum, ju.check_vid_crc(vid)))
    print("  layout volume id = %#x" % ju.UBI_LAYOUT_VOLUME_ID)


if __name__ == "__main__":
    main()
