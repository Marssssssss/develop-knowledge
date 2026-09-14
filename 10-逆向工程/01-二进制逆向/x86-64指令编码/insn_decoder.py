#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""insn_decoder.py —— x86-64 变长指令解码器（表驱动）

与 modrm_decoder.c 同题异构：C 版用手写 switch 演示字段拆解，
Python 版用"指令表 + 通用字段消费器"演示**反汇编器内核**的标准做法，
并额外实现 `insn_length()`——线性扫描（linear sweep）只需长度、
不必还原操作数，是性能关键路径上的最小实现。

运行：python3 insn_decoder.py
参考：OSDev "X86-64 Instruction Encoding"；Intel SDM Vol.2 §2.1
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------- 基础表

REG64 = ["rax", "rcx", "rdx", "rbx", "rsp", "rbp", "rsi", "rdi",
         "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15"]
REG32 = ["eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi",
         "r8d", "r9d", "r10d", "r11d", "r12d", "r13d", "r14d", "r15d"]

#: legacy prefix 按"组"归类，同组内最后一个生效
PREFIX_GROUPS = {
    1: {0xF0: "lock", 0xF2: "repne", 0xF3: "rep"},
    2: {0x2E: "cs", 0x36: "ss", 0x3E: "ds", 0x26: "es", 0x64: "fs", 0x65: "gs"},
    3: {0x66: "opsize", 0x67: "addrsize"},
}

#: opcode 表。kind 决定操作数形态：
#:   'nop' 无操作数 | 'rm_r' r/m 是目的、reg 是源 | 'r_rm' 反过来
#:   'rm'  只有 r/m，reg 字段当扩展操作码 | 'imm32' 立即数 32
#:   'rel32' 相对位移 | 'ret'
OPCODES: dict[int, tuple[str, str]] = {
    0x90: ("nop", "nop"),
    0xC3: ("ret", "ret"),
    0x89: ("mov", "rm_r"),
    0x8B: ("mov", "r_rm"),
    0x01: ("add", "rm_r"),
    0x29: ("sub", "rm_r"),
    0x39: ("cmp", "rm_r"),
    0xFF: ("group5", "rm"),
    0xE8: ("call", "rel32"),
}

#: FF 的 reg 字段是扩展操作码
GROUP5 = ["inc", "dec", "call", "callf", "jmp", "jmpf", "push", None]


@dataclass
class Decoded:
    """一条解码结果"""

    length: int = 0
    text: str = ""
    prefixes: list[str] = field(default_factory=list)
    rex: int = 0
    mem_rip_relative: bool = False


# ------------------------------------------------------------ 字段消费器

def _read_int(buf: bytes, off: int, size: int) -> int:
    """读 size 字节小端整数（size ∈ {1,2,4,8}）"""
    return int.from_bytes(buf[off:off + size], "little", signed=False)


def _sign_extend(v: int, size: int) -> int:
    """按 size 字节做符号扩展 —— disp8 的 -1 不能显示成 255"""
    return int.from_bytes(v.to_bytes(size, "little"), "little", signed=True)


def decode_modrm(buf: bytes, off: int):
    """拆 ModRM 三字段"""
    b = buf[off]
    return (b >> 6) & 0x3, (b >> 3) & 0x7, b & 0x7


def decode_rm(buf: bytes, off: int, mod: int, rm: int, rex: int, width: int):
    """解码 r/m 操作数。

    返回 (操作数字符串, 消耗字节数, 是否 RIP 相对)。
    分支顺序不可换：mod==3 → rm==4(SIB) → mod==0 && rm==5(RIP) → 普通内存。
    """
    ext_b = 8 if rex & 0x1 else 0            # REX.B
    ext_x = 8 if rex & 0x2 else 0            # REX.X
    # 注意：64 位模式下**寻址寄存器恒为 64 位**，与 REX.W（数据操作数宽度）无关；
    # 只有 0x67 地址尺寸前缀才会切到 32 位寻址。mod=11（寄存器直接操作数）才受宽度影响。
    regs = REG64

    # (1) mod=11：rm 就是寄存器
    if mod == 3:
        return regs[rm | ext_b], 0, False

    # (2) rm=100：SIB 字节在场
    if rm == 4:
        sib = buf[off]
        scale = 1 << ((sib >> 6) & 0x3)
        index = ((sib >> 3) & 0x7) | ext_x
        base = (sib & 0x7) | ext_b
        has_index = not (index == 4 and ext_x == 0)   # index=100 是"无变址"哨兵
        has_base = not (base == 5 and mod == 0)       # base=101&mod=00 是"无基址"
        consumed = 1
        db = {0: 0, 1: 1, 2: 4}[mod]
        if not has_base:
            db = 4
        disp = 0
        if db:
            disp = _sign_extend(_read_int(buf, off + consumed, db), db)
        consumed += db

        parts = []
        if has_base:
            parts.append(regs[base])
        if has_index:
            parts.append(f"{regs[index]}*{scale}")
        if db:
            parts.append(f"0x{disp:x}" if disp >= 0 else f"-0x{-disp:x}")
        if not parts:
            return "[???]", consumed, False
        # 拼接：负位移前不重复加 "+"
        expr = parts[0]
        for p in parts[1:]:
            expr += (" " + p) if p.startswith("-") else (" + " + p)
        return "[" + expr + "]", consumed, False

    # (3) mod=00 && rm=101：RIP 相对（disp32 相对"下一条指令"）
    if mod == 0 and rm == 5:
        d = _sign_extend(_read_int(buf, off, 4), 4)
        return f"[rip + 0x{d & 0xFFFFFFFF:x}]", 4, True

    # (4) 普通 [base (+disp)]
    db = {0: 0, 1: 1, 2: 4}[mod]
    disp = _sign_extend(_read_int(buf, off, db), db) if db else 0
    tail = f" + 0x{disp:x}" if disp > 0 else (f" - 0x{-disp:x}" if disp < 0 else "")
    return f"[{regs[rm | ext_b]}{tail}]", db, False


# -------------------------------------------------------------- 主解码器

def decode(buf: bytes, off: int = 0) -> Optional[Decoded]:
    """解码 buf[off:] 处的一条指令；返回 None 表示 opcode 未支持"""
    d = Decoded()
    i = off
    seen: dict[int, str] = {}

    # 阶段一：legacy prefix（最多 4 字节，同组后写覆盖前写）
    while i - off < 4 and buf[i] in (0xF0, 0xF2, 0xF3, 0x2E, 0x36,
                                     0x3E, 0x26, 0x64, 0x65, 0x66, 0x67):
        for g, table in PREFIX_GROUPS.items():
            if buf[i] in table:
                seen[g] = table[buf[i]]
        i += 1
    d.prefixes = [seen[g] for g in sorted(seen)]

    # 阶段二：REX —— 必须紧跟 opcode；这里一旦遇到 opcode 就不再当 REX
    if 0x40 <= buf[i] <= 0x4F:
        d.rex = buf[i] & 0x0F
        i += 1
        if i - off > 4:      # 前缀 + REX 合计不得超过 4 字节（SDM 规则）
            return None

    op = buf[i]
    i += 1
    width = 8 if d.rex & 0x08 else 4   # REX.W
    regs = REG64 if width == 8 else REG32

    # 阶段三：扩展 opcode（0F / B8+r 等）
    if (op & 0xF8) == 0xB8:
        r = (op & 7) | (8 if d.rex & 0x01 else 0)
        imm = _read_int(buf, i, 4)
        d.length = i - off + 4
        d.text = f"mov {regs[r]}, 0x{imm:x}"
        return d

    if op not in OPCODES:
        return None

    mnemonic, kind = OPCODES[op]

    if kind == "nop":
        d.length = i - off
        d.text = "nop"
        return d

    if kind == "ret":
        d.length = i - off
        d.text = "ret"
        return d

    if kind == "rel32":
        rel = _sign_extend(_read_int(buf, i, 4), 4)
        d.length = i - off + 4
        d.text = f"call 0x{rel & 0xFFFFFFFF:x}   ; 目标 = 下一条指令 + ({rel})"
        return d

    if kind == "imm32":
        imm = _read_int(buf, i, 4)
        d.length = i - off + 4
        d.text = f"{mnemonic} 0x{imm:x}"
        return d

    # 需要 ModRM 的三类
    mod, reg, rm = decode_modrm(buf, i)
    i += 1
    ext_r = reg | (8 if d.rex & 0x04 else 0)     # REX.R
    rm_str, used, rip_rel = decode_rm(buf, i, mod, rm, d.rex, width)
    i += used
    d.mem_rip_relative = rip_rel
    d.length = i - off

    if kind == "rm_r":
        d.text = f"{mnemonic} {rm_str}, {regs[ext_r]}"
    elif kind == "r_rm":
        d.text = f"{mnemonic} {regs[ext_r]}, {rm_str}"
    elif kind == "rm":
        g = GROUP5[reg]
        if g is None:
            return None
        d.text = f"{g} {rm_str}"
    return d


def insn_length(buf: bytes, off: int = 0) -> int:
    """线性扫描专用的轻量接口：只求长度。返回 0 表示解码失败。"""
    d = decode(buf, off)
    return d.length if d else 0


# ----------------------------------------------------------------- demo

CASES: list[tuple[bytes, str]] = [
    (bytes.fromhex("48 89 d8"), "寄存器-寄存器"),
    (bytes.fromhex("48 8b 04 25 00 10 00 00"), "SIB 绝对地址（无基址无变址 disp32）"),
    (bytes.fromhex("48 8b 44 8b 10"), "SIB 基址+变址*scale+disp8"),
    (bytes.fromhex("48 8b 44 8b f0"), "disp8 = -16 的符号扩展"),
    (bytes.fromhex("48 8b 05 10 00 00 00"), "RIP 相对"),
    (bytes.fromhex("4c 89 c0"), "REX.R 扩 reg 到 r8"),
    (bytes.fromhex("48 8b 04 c5 00 20 00 00"), "REX.X 扩 index 到 r8"),
    (bytes.fromhex("48 01 d8"), "REX.W 切 64 位"),
    (bytes.fromhex("ff 54 24 08"), "FF /2 + SIB(rsp)"),
    (bytes.fromhex("f3 0f 10 00"), "SSE mandatory prefix（未支持，返回失败）"),
    (bytes.fromhex("c3"), "单字节 ret"),
]


def main() -> None:
    print(f"{'bytes':<26} {'len':>3}  {'说明':<34} 反汇编")
    print("-" * 108)
    total = 0
    for code, note in CASES:
        d = decode(code)
        if d is None:
            print(f"{code.hex(' '):<26} {'-':>3}  {note:<34} <unsupported>")
            continue
        total += d.length
        rip = "  [RIP-rel]" if d.mem_rip_relative else ""
        print(f"{code.hex(' '):<26} {d.length:>3}  {note:<34} {d.text}{rip}")

    # 线性扫描：把一整块代码按长度切成指令流
    blob = bytes.fromhex("48 89 d8 48 01 d8 48 8b 05 10 00 00 00 ff d0 c3")
    print("\n线性扫描 byte blob：", blob.hex(" "))
    off = 0
    while off < len(blob):
        n = insn_length(blob, off)
        if n == 0:
            print(f"  +{off:02x}: <无法解码，线性扫描在此断裂（数据被误认或指令未支持）>")
            break
        d = decode(blob, off)
        print(f"  +{off:02x}: {d.text}")
        off += n


if __name__ == "__main__":
    main()
