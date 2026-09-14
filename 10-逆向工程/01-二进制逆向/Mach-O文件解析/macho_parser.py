#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""macho_parser.py —— Mach-O（macOS / iOS）可执行文件格式解析器

解析链条： mach_header_64 → load_command[] → segment_command_64 → section_64，
并额外取出 LC_SYMTAB（符号表位置）与 LC_LOAD_DYLIB（依赖库 + 库序号）。
常量表与合成样本构造在 `macho_defs.py`（拆分原因见该文件头注释）。

为了让 demo 在没有 macOS 的机器上也能跑，内置一个**代码构造的合成样本**
（结构与真实 MH_EXECUTE 一致，含 __PAGEZERO / __TEXT / __DATA / __LINKEDIT）。

用法：
    python3 macho_parser.py              # 解析内置合成样本（任意平台）
    python3 macho_parser.py /usr/bin/ls  # 解析真实 Mach-O（仅 macOS）

参考：OSX ABI Mach-O File Format Reference (aidansteele)；Apple `<mach-o/loader.h>`
"""

from __future__ import annotations

import struct
import sys

from macho_defs import (  # noqa: F401  (Section/Segment 供类型注解与外部复用)
    DYLIB_CMD_SIZE, LC_ID_DYLIB, LC_LOAD_DYLIB, LC_NAME, LC_SEGMENT_64,
    LC_SYMTAB, MAGIC_SIZE, MH_CIGAM_64, MH_MAGIC_64, SECTION_64_SIZE,
    SEG_CMD_64_SIZE, Dylib, MachHeader, MachO, MachOError, Section, Segment,
    build_sample,
)

# --------------------------------------------------------------- 解析器


def parse_header(data: bytes) -> MachHeader:
    if len(data) < MAGIC_SIZE:
        raise MachOError("文件太小，连 mach_header_64 都装不下")
    fields = struct.unpack_from("<IIIIIIII", data, 0)
    h = MachHeader(*fields)
    if h.magic == MH_CIGAM_64:
        raise MachOError("检测到大端 Mach-O（MH_CIGAM_64），本 demo 不做字节序翻转")
    if h.magic != MH_MAGIC_64:
        raise MachOError(f"不是 64 位 Mach-O：magic=0x{h.magic:08x}")
    return h


def iter_load_commands(data: bytes, header: MachHeader):
    """按 cmdsize 步进遍历命令数组。

    64 位下 cmdsize **必须 8 字节对齐**，否则后续全部错位 —— 见 README 坑 1。
    """
    off = MAGIC_SIZE
    for i in range(header.ncmds):
        if off + 8 > len(data):
            raise MachOError(f"第 {i} 条 load command 越界")
        cmd, cmdsize = struct.unpack_from("<II", data, off)
        if cmdsize < 8 or cmdsize % 8 != 0:
            raise MachOError(f"第 {i} 条 cmdsize 非法（64 位需 8 字节对齐）: {cmdsize:#x}")
        if off + cmdsize > len(data):
            raise MachOError(f"第 {i} 条 load command 超出文件末尾")
        yield off, cmd, cmdsize, data[off:off + cmdsize]
        off += cmdsize
    # 命令区实际结束位置应与 sizeofcmds 一致
    if off - MAGIC_SIZE != header.sizeofcmds:
        raise MachOError(
            f"sizeofcmds 与实际不符：声明 {header.sizeofcmds}，实测 {off - MAGIC_SIZE}")


def _cstr16(raw: bytes) -> str:
    """固定 16 字节名 → 字符串。

    注意：短名才补 '\\0'，长名可能**没有终止符**，必须强制作终止（README 坑 2）。
    """
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace")


def _lc_str(cmd_bytes: bytes, offset: int) -> str:
    """union lc_str 存的是**相对 load command 起点**的偏移（README 坑 6）"""
    if not 0 < offset < len(cmd_bytes):
        return f"<非法 lc_str 偏移 {offset:#x}>"
    end = cmd_bytes.find(b"\0", offset)
    if end < 0:
        end = len(cmd_bytes)
    return cmd_bytes[offset:end].decode("utf-8", "replace")


def _ver(v: int) -> str:
    """packed version：X.Y.Z 各 16/8/8 位"""
    return f"{(v >> 16) & 0xFFFF}.{(v >> 8) & 0xFF}.{v & 0xFF}"


def _parse_segment(cmd_bytes: bytes) -> Segment:
    (_, _, segname_raw, vmaddr, vmsize, fileoff, filesize,
     maxprot, initprot, nsects, flags) = struct.unpack_from("<II16sQQQQiiII", cmd_bytes, 0)
    seg = Segment(_cstr16(segname_raw), vmaddr, vmsize, fileoff, filesize,
                  maxprot, initprot, flags)

    expect = SEG_CMD_64_SIZE + nsects * SECTION_64_SIZE
    if len(cmd_bytes) != expect:
        raise MachOError(
            f"{seg.segname}: cmdsize 与 nsects 不符（期望 {expect}，实得 {len(cmd_bytes)}）")

    off = SEG_CMD_64_SIZE
    for _ in range(nsects):
        (sectname_raw, segname_raw2, addr, size, soff, align,
         reloff, nreloc, sflags, _r1, _r2, _r3) = struct.unpack_from(
            "<16s16sQQIIIIIIII", cmd_bytes, off)
        sec = Section(index=0, sectname=_cstr16(sectname_raw),
                      segname=_cstr16(segname_raw2), addr=addr, size=size,
                      offset=soff, align=align, flags=sflags)
        # 记录一下重定位信息，仅供展示时判断有无
        setattr(sec, "nreloc", nreloc)
        setattr(sec, "reloff", reloff)
        seg.sections.append(sec)
        off += SECTION_64_SIZE
    return seg


def parse(data: bytes) -> MachO:
    header = parse_header(data)
    segments: list[Segment] = []
    dylibs: list[Dylib] = []
    other: list[tuple[int, int]] = []
    symtab = None
    section_counter = 0

    for _off, cmd, cmdsize, body in iter_load_commands(data, header):
        if cmd == LC_SEGMENT_64:
            seg = _parse_segment(body)
            for s in seg.sections:          # 节编号从 1 开始，跨段连续（README 坑 5）
                section_counter += 1
                s.index = section_counter
            segments.append(seg)

        elif cmd == LC_SYMTAB:
            _, _, symoff, nsyms, stroff, strsize = struct.unpack_from("<IIIIII", body, 0)
            symtab = (symoff, nsyms, stroff, strsize)

        elif cmd in (LC_LOAD_DYLIB, LC_ID_DYLIB):
            name_off, _ts, cur, compat = struct.unpack_from("<IIII", body, 8)
            name = _lc_str(body, name_off)
            dylibs.append(Dylib(ordinal=len(dylibs) + 1, name=name,
                                current_version=_ver(cur), compat_version=_ver(compat)))

        else:
            other.append((cmd, cmdsize))

    return MachO(header, segments, dylibs, symtab, other)


# --------------------------------------------------------------- 展示

def dump(m: MachO) -> None:
    h = m.header
    print("=== mach_header_64 ===")
    print(f"  magic      = 0x{h.magic:08x}  (MH_MAGIC_64)")
    print(f"  cputype    = {h.cpuname}   cpusubtype = 0x{h.cpusubtype:x}")
    print(f"  filetype   = {h.filetypename} ({h.filetype})")
    print(f"  ncmds      = {h.ncmds}   sizeofcmds = {h.sizeofcmds} 字节")
    print(f"  flags      = 0x{h.flags:08x}")

    print("\n=== 段（LC_SEGMENT_64）===")
    print(f"  {'segname':<12} {'vmaddr':>12} {'vmsize':>9} {'fileoff':>8} "
          f"{'filesize':>9}  prot(init/max)  节数")
    for s in m.segments:
        print(f"  {s.segname:<12} 0x{s.vmaddr:09x} 0x{s.vmsize:07x} "
              f"0x{s.fileoff:06x} 0x{s.filesize:07x}  {s.prot_str:<12} {len(s.sections)}")

    print("\n=== 节（section_64）===")
    print(f"  {'#':>2} {'sectname':<18} {'segname':<10} {'addr':>12} {'size':>6} "
          f"{'offset':>7} {'align':>5}  类型/属性")
    for s in m.segments:
        for sec in s.sections:
            attr = " | PURE_INSTRUCTIONS" if sec.pure_instructions else ""
            print(f"  {sec.index:>2} {sec.sectname:<18} {sec.segname:<10} "
                  f"0x{sec.addr:09x} 0x{sec.size:04x} 0x{sec.offset:05x} "
                  f"{1 << sec.align:>5}{'B':<1} {sec.sectype}{attr}")

    print("\n=== 依赖库（LC_LOAD_DYLIB，顺序即库序号 ordinal）===")
    for d in m.dylibs:
        print(f"  [{d.ordinal}] {d.name}   cur={d.current_version} "
              f"compat={d.compat_version}")

    print("\n=== 符号表（LC_SYMTAB）===")
    if m.symtab:
        symoff, nsyms, stroff, strsize = m.symtab
        print(f"  symoff=0x{symoff:x} nsyms={nsyms} stroff=0x{stroff:x} strsize=0x{strsize:x}")
        print("  → 符号表是 nlist_64 数组，每项 16 字节；字符串表存名字")
    else:
        print("  无")

    print("\n=== 其他 load command ===")
    for cmd, size in m.other_commands:
        print(f"  {LC_NAME.get(cmd, f'cmd(0x{cmd:x})'):<16} cmdsize={size}")

    # 一致性自检：节编号必须从 1 开始且跨段连续
    idx = [s.index for seg in m.segments for s in seg.sections]
    assert idx == list(range(1, len(idx) + 1)), f"节编号不连续: {idx}"
    pagezero = [s for s in m.segments if s.segname == "__PAGEZERO"]
    print("\n=== 解析自检 ===")
    print(f"  段数={len(m.segments)}  节数={len(idx)}  依赖库={len(m.dylibs)}")
    print(f"  节编号 = 1..{len(idx)} 连续（跨段编号，从 1 开始）")
    if pagezero:
        p = pagezero[0]
        print(f"  __PAGEZERO: vmsize=0x{p.vmsize:x} 而 filesize={p.filesize} "
              f"→ 虚拟占位、不占磁盘（README 坑 4）")
    text = next((s for s in m.segments if s.segname == "__TEXT"), None)
    if text:
        code = next((c for c in text.sections if c.sectname == "__text"), None)
        if code:
            print(f"  __TEXT,__text 标记为 PURE_INSTRUCTIONS "
                  f"→ addr=0x{code.addr:x} 起的 0x{code.size:x} 字节是代码")


def main() -> None:
    if len(sys.argv) > 1:
        path = sys.argv[1]
        with open(path, "rb") as f:
            data = f.read()
        print(f"解析文件：{path}（{len(data)} 字节）\n")
    else:
        data = build_sample()
        print(f"解析内置合成样本（{len(data)} 字节，模拟 MH_EXECUTE 布局）\n")

    m = parse(data)
    dump(m)


if __name__ == "__main__":
    main()
