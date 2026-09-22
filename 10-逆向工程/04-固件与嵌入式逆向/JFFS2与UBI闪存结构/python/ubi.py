"""UBI 层：EC/VID 头与双份 PEB 仲裁（从 jffs2ubi.py 拆出）。

常量与结构见 README 的参考资料；CRC 用 fwcrc.crc32（seed 0xFFFFFFFF）。
"""

import struct

from fwcrc import crc32

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
