"""iOS 砸壳：Mach-O 的 LC_ENCRYPTION_INFO(_64) 语义与"从内存 dump 回文件"的全过程。

结构体与常量全部取自 xnu 源码 EXTERNAL_HEADERS/mach-o/loader.h：
  - mach_header_64 / segment_command_64 / encryption_info_command(_64)
  - MH_MAGIC_64 / MH_CIGAM_64 / LC_SEGMENT_64 / LC_ENCRYPTION_INFO(_64)
  - MH_DYLIB_IN_CACHE（在 dyld 共享缓存里的 dylib）
参考：https://github.com/apple-oss-distributions/xnu/blob/main/EXTERNAL_HEADERS/mach-o/loader.h
"""

import struct

# ---------- 常量（loader.h） ----------

MH_MAGIC_64 = 0xFEEDFACF          # 64 位 mach header 的 magic
MH_CIGAM_64 = 0xCFFAEDFE          # NXSwapInt(MH_MAGIC_64)：字节序被交换过

LC_SYMTAB = 0x02
LC_SEGMENT_64 = 0x19
LC_UUID = 0x1B
LC_ENCRYPTION_INFO = 0x21
LC_ENCRYPTION_INFO_64 = 0x2C

MH_EXECUTE = 0x2
MH_DYLIB = 0x6
MH_PIE = 0x200000
MH_DYLIB_IN_CACHE = 0x80000000    # 只用于 dylib：该库在 dyld 共享缓存里

# ---------- 结构体大小（按 loader.h 字段推算） ----------

HEADER_64_SIZE = 32               # 8 个 uint32
SEGMENT_64_SIZE = 72              # 4+4+16+8*4+4*4
ENCRYPTION_INFO_SIZE = 20         # 5 个 uint32
ENCRYPTION_INFO_64_SIZE = 24      # 6 个 uint32（pad 使大小为 8 的倍数）

# encryption_info_command_64 内 cryptid 的偏移：cmd + cmdsize + cryptoff + cryptsize
CRYPTID_OFFSET = 16


class MachOError(Exception):
    pass


class Segment(object):

    def __init__(self, name, vmaddr, vmsize, fileoff, filesize, cmd_off):
        self.name = name
        self.vmaddr = vmaddr
        self.vmsize = vmsize
        self.fileoff = fileoff
        self.filesize = filesize
        self.cmd_off = cmd_off

    def contains_fileoff(self, off):
        return self.fileoff <= off < self.fileoff + self.filesize

    def fileoff_to_vmaddr(self, off):
        if not self.contains_fileoff(off):
            raise MachOError("文件偏移 0x%x 不在段 %s 内" % (off, self.name))
        return self.vmaddr + (off - self.fileoff)


class MachO(object):
    """最小 Mach-O 解析：header + load commands + 段表 + 加密信息。"""

    def __init__(self, blob):
        self.blob = bytearray(blob)
        self._parse_header()
        self.segments = []
        self.crypt = None
        self.crypt_cmd_off = None
        self._parse_load_commands()

    def _u32(self, off):
        return struct.unpack_from("<I", self.blob, off)[0]

    def _u64(self, off):
        return struct.unpack_from("<Q", self.blob, off)[0]

    def _parse_header(self):
        magic = self._u32(0)
        if magic == MH_MAGIC_64:
            self.swapped = False
        elif magic == MH_CIGAM_64:
            self.swapped = True          # 字节序被交换过，本模型不再做完整交换
        else:
            raise MachOError("magic 0x%08x 不是 64 位 Mach-O" % magic)
        self.cputype = self._u32(4)
        self.cpusubtype = self._u32(8)
        self.filetype = self._u32(12)
        self.ncmds = self._u32(16)
        self.sizeofcmds = self._u32(20)
        self.flags = self._u32(24)
        self.reserved = self._u32(28)

    def _parse_load_commands(self):
        off = HEADER_64_SIZE
        for _ in range(self.ncmds):
            cmd = self._u32(off)
            cmdsize = self._u32(off + 4)
            if cmdsize < 8:
                raise MachOError("cmdsize 非法")
            if cmd == LC_SEGMENT_64:
                name = bytes(self.blob[off + 8:off + 24]).split(b"\x00")[0].decode("ascii")
                vmaddr = self._u64(off + 24)
                vmsize = self._u64(off + 32)
                fileoff = self._u64(off + 40)
                filesize = self._u64(off + 48)
                self.segments.append(Segment(name, vmaddr, vmsize, fileoff, filesize, off))
            elif cmd in (LC_ENCRYPTION_INFO, LC_ENCRYPTION_INFO_64):
                self.crypt = {
                    "cmd": cmd,
                    "cryptoff": self._u32(off + 8),
                    "cryptsize": self._u32(off + 12),
                    "cryptid": self._u32(off + 16),
                }
                self.crypt_cmd_off = off
            off += cmdsize

    # --- 查询 ---
    def segment_named(self, name):
        for s in self.segments:
            if s.name == name:
                return s
        return None

    def is_encrypted(self):
        """loader.h 原话：cryptid 为 0 表示尚未加密（即已被砸壳或本就未加密）。"""
        return bool(self.crypt and self.crypt["cryptid"] != 0)

    def in_dyld_shared_cache(self):
        return bool(self.flags & MH_DYLIB_IN_CACHE)

    def dump_plan(self):
        """算出"该从内存的哪个地址、拷多少字节、写回文件的哪个偏移"。

        cryptoff 是**文件偏移**；进程里解密后的数据位于
        vmaddr + (cryptoff - segment.fileoff)。这两者的换算是砸壳最容易写错的地方。
        """
        if not self.crypt:
            raise MachOError("没有 LC_ENCRYPTION_INFO(_64)")
        cryptoff = self.crypt["cryptoff"]
        cryptsize = self.crypt["cryptsize"]
        seg = None
        for s in self.segments:
            if s.contains_fileoff(cryptoff):
                seg = s
                break
        if seg is None:
            raise MachOError("cryptoff 0x%x 不落在任何段内" % cryptoff)
        return {"segment": seg.name, "file_offset": cryptoff, "size": cryptsize,
                "vmaddr": seg.fileoff_to_vmaddr(cryptoff)}

    # --- 砸壳动作 ---
    def apply_dump(self, memory, image_vm_base, plan=None):
        """把进程内存里已解密的字节写回文件对应区间。"""
        plan = plan or self.dump_plan()
        start_mem = plan["vmaddr"] - image_vm_base
        for i in range(plan["size"]):
            self.blob[plan["file_offset"] + i] = memory[start_mem + i]
        return plan

    def patch_cryptid(self, value=0):
        """把 cryptid 置 0，让内核/加载器不再尝试解密。"""
        if self.crypt_cmd_off is None:
            raise MachOError("没有加密命令可改")
        struct.pack_into("<I", self.blob, self.crypt_cmd_off + CRYPTID_OFFSET, value)
        self.crypt["cryptid"] = value

    def bytes(self):
        return bytes(self.blob)


def page_aligned_range(cryptoff, cryptsize, page_size):
    """加密区间落在若干页内，dump 必须覆盖**整个页**。

    page_size 由调用方给出（不同平台/机型不同），本函数不假定具体数值。
    """
    start = cryptoff - (cryptoff % page_size)
    end = cryptoff + cryptsize
    end = end + ((-end) % page_size)
    return start, end - start


def build_sample(page_size=0x1000, encrypted=True):
    """构造一个带 __TEXT 段与 LC_ENCRYPTION_INFO_64 的最小 Mach-O。"""
    text_off = 0x4000
    text_size = 0x2000
    cryptsize = 0x1000
    cryptoff = text_off + 0x1000               # __TEXT 的后半段被加密
    blob = bytearray(0x8000)
    # 段内容：前半段明文标记，后半段"密文"标记
    for i in range(text_size):
        blob[text_off + i] = 0x41 if i < 0x1000 else 0xEE
    struct.pack_into("<I", blob, 0, MH_MAGIC_64)
    struct.pack_into("<I", blob, 12, MH_EXECUTE)
    struct.pack_into("<I", blob, 16, 2)        # ncmds
    struct.pack_into("<I", blob, 20, SEGMENT_64_SIZE + ENCRYPTION_INFO_64_SIZE)
    struct.pack_into("<I", blob, 24, MH_PIE)

    off = HEADER_64_SIZE
    struct.pack_into("<II", blob, off, LC_SEGMENT_64, SEGMENT_64_SIZE)
    struct.pack_into("<16s", blob, off + 8, b"__TEXT")   # segname 是定长 16 字节
    struct.pack_into("<QQQQ", blob, off + 24, 0x100000000, text_size, text_off, text_size)
    off += SEGMENT_64_SIZE

    struct.pack_into("<IIIII I".replace(" ", ""), blob, off,
                     LC_ENCRYPTION_INFO_64, ENCRYPTION_INFO_64_SIZE,
                     cryptoff, cryptsize, 1 if encrypted else 0, 0)
    return bytes(blob), {"text_off": text_off, "cryptoff": cryptoff,
                         "cryptsize": cryptsize, "page_size": page_size}
