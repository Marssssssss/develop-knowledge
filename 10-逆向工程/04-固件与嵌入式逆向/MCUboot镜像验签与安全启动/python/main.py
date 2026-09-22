"""demo595 演示入口：造一份 MCUboot 镜像并跑一遍官方校验顺序。"""

import mcuboot as mb


def main():
    body = bytes(range(64))
    pub = b"\x04" + b"\x21" * 64
    kh = mb.keyhash(pub)
    hdr, blob = mb.build_image(
        body, flags=0, ver=mb.ImageVersion(2, 1, 0, 42),
        protected=[(mb.IMAGE_TLV_SEC_CNT, (3).to_bytes(4, "little"))],
        unprotected=[(mb.IMAGE_TLV_SHA256, b"\x00" * 32),
                     (mb.IMAGE_TLV_KEYHASH, kh)])
    # 用真实摘要回填（build_image 不知道摘要，故先占位再补）
    digest = mb.image_hash(blob, hdr)
    hdr, blob = mb.build_image(
        body, flags=0, ver=mb.ImageVersion(2, 1, 0, 42),
        protected=[(mb.IMAGE_TLV_SEC_CNT, (3).to_bytes(4, "little"))],
        unprotected=[(mb.IMAGE_TLV_SHA256, digest),
                     (mb.IMAGE_TLV_KEYHASH, kh)])

    print("== MCUboot 镜像头 ==")
    print("  magic=%#010x hdr_size=%d img_size=%d protect_tlv=%d"
          % (hdr.magic, hdr.hdr_size, hdr.img_size, hdr.protect_tlv_size))
    print("  version=%s flags=%#x load=%#x" % (hdr.ver, hdr.flags, hdr.load_addr))
    tlv = hdr.hdr_size + hdr.img_size
    i1 = mb.parse_tlv_info(blob, tlv)
    print("== TLV ==")
    print("  第一段 info magic=%#06x total=%d" % (i1["magic"], i1["total"]))
    for t, ln, v in mb.iter_tlvs(blob, tlv, i1["total"]):
        print("    type=%#04x len=%d" % (t, ln))
    i2 = mb.parse_tlv_info(blob, tlv + hdr.protect_tlv_size)
    print("  第二段 info magic=%#06x total=%d" % (i2["magic"], i2["total"]))
    for t, ln, v in mb.iter_tlvs(blob, tlv + hdr.protect_tlv_size, i2["total"]):
        print("    type=%#04x len=%d value=%s" % (t, ln, v[:8].hex()))
    print("  摘要 = %s" % digest.hex()[:32])
    print("  validate ->", mb.validate(blob))
    print("  trailer 偏移（slot 64K）:", mb.trailer_offsets(0x10000))


if __name__ == "__main__":
    main()
