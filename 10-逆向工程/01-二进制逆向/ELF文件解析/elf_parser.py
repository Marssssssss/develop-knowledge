#!/usr/bin/env python3
"""最小 ELF64 解析器 — 仅标准库,零第三方依赖。

依据 elf(5) 手册页(man7.org)定义的结构布局手工解析:
  Elf64_Ehdr(64B) @ 偏移 0  →  节头表 @ e_shoff(每项 64B)  →  .symtab(每项 24B)
所有偏移均来自 elf(5) 的结构体定义与"自然对齐"规则。

用法: python elf_parser.py <ELF 文件路径>
"""
import struct
import sys

# ---- elf(5) 常量表 ------------------------------------------------
ET = {0: "NONE", 1: "REL(可重定位)", 2: "EXEC(可执行)", 3: "DYN(共享对象)", 4: "CORE"}
EM = {3: "x86", 8: "MIPS", 40: "ARM", 50: "IA-64", 62: "x86-64", 183: "AArch64"}
SHT = {0: "NULL", 1: "PROGBITS", 2: "SYMTAB", 3: "STRTAB", 4: "RELA", 5: "HASH",
       6: "DYNAMIC", 8: "NOBITS", 9: "REL", 11: "DYNSYM"}
SHF = {1: "WRITE", 2: "ALLOC", 4: "EXECINSTR"}
STT = {0: "NOTYPE", 1: "OBJECT", 2: "FUNC", 3: "SECTION", 4: "FILE"}
STB = {0: "LOCAL", 1: "GLOBAL", 2: "WEAK"}

EHSZ, SHSZ, SYMSZ = 64, 64, 24  # Elf64_Ehdr / Elf64_Shdr / Elf64_Sym 大小


class Elf64:
    def __init__(self, data: bytes):
        self.d = data
        if data[:4] != b"\x7fELF":
            raise ValueError("魔数错误: 不是 ELF 文件")
        self.ei_class, self.ei_data = data[4], data[5]
        if (self.ei_class, self.ei_data) != (2, 1):
            raise NotImplementedError("本 demo 仅支持 ELFCLASS64 + 小端(ELFDATA2LSB)")
        # e_ident 之后: HH I Q Q Q I H H H H H H  = 48 字节 → 共 64
        (self.e_type, self.e_machine, self.e_version, self.e_entry,
         self.e_phoff, self.e_shoff, self.e_flags, self.e_ehsize,
         self.e_phentsize, self.e_phnum, self.e_shentsize,
         self.e_shnum, self.e_shstrndx) = struct.unpack_from("<HHIQQQIHHHHHH", data, 16)

    # ---- 节头表 ----
    def sections(self):
        """解析节头表;返回 [(name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, sh_link, sh_entsize)]"""
        hdrs = []
        for i in range(self.e_shnum):
            off = self.e_shoff + i * SHSZ
            (sh_name, sh_type, sh_flags, sh_addr, sh_offset,
             sh_size, sh_link, sh_info, sh_addralign, sh_entsize) = struct.unpack_from(
                "<IIQQQQIIQQ", self.d, off)
            hdrs.append(dict(idx=i, name_off=sh_name, type=sh_type, flags=sh_flags,
                             addr=sh_addr, offset=sh_offset, size=sh_size,
                             link=sh_link, entsize=sh_entsize))
        # 节名存在 e_shstrndx 指向的字符串表里(SHT_STRTAB)
        if self.e_shstrndx < len(hdrs):
            strtab = self._section_bytes(hdrs[self.e_shstrndx])
            for h in hdrs:
                h["name"] = self._cstr(strtab, h["name_off"])
        else:
            for h in hdrs:
                h["name"] = f"<bad shstrndx {self.e_shstrndx}>"
        return hdrs

    def _section_bytes(self, sh) -> bytes:
        return self.d[sh["offset"]:sh["offset"] + sh["size"]]

    @staticmethod
    def _cstr(buf: bytes, off: int) -> str:
        end = buf.find(b"\x00", off)
        return buf[off:end].decode("latin-1") if end >= 0 else ""

    # ---- 符号表(.symtab 全量 / .dynsym 动态) ----
    def symbols(self, sections):
        out = []
        for sh in sections:
            if sh["type"] not in (2, 11):  # SHT_SYMTAB / SHT_DYNSYM
                continue
            strtab = self._section_bytes(sections[sh["link"]])  # sh_link → 字符串表
            for i in range(sh["size"] // SYMSZ):
                off = sh["offset"] + i * SYMSZ
                st_name, st_info, st_other, st_shndx, st_value, st_size = \
                    struct.unpack_from("<IBBHQQ", self.d, off)
                if st_name == 0 and st_value == 0:
                    continue  # 索引 0 的空符号
                out.append(dict(
                    table=".dynsym" if sh["type"] == 11 else ".symtab",
                    name=self._cstr(strtab, st_name),
                    bind=STB.get(st_info >> 4, str(st_info >> 4)),   # ELF64_ST_BIND
                    type=STT.get(st_info & 0xF, str(st_info & 0xF)),  # ELF64_ST_TYPE
                    value=st_value, size=st_size, shndx=st_shndx))
        return out


def main(path):
    with open(path, "rb") as f:
        elf = Elf64(f.read())

    print(f"== ELF 头(Elf64_Ehdr, {EHSZ} 字节)==")
    print(f"  类别: ELFCLASS64  小端 | 类型: {ET.get(elf.e_type, elf.e_type)}"
          f" | 机器: {EM.get(elf.e_machine, elf.e_machine)}")
    print(f"  入口 e_entry      : 0x{elf.e_entry:x}")
    print(f"  节头表 e_shoff    : 0x{elf.e_shoff:x} ({elf.e_shnum} 项 × {elf.e_shentsize}B,"
          f" 名称表索引 e_shstrndx={elf.e_shstrndx})")

    secs = elf.sections()
    print(f"\n== 节头表(Elf64_Shdr, 每项 {SHSZ} 字节)==")
    print(f"  {'idx':>3} {'name':<18} {'type':<10} {'flags':<16} {'addr':>12} {'off':>8} {'size':>8}")
    for s in secs:
        flags = "|".join(v for k, v in SHF.items() if s["flags"] & k)
        print(f"  {s['idx']:>3} {s['name']:<18} {SHT.get(s['type'], s['type']):<10} "
              f"{flags:<16} {s['addr']:>12x} {s['offset']:>8x} {s['size']:>8x}")

    syms = elf.symbols(secs)
    print(f"\n== 符号表(Elf64_Sym, 每项 {SYMSZ} 字节, 共 {len(syms)} 个)==")
    for s in syms[:25]:
        print(f"  [{s['table']}] {s['bind']:>6} {s['type']:>7} 0x{s['value']:>12x} "
              f"{s['size']:>6}  {s['name']}")
    if len(syms) > 25:
        print(f"  ... 其余 {len(syms) - 25} 个省略")
    # strip 前后差异:.symtab 消失,只剩 .dynsym —— 逆向定位入口的常用判据
    has_symtab = any(s["type"] == 2 for s in secs)
    print(f"\n  含 .symtab(未 strip): {has_symtab} — "
          f"{'可直接读函数名' if has_symtab else '符号已剥离,需反推/FLIRT 签名匹配'}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("用法: python elf_parser.py <ELF 文件>")
    main(sys.argv[1])
