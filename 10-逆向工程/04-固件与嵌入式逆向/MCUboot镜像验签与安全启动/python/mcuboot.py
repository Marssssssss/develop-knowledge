"""MCUboot 镜像头、TLV 清单与镜像尾（trailer）。

全部取自 mcu-tools/mcuboot@main：
  - `boot/bootutil/include/bootutil/image.h`：IMAGE_MAGIC / TLV 类型 / 标志位
  - `docs/design.md` §Image format：头结构、TLV info 结构、哈希覆盖范围
  - `boot/bootutil/src/image_validate.c`：
        TLV 区紧跟 `hdr_size + img_size`；
        PROT_INFO_MAGIC 之后若存在受保护 TLV，则其后还要有一个 INFO_MAGIC；
        校验顺序是 magic -> TLV info -> SHA256 TLV -> 摘要比对 -> 签名
  - `boot/bootutil/include/bootutil/bootutil.h`：`struct image_trailer`

MCUboot 的头是**小端**，与 U-Boot 的 legacy uImage（大端）正好相反。
"""

import hashlib
import struct

IMAGE_MAGIC = 0x96F3B83D
IMAGE_MAGIC_V1 = 0x96F3B83C
IMAGE_MAGIC_NONE = 0xFFFFFFFF
IMAGE_TLV_INFO_MAGIC = 0x6907
IMAGE_TLV_PROT_INFO_MAGIC = 0x6908

IMAGE_HEADER_SIZE = 32
IMAGE_HASH_LEN = 32

IMAGE_F_PIC = 0x00000001
IMAGE_F_ENCRYPTED_AES128 = 0x00000004
IMAGE_F_ENCRYPTED_AES256 = 0x00000008
IMAGE_F_NON_BOOTABLE = 0x00000010
IMAGE_F_RAM_LOAD = 0x00000020
IMAGE_F_ROM_FIXED = 0x00000100
IMAGE_F_COMPRESSED_LZMA1 = 0x00000200
IMAGE_F_COMPRESSED_LZMA2 = 0x00000400
IMAGE_F_COMPRESSED_ARM_THUMB_FLT = 0x00000800

IMAGE_TLV_KEYHASH = 0x01
IMAGE_TLV_PUBKEY = 0x02
IMAGE_TLV_SHA256 = 0x10
IMAGE_TLV_SHA384 = 0x11
IMAGE_TLV_SHA512 = 0x12
IMAGE_TLV_RSA2048_PSS = 0x20
IMAGE_TLV_ECDSA224 = 0x21
IMAGE_TLV_ECDSA_SIG = 0x22
IMAGE_TLV_RSA3072_PSS = 0x23
IMAGE_TLV_ED25519 = 0x24
IMAGE_TLV_SIG_PURE = 0x25
IMAGE_TLV_ENC_RSA2048 = 0x30
IMAGE_TLV_ENC_KW = 0x31
IMAGE_TLV_ENC_EC256 = 0x32
IMAGE_TLV_ENC_X25519 = 0x33
IMAGE_TLV_ENC_X25519_SHA512 = 0x34
IMAGE_TLV_DEPENDENCY = 0x40
IMAGE_TLV_SEC_CNT = 0x50
IMAGE_TLV_BOOT_RECORD = 0x60
IMAGE_TLV_DECOMP_SIZE = 0x70
IMAGE_TLV_DECOMP_SHA = 0x71
IMAGE_TLV_DECOMP_SIGNATURE = 0x72
IMAGE_TLV_COMP_DEC_SIZE = 0x73
IMAGE_TLV_UUID_VID = 0x74
IMAGE_TLV_UUID_CID = 0x75

# 未开启 TLV allow list 时允许出现在 unprotected 区的类型
ALLOWED_UNPROT_TLVS = (
    IMAGE_TLV_KEYHASH, IMAGE_TLV_PUBKEY,
    IMAGE_TLV_SHA256, IMAGE_TLV_SHA384, IMAGE_TLV_SHA512,
    IMAGE_TLV_RSA2048_PSS, IMAGE_TLV_ECDSA224, IMAGE_TLV_ECDSA_SIG,
    IMAGE_TLV_RSA3072_PSS, IMAGE_TLV_ED25519,
    IMAGE_TLV_ENC_RSA2048, IMAGE_TLV_ENC_KW, IMAGE_TLV_ENC_EC256,
    IMAGE_TLV_ENC_X25519,
)

BOOT_MAX_ALIGN = 8
BOOT_MAGIC_SZ = 16                 # union boot_img_magic_t 里 uint8_t val[16]
TRAILER_SIZE = BOOT_MAX_ALIGN * 3 + BOOT_MAGIC_SZ


class ImageVersion(object):
    def __init__(self, major=0, minor=0, revision=0, build_num=0):
        self.major = major
        self.minor = minor
        self.revision = revision
        self.build_num = build_num

    def pack(self):
        return struct.pack("<BBHI", self.major, self.minor, self.revision,
                           self.build_num)

    def __str__(self):
        return "%u.%u.%u+%u" % (self.major, self.minor, self.revision, self.build_num)


class ImageHeader(object):
    """struct image_header：32 字节，全小端。"""

    def __init__(self, load_addr=0, hdr_size=IMAGE_HEADER_SIZE,
                 protect_tlv_size=0, img_size=0, flags=0,
                 ver=None, magic=IMAGE_MAGIC):
        self.magic = magic
        self.load_addr = load_addr
        self.hdr_size = hdr_size
        self.protect_tlv_size = protect_tlv_size
        self.img_size = img_size
        self.flags = flags
        self.ver = ver or ImageVersion()

    def pack(self):
        return struct.pack("<IIHHII", self.magic, self.load_addr, self.hdr_size,
                           self.protect_tlv_size, self.img_size, self.flags) \
            + self.ver.pack() + struct.pack("<I", 0)


def parse_header(blob, off=0):
    magic, load, hs, pts, isz, fl = struct.unpack("<IIHHII", blob[off:off + 20])
    maj, mino, rev, build = struct.unpack("<BBHI", blob[off + 20:off + 28])
    return ImageHeader(load, hs, pts, isz, fl, ImageVersion(maj, mino, rev, build),
                       magic)


def check_magic(h):
    return h.magic == IMAGE_MAGIC


def parse_tlv_info(blob, off):
    magic, total = struct.unpack("<HH", blob[off:off + 4])
    return {"magic": magic, "total": total}


def iter_tlvs(blob, off, total):
    """TLV 区逐条走：每个条目 4 字节头（type,len），len 不含头。"""
    end = off + total
    pos = off + 4
    while pos + 4 <= end:
        t, ln = struct.unpack("<HH", blob[pos:pos + 4])
        yield t, ln, blob[pos + 4:pos + 4 + ln]
        pos += 4 + ln


def build_tlv_area(entries, magic=IMAGE_TLV_INFO_MAGIC):
    """entries: [(type, bytes)]。total 含 4 字节 info 头。"""
    body = b"".join(struct.pack("<HH", t, len(v)) + v for t, v in entries)
    return struct.pack("<HH", magic, len(body) + 4) + body


def image_hash(blob, hdr):
    """哈希 = SHA256(头 + 镜像体 [+ 受保护 TLV 区])。

    ih_protect_tlv_size 为 0 时只算前两段；否则把整段受保护 TLV 一起算进去。
    """
    start = 0
    end = hdr.hdr_size + hdr.img_size + hdr.protect_tlv_size
    return hashlib.sha256(blob[start:end]).digest()


def build_image(body, flags=0, ver=None, protected=(), unprotected=(),
                load_addr=0):
    """拼一份完整的 slot 内容：头 + 体 + 受保护 TLV + 普通 TLV。"""
    prot_area = b""
    if protected:
        prot_area = build_tlv_area(list(protected), IMAGE_TLV_PROT_INFO_MAGIC)
    unprot = build_tlv_area(list(unprotected), IMAGE_TLV_INFO_MAGIC)
    hdr = ImageHeader(load_addr=load_addr, hdr_size=IMAGE_HEADER_SIZE,
                      protect_tlv_size=len(prot_area), img_size=len(body),
                      flags=flags, ver=ver)
    return hdr, hdr.pack() + body + prot_area + unprot


def validate(blob):
    """按 docs/design.md 的顺序校验，返回 (ok, 失败原因)。"""
    if len(blob) < IMAGE_HEADER_SIZE:
        return False, "too short"
    h = parse_header(blob)
    if h.magic != IMAGE_MAGIC:
        return False, "bad magic"
    tlv_off = h.hdr_size + h.img_size
    if tlv_off + 4 > len(blob):
        return False, "no tlv info"
    info = parse_tlv_info(blob, tlv_off)
    if info["magic"] == IMAGE_TLV_PROT_INFO_MAGIC:
        if h.protect_tlv_size == 0:
            return False, "PROT_INFO but protect_tlv_size == 0"
        nxt = tlv_off + h.protect_tlv_size
        if nxt + 4 > len(blob):
            return False, "no second tlv info"
        info2 = parse_tlv_info(blob, nxt)
        if info2["magic"] != IMAGE_TLV_INFO_MAGIC:
            return False, "second tlv info has wrong magic"
        tlv_off = nxt
    elif info["magic"] != IMAGE_TLV_INFO_MAGIC:
        return False, "bad tlv info magic"
    sha = None
    for t, _ln, v in iter_tlvs(blob, tlv_off, info["total"]):
        if t == IMAGE_TLV_SHA256:
            sha = v
            break
    if sha is None:
        return False, "no sha256 tlv"
    if len(sha) != IMAGE_HASH_LEN:
        return False, "sha256 tlv has wrong length"
    if image_hash(blob, h) != sha:
        return False, "sha256 mismatch"
    return True, "ok"


def trailer_offsets(slot_size, align=BOOT_MAX_ALIGN, magic_sz=BOOT_MAGIC_SZ):
    """struct image_trailer 在 slot 末尾，字段之间用 align 补齐。"""
    swap_type = slot_size - (align * 3 + magic_sz)
    return {
        "swap_type": swap_type,
        "copy_done": swap_type + align,
        "image_ok": swap_type + align * 2,
        "magic": swap_type + align * 3,
    }


def keyhash(pubkey_bytes):
    """KEYHASH TLV = SHA256(公钥)（design.md：hash of the public key）。"""
    return hashlib.sha256(pubkey_bytes).digest()
