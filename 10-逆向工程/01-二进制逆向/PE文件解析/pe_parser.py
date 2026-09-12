#!/usr/bin/env python3
"""最小 PE 解析器(PE32 / PE32+ 双支持)— 仅标准库。

依据 Microsoft Learn "PE Format" 规范手工解析:
  DOS 头(e_magic='MZ' @0, e_lfanew @0x3C)
  → NT 签名 'PE\\0\\0' → COFF 文件头(20B)
  → 可选头(Magic 0x10b=PE32 / 0x20b=PE32+, 内含数据目录)
  → 节表(每项 40B) → 导入表(数据目录 #1)

核心是 rva_to_offset():RVA(相对虚拟地址)→ 文件偏移 的换算,
这是解析 PE 任何目录(导入/导出/资源/重定位)的公共前置。

用法: python pe_parser.py <PE 文件路径>   (.exe / .dll / .sys)
"""
import struct
import sys

MACHINE = {0x14c: "i386", 0x8664: "AMD64", 0xaa64: "ARM64"}
SUBSYSTEM = {1: "NATIVE", 2: "GUI", 3: "CUI(控制台)", 10: "EFI 应用"}
DIR_NAMES = ["Export", "Import", "Resource", "Exception", "Certificate", "Reloc",
             "Debug", "Architecture", "GlobalPtr", "TLS", "LoadConfig",
             "BoundImport", "IAT", "DelayImport", "CLR", "Reserved"]
SECTION_CHARS = [(0x20, "CODE"), (0x40, "INITIALIZED_DATA"), (0x02000000, "DISCARDABLE"),
                 (0x20000000, "EXECUTE"), (0x40000000, "READ"), (0x80000000, "WRITE")]


class PE:
    def __init__(self, data: bytes):
        self.d = data
        if data[:2] != b"MZ":
            raise ValueError("DOS 头 e_magic 不是 'MZ'")
        (self.e_lfanew,) = struct.unpack_from("<I", data, 0x3C)
        if data[self.e_lfanew:self.e_lfanew + 4] != b"PE\x00\x00":
            raise ValueError(f"NT 签名错误 @0x{self.e_lfanew:x}")
        coff = self.e_lfanew + 4
        (self.machine, self.num_sections, self.timestamp, _sym_ptr, _num_sym,
         self.size_opt, self.characteristics) = struct.unpack_from("<HHIIIHH", data, coff)
        opt = coff + 20
        (self.magic,) = struct.unpack_from("<H", data, opt)
        if self.magic not in (0x10b, 0x20b):
            raise ValueError(f"可选头 Magic 0x{self.magic:x} 非 PE32/PE32+")
        self.pe32plus = self.magic == 0x20b
        # 可选头:PE32+ 布局(标准字段 24B 后接 ImageBase 8B;PE32 是 28B 后 4B)
        (self.entry_rva, self.base_of_code) = struct.unpack_from("<II", data, opt + 16)
        base_off = opt + (24 if self.pe32plus else 28)
        self.image_base = struct.unpack_from("<Q" if self.pe32plus else "<I", data, base_off)[0]
        (self.sect_align, self.file_align) = struct.unpack_from("<II", data, base_off + 8)
        num_dd_off = opt + (108 if self.pe32plus else 92)
        (self.num_dd,) = struct.unpack_from("<I", data, num_dd_off)
        # DllCharacteristics 与 Subsystem 在 PE32/PE32+ 中偏移相同(可选头 +68)
        (self.dll_chars, self.subsystem) = struct.unpack_from("<HH", data, opt + 68)
        dd_off = num_dd_off + 4
        self.data_dirs = [struct.unpack_from("<II", data, dd_off + 8 * i)
                          for i in range(min(self.num_dd, 16))]
        # 节表紧跟可选头
        self.sections = []
        st = opt + self.size_opt
        for i in range(self.num_sections):
            (name, vsize, vaddr, rsize, roff, _rel, _ln, _nrel, _nln,
             chars) = struct.unpack_from("<8sIIIIIIHHI", data, st + 40 * i)
            self.sections.append(dict(
                name=name.rstrip(b"\x00").decode("latin-1"), vsize=vsize, vaddr=vaddr,
                rsize=rsize, roff=roff, chars=chars))

    def rva_to_offset(self, rva: int) -> int:
        """RVA → 文件偏移。头部区(SizeOfHeaders 内)磁盘与内存布局一致;
        否则查节表:file = rva - sec.VirtualAddress + sec.PointerToRawData"""
        first = self.sections[0]
        if rva < first["vaddr"]:
            return rva  # 落在头部
        for s in self.sections:
            if s["vaddr"] <= rva < s["vaddr"] + max(s["vsize"], s["rsize"]):
                if rva >= s["vaddr"] + s["rsize"]:
                    return -1  # .bss 类零填充区,磁盘无对应数据
                return rva - s["vaddr"] + s["roff"]
        return -1

    def cstr(self, off: int) -> str:
        end = self.d.find(b"\x00", off)
        return self.d[off:end].decode("latin-1")

    def imports(self):
        """遍历导入表:IMAGE_IMPORT_DESCRIPTOR 数组,全零项结尾(每项 20B)。
        OriginalFirstThunk→ILT(名字),FirstThunk→IAT(加载器改写为真实地址)。"""
        if len(self.data_dirs) < 2 or self.data_dirs[1][0] == 0:
            return []
        out = []
        off = self.rva_to_offset(self.data_dirs[1][0])
        for i in range(256):  # 上限防损坏文件死循环
            oft, ts, fwd, name_rva, ft = struct.unpack_from("<IIIII", self.d, off + 20 * i)
            if oft == name_rva == ft == 0:
                break
            dll = self.cstr(self.rva_to_offset(name_rva))
            thunk_rva = oft or ft          # 绑定场景 OFT 可为 0,回退 IAT
            funcs = self._walk_thunks(thunk_rva)
            out.append((dll, funcs))
        return out

    def _walk_thunks(self, rva: int):
        names = []
        off = self.rva_to_offset(rva)
        for i in range(4096):
            if self.pe32plus:
                (val,) = struct.unpack_from("<Q", self.d, off + 8 * i)
            else:
                (val,) = struct.unpack_from("<I", self.d, off + 4 * i)
            if val == 0:
                break
            ordinal_flag = 1 << 63 if self.pe32plus else 1 << 31
            if val & ordinal_flag:
                names.append(f"#ordinal{val & 0xFFFF}")
            else:
                hint_off = self.rva_to_offset(val & 0x7FFFFFFF)
                names.append(self.cstr(hint_off + 2))  # IMAGE_IMPORT_BY_NAME: WORD Hint + 名字
        return names


def main(path):
    with open(path, "rb") as f:
        pe = PE(f.read())
    print(f"== PE {'32+' if pe.pe32plus else '32'} | 机器 {MACHINE.get(pe.machine, hex(pe.machine))}"
          f" | {pe.num_sections} 节 | 子系统 {SUBSYSTEM.get(pe.subsystem, pe.subsystem)} ==")
    print(f"  ImageBase=0x{pe.image_base:x}  EntryPoint RVA=0x{pe.entry_rva:x}"
          f"  (VA=0x{pe.image_base + pe.entry_rva:x})")
    print(f"  SectionAlignment=0x{pe.sect_align:x}  FileAlignment=0x{pe.file_align:x}")
    print(f"\n== 节表(每项 40B)==")
    for s in pe.sections:
        chars = "|".join(v for k, v in SECTION_CHARS if s["chars"] & k)
        print(f"  {s['name']:<10} VA=0x{s['vaddr']:08x} VSize=0x{s['vsize']:06x}"
              f" Raw=0x{s['roff']:08x} RawSize=0x{s['rsize']:06x}  {chars}")
    print(f"\n== 数据目录(前 8 项)==")
    for i, (rva, sz) in enumerate(pe.data_dirs[:8]):
        print(f"  [{i}] {DIR_NAMES[i]:<12} RVA=0x{rva:08x} Size=0x{sz:x}"
              f"{'  → 文件偏移 0x%x' % pe.rva_to_offset(rva) if rva else ''}")
    print(f"\n== 导入表 ==")
    for dll, funcs in pe.imports():
        print(f"  {dll} ({len(funcs)} 个函数): {', '.join(funcs[:8])}"
              f"{' ...' if len(funcs) > 8 else ''}")
    if not pe.imports():
        print("  (无导入表 — 可能是静态链接或 .sys 驱动)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("用法: python pe_parser.py <PE 文件>")
    main(sys.argv[1])
