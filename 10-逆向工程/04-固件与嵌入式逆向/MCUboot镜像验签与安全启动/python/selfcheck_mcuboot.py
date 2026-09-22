"""demo595 自检：MCUboot 头 / TLV / 哈希覆盖 / trailer。"""

import hashlib
import struct
import sys

import mcuboot as mb

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


def make(protected=(), unprotected_extra=(), body=b"\x90" * 128, flags=0):
    ver = mb.ImageVersion(1, 2, 3, 4)
    hdr, blob_no_hash = mb.build_image(
        body, flags=flags, ver=ver, protected=protected, unprotected=())
    h = mb.image_hash(blob_no_hash, hdr)
    ent = [(mb.IMAGE_TLV_SHA256, h)] + list(unprotected_extra)
    _, full = mb.build_image(body, flags=flags, ver=ver, protected=protected,
                             unprotected=ent)
    return hdr, full


# ---------------------------------------------------------------- A. 常量
eq(mb.IMAGE_MAGIC, 0x96F3B83D, "A1 IMAGE_MAGIC")
eq(mb.IMAGE_MAGIC_V1, 0x96F3B83C, "A2 V1 魔数只差 1")
eq(mb.IMAGE_MAGIC_NONE, 0xFFFFFFFF, "A3 擦除态")
eq(mb.IMAGE_TLV_INFO_MAGIC, 0x6907, "A4 TLV info 魔数")
eq(mb.IMAGE_TLV_PROT_INFO_MAGIC, 0x6908, "A5 受保护 TLV info 魔数")
eq(mb.IMAGE_HEADER_SIZE, 32, "A6 头 32 字节")
eq(mb.IMAGE_HASH_LEN, 32, "A7 SHA256 摘要 32 字节")
eq(mb.IMAGE_TLV_KEYHASH, 0x01, "A8 KEYHASH")
eq(mb.IMAGE_TLV_SHA256, 0x10, "A9 SHA256 TLV")
eq(mb.IMAGE_TLV_RSA2048_PSS, 0x20, "A10 RSA2048-PSS")
eq(mb.IMAGE_TLV_ECDSA_SIG, 0x22, "A11 ECDSA")
eq(mb.IMAGE_TLV_ED25519, 0x24, "A12 ED25519")
eq(mb.IMAGE_TLV_SEC_CNT, 0x50, "A13 安全计数器")
eq(mb.IMAGE_TLV_DECOMP_SHA, 0x71, "A14 解压后摘要")
eq(mb.IMAGE_F_RAM_LOAD, 0x20, "A15 RAM_LOAD 标志")
eq(mb.IMAGE_F_ENCRYPTED_AES256, 0x08, "A16 AES256 加密标志")
eq(mb.IMAGE_F_NON_BOOTABLE, 0x10, "A17 不可启动")
eq(mb.BOOT_MAGIC_SZ, 16, "A18 trailer 魔数 16 字节")
eq(mb.BOOT_MAX_ALIGN, 8, "A19 默认对齐 8")

# ---------------------------------------------------------------- B. 头布局
h = mb.ImageHeader(load_addr=0x08020000, hdr_size=32, protect_tlv_size=0,
                   img_size=128, flags=mb.IMAGE_F_RAM_LOAD,
                   ver=mb.ImageVersion(1, 2, 3, 4))
raw = h.pack()
eq(len(raw), 32, "B1 头长 32")
eq(struct.unpack("<I", raw[0:4])[0], mb.IMAGE_MAGIC, "B2 magic 小端")
eq(struct.unpack("<I", raw[4:8])[0], 0x08020000, "B3 load_addr 在偏移 4")
eq(struct.unpack("<H", raw[8:10])[0], 32, "B4 hdr_size 是 16 位")
eq(struct.unpack("<H", raw[10:12])[0], 0, "B5 protect_tlv_size 是 16 位")
eq(struct.unpack("<I", raw[12:16])[0], 128, "B6 img_size 在偏移 12")
eq(struct.unpack("<I", raw[16:20])[0], mb.IMAGE_F_RAM_LOAD, "B7 flags 在偏移 16")
eq(raw[20], 1, "B8 major")
eq(raw[21], 2, "B9 minor")
eq(struct.unpack("<H", raw[22:24])[0], 3, "B10 revision 是 16 位")
eq(struct.unpack("<I", raw[24:28])[0], 4, "B11 build_num 是 32 位")
eq(struct.unpack("<I", raw[28:32])[0], 0, "B12 _pad1")
ck(raw[0:4] == struct.pack("<I", mb.IMAGE_MAGIC), "B13 小端而非大端")
p = mb.parse_header(raw)
eq(p.img_size, 128, "B14 解析回 img_size")
eq(str(p.ver), "1.2.3+4", "B15 版本字符串")
ck(mb.check_magic(p), "B16 magic 校验")

# ---------------------------------------------------------------- C. 无保护 TLV 的镜像
hdr, full = make()
eq(len(full), hdr.hdr_size + hdr.img_size + 4 + 4 + 32, "C1 布局长度")
info = mb.parse_tlv_info(full, hdr.hdr_size + hdr.img_size)
eq(info["magic"], mb.IMAGE_TLV_INFO_MAGIC, "C2 TLV info 落在 hdr+img 之后")
eq(info["total"], 40, "C3 total 含 info 头自身")
tlvs = list(mb.iter_tlvs(full, hdr.hdr_size + hdr.img_size, info["total"]))
eq([t for t, _l, _v in tlvs], [mb.IMAGE_TLV_SHA256], "C4 只有一个 SHA256 TLV")
eq(tlvs[0][1], 32, "C5 len 不含 4 字节头")
eq(len(tlvs[0][2]), 32, "C6 值长 32")
ok, why = mb.validate(full)
ck(ok, "C7 校验通过: %s" % why)
eq(mb.image_hash(full, hdr), tlvs[0][2], "C8 摘要与 TLV 一致")

# ---------------------------------------------------------------- D. 受保护 TLV
pub = b"\x04" + b"\x11" * 64
kh = mb.keyhash(pub)
eq(len(kh), 32, "D1 KEYHASH = SHA256(公钥)")
eq(kh, hashlib.sha256(pub).digest(), "D2 与标准库一致")
hdr2, full2 = make(protected=[(mb.IMAGE_TLV_SHA256, b"\x00" * 32)],
                   unprotected_extra=[(mb.IMAGE_TLV_KEYHASH, kh)])
eq(hdr2.protect_tlv_size, 40, "D3 protect_tlv_size = 受保护区长度")
i1 = mb.parse_tlv_info(full2, hdr2.hdr_size + hdr2.img_size)
eq(i1["magic"], mb.IMAGE_TLV_PROT_INFO_MAGIC, "D4 先出现受保护 info")
i2 = mb.parse_tlv_info(full2, hdr2.hdr_size + hdr2.img_size + hdr2.protect_tlv_size)
eq(i2["magic"], mb.IMAGE_TLV_INFO_MAGIC, "D5 其后紧跟普通 info")
ok2, why2 = mb.validate(full2)
ck(ok2, "D6 受保护 TLV 镜像校验通过: %s" % why2)
# 受保护区参与哈希：改它的一个字节会让摘要失配
bad = bytearray(full2)
bad[hdr2.hdr_size + hdr2.img_size + 8] ^= 0xFF
ok3, why3 = mb.validate(bytes(bad))
ck(not ok3 and "sha256" in why3, "D7 改受保护区导致摘要失配")
# 对偶：改普通区（签名/KEYHASH）不影响"摘要"，但会改变镜像内容
bad2 = bytearray(full2)
n = len(full2)
bad2[n - 8] ^= 0xFF
ok4, _ = mb.validate(bytes(bad2))
ck(ok4, "D8 改普通 TLV 的值不影响摘要（它不在哈希覆盖内）")

# ---------------------------------------------------------------- E. 负例
hdr5, full5 = make()
b = bytearray(full5)
b[0] ^= 0xFF
ck(not mb.validate(bytes(b))[0], "E1 改 magic 失败")
b = bytearray(full5)
body_off = hdr5.hdr_size
b[body_off] ^= 0xFF
ck(not mb.validate(bytes(b))[0], "E2 改镜像体失败")
b = bytearray(full5)
tinfo = hdr5.hdr_size + hdr5.img_size
b[tinfo] = 0x00
b[tinfo + 1] = 0x00
ck(not mb.validate(bytes(b))[0], "E3 TLV info 魔数错失败")
b = bytearray(full5)
# 把 SHA256 TLV 的 type 改成别的，等于没有摘要
b[tinfo + 4] = 0x11
ck(not mb.validate(bytes(b))[0], "E4 没有 SHA256 TLV 失败")
ck(not mb.validate(b"\x00" * 8)[0], "E5 太短失败")

# ---------------------------------------------------------------- F. TLV allow list
ck(mb.IMAGE_TLV_SHA256 in mb.ALLOWED_UNPROT_TLVS, "F1 SHA256 允许在非保护区")
ck(mb.IMAGE_TLV_KEYHASH in mb.ALLOWED_UNPROT_TLVS, "F2 KEYHASH 允许")
ck(mb.IMAGE_TLV_SEC_CNT not in mb.ALLOWED_UNPROT_TLVS, "F3 安全计数器必须在保护区")
area = mb.build_tlv_area([(mb.IMAGE_TLV_SHA256, b"\x11" * 32),
                          (mb.IMAGE_TLV_KEYHASH, b"\x22" * 32)])
eq(len(area), 4 + 4 + 32 + 4 + 32, "F4 TLV 区长度")
eq(struct.unpack("<HH", area[0:4])[1], len(area), "F5 total 覆盖整段")
eq(struct.unpack("<HH", area[4:8])[0], mb.IMAGE_TLV_SHA256, "F6 第一条 type")
eq(struct.unpack("<HH", area[4:8])[1], 32, "F7 第一条 len")
eq(struct.unpack("<HH", area[40:44])[0], mb.IMAGE_TLV_KEYHASH, "F8 第二条 type")
eq(mb.build_tlv_area([], magic=mb.IMAGE_TLV_PROT_INFO_MAGIC),
   struct.pack("<HH", mb.IMAGE_TLV_PROT_INFO_MAGIC, 4), "F9 空 TLV 区仍有 4 字节头")
ent = list(mb.iter_tlvs(area, 0, len(area)))
eq(len(ent), 2, "F10 迭代出两条")
eq(ent[1][2], b"\x22" * 32, "F11 第二条的值")

# ---------------------------------------------------------------- G. trailer
offs = mb.trailer_offsets(0x20000)
eq(offs["swap_type"], 0x20000 - 40, "G1 swap_type 偏移")
eq(offs["copy_done"], 0x20000 - 32, "G2 copy_done 间隔一个 align")
eq(offs["image_ok"], 0x20000 - 24, "G3 image_ok 再隔一个 align")
eq(offs["magic"], 0x20000 - 16, "G4 magic 在最后 16 字节")
eq(mb.TRAILER_SIZE, 40, "G5 trailer 总长 40")
offs2 = mb.trailer_offsets(0x1000, align=4, magic_sz=16)
eq(offs2["magic"], 0x1000 - 16, "G6 magic 永远贴着末尾")
eq(offs2["swap_type"], 0x1000 - 28, "G7 align=4 时 trailer 更短")
ck(offs["magic"] > offs["image_ok"] > offs["copy_done"] > offs["swap_type"],
   "G8 四段顺序固定")

if __name__ == "__main__":
    print("PASS=%d FAIL=%d" % (PASS, len(FAIL)))
    for f in FAIL:
        print("  FAIL:", f)
    sys.exit(1 if FAIL else 0)
