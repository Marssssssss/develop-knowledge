"""U-Boot legacy uImage 头。

字段与常量全部取自 u-boot@master 的 `include/image.h`：

    #define IH_MAGIC  0x27051956
    #define IH_NMLEN  32
    struct legacy_img_hdr {
        uint32_t ih_magic; uint32_t ih_hcrc; uint32_t ih_time;
        uint32_t ih_size;  uint32_t ih_load; uint32_t ih_ep;
        uint32_t ih_dcrc;  uint8_t  ih_os;   uint8_t  ih_arch;
        uint8_t  ih_type;  uint8_t  ih_comp; uint8_t  ih_name[32];
    };

注释写明 **all data in network byte order (bigendian)**，所以解析一律用 `>I`。
CRC 语义取自 `boot/image.c` 与 `tools/default_image.c`：
    hcrc = crc32(0, header[:64] 且 hcrc 字段先清零)
    dcrc = crc32(0, payload)
两处都是 seed 0、不做首尾取反的 Linux `crc32()`（见 `include/linux/crc32.h`
的 "does *not* invert the CRC at the beginning or end"）。
"""

import struct
import zlib

IH_MAGIC = 0x27051956
IH_NMLEN = 32
LEGACY_HDR_SIZE = 64

# include/image.h：IH_OS_* 从 0 起顺序枚举
IH_OS = {
    "invalid": 0, "openbsd": 1, "netbsd": 2, "freebsd": 3, "4_4bsd": 4,
    "linux": 5, "svr4": 6, "esix": 7, "solaris": 8, "irix": 9, "sco": 10,
    "dell": 11, "ncr": 12, "lynxos": 13, "vxworks": 14, "psos": 15, "qnx": 16,
    "u_boot": 17, "rtems": 18, "artos": 19, "unity": 20, "integrity": 21,
    "ose": 22, "plan9": 23, "openrtos": 24, "arm_trusted_firmware": 25,
    "tee": 26, "opensbi": 27, "efi": 28, "elf": 29,
}

IH_ARCH = {
    "invalid": 0, "alpha": 1, "arm": 2, "i386": 3, "ia64": 4, "mips": 5,
    "mips64": 6, "ppc": 7, "s390": 8, "sh": 9, "sparc": 10, "sparc64": 11,
    "m68k": 12, "microblaze": 14, "nios2": 15, "blackfin": 16, "avr32": 17,
    "sh64": 18, "sandbox": 19, "nds32": 20, "arc": 21, "x86_64": 22,
    "xtensa": 23, "riscv": 24, "mips32": 25,
}

IH_TYPE = {
    "invalid": 0, "standalone": 1, "kernel": 2, "ramdisk": 3, "multi": 4,
    "firmware": 5, "script": 6, "filesystem": 7, "flatdt": 8,
    "kwbimage": 9, "imximage": 10, "ublimage": 11, "omapimage": 12,
    "aisimage": 13, "kernel_noload": 14, "pblimage": 15, "mxsimage": 16,
    "gpimage": 17, "atmelimage": 18, "socfpgaimage": 19, "x86_setup": 20,
    "lpc32xximage": 21, "loadable": 22, "rkimage": 23, "rksd": 24,
    "rkspi": 25, "zynqimage": 26, "zynqmpimage": 27, "zynqmpbif": 28,
    "fpga": 29, "vybridimage": 30, "tee": 31, "firmware_ivt": 32,
    "pmmc": 33, "stm32image": 34, "socfpgaimage_v1": 35, "mtkimage": 36,
    "imx8mimage": 37, "imx8image": 38, "copro": 39, "sunxi_egon": 40,
    "sunxi_toc0": 41, "fdt_legacy": 42, "renesas_spkg": 43,
    "starfive_spl": 44, "tfa_bl31": 45, "stm32image_v2": 46, "amlimage": 47,
}

IH_COMP = {
    "none": 0, "gzip": 1, "bzip2": 2, "lzma": 3, "lzo": 4, "lz4": 5, "zstd": 6,
}


def _make_table():
    tbl = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ (0xEDB88320 if (c & 1) else 0)
        tbl.append(c)
    return tbl


_CRC_TABLE = _make_table()


def crc32(data, seed=0):
    """Linux crc32()：LSB-first、多项式 0xEDB88320、寄存器初值 = seed、
    首尾**都不**取反（`include/linux/crc32.h` 明说 "does not invert"）。"""
    crc = seed & 0xFFFFFFFF
    for b in data:
        crc = _CRC_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc & 0xFFFFFFFF


class LegacyHeader(object):
    def __init__(self, magic=IH_MAGIC, hcrc=0, time=0, size=0, load=0, ep=0,
                 dcrc=0, os=0, arch=0, type=0, comp=0, name=b""):
        self.magic = magic
        self.hcrc = hcrc
        self.time = time
        self.size = size
        self.load = load
        self.ep = ep
        self.dcrc = dcrc
        self.os = os
        self.arch = arch
        self.type = type
        self.comp = comp
        self.name = name

    def pack(self):
        nm = self.name[:IH_NMLEN]
        nm = nm + b"\0" * (IH_NMLEN - len(nm))
        return struct.pack(
            ">7I4B32s",
            self.magic, self.hcrc, self.time, self.size, self.load, self.ep,
            self.dcrc, self.os, self.arch, self.type, self.comp, nm,
        )

    def with_hcrc_zeroed(self):
        h = LegacyHeader(**dict(self.__dict__))
        h.hcrc = 0
        return h

    def seal(self, payload):
        """按 mkimage 的顺序填 dcrc 与 hcrc。"""
        self.size = len(payload)
        self.dcrc = crc32(payload)
        self.hcrc = crc32(self.with_hcrc_zeroed().pack())
        return self


def parse_legacy(blob, offset=0):
    hdr = blob[offset:offset + LEGACY_HDR_SIZE]
    if len(hdr) < LEGACY_HDR_SIZE:
        return None
    (magic, hcrc, time, size, load, ep, dcrc, os_, arch, type_, comp) = struct.unpack(
        ">7I4B", hdr[:32]
    )
    return LegacyHeader(magic, hcrc, time, size, load, ep, dcrc, os_, arch, type_,
                        comp, hdr[32:64])


def check_hcrc(h):
    return crc32(h.with_hcrc_zeroed().pack()) == h.hcrc


def check_dcrc(h, payload):
    return crc32(payload) == h.dcrc


def check_magic(h):
    return h.magic == IH_MAGIC


def data_offset(offset=0):
    """image_get_data()：payload 起点 = 头地址 + 头长度。"""
    return offset + LEGACY_HDR_SIZE


def image_size(h):
    """image_get_image_size() = size + 头长度。"""
    return h.size + LEGACY_HDR_SIZE


def multi_sizes(payload):
    """多文件镜像：payload 开头是一串 be32 长度，以 0 结束。"""
    out = []
    while True:
        (v,) = struct.unpack(">I", payload[len(out) * 4:len(out) * 4 + 4])
        if v == 0:
            return out
        out.append(v)


def build_multi(parts):
    """按 tools/mkimage 的 multi 布局拼 payload。"""
    table = b"".join(struct.pack(">I", len(p)) for p in parts) + struct.pack(">I", 0)
    body = b"".join(parts)
    return table + body


def multi_part_offsets(parts):
    """每个子镜像在整个镜像文件里的偏移。"""
    base = LEGACY_HDR_SIZE + (len(parts) + 1) * 4
    offs = []
    cur = base
    for p in parts:
        offs.append(cur)
        cur += len(p)
    return offs
