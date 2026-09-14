#!/usr/bin/env python3
"""mini_bpf.py — eBPF 虚拟机 + 验证器的最小忠实实现(教学用,不依赖内核)

覆盖:
  1. 8 字节定长指令的编码/解码(opcode + regs + offset + imm)
  2. 11 个 64 位寄存器 r0-r10,r10 是只读帧指针,512 字节栈
  3. 8 个 instruction class:LD/LDX/ST/STX/ALU/JMP/JMP32/ALU64
  4. 验证器:未初始化读、栈越界、写 r10、r0 未设就 EXIT、无界循环
  5. 可执行的最小 VM:ALU64/ALU 运算 + 跳转 + 栈访存 + helper 白名单 + map

权威依据:docs.kernel.org/bpf/standardization/instruction-set.html(编码与指令类)、
ebpf.io/what-is-ebpf(验证器职责与"必须运行至完成")。
"""

import struct
from dataclasses import dataclass

# ---------------------------------------------------------------- 编码常量
LD, LDX, ST, STX, ALU, JMP, JMP32, ALU64 = range(8)          # opcode 低 3 位 = class
ADD, SUB, MUL, DIV, OR, AND, LSH, RSH, NEG, MOD, XOR, MOV, ARSH, END = range(14)
JA, JEQ, JGT, JGE, JSET, JNE, JSGT, JSGE, CALL, EXIT, JLT, JLE = range(12)
K, X = 0, 1                                                   # source: 立即数 / 寄存器
MEM, DW, W = 3, 3, 0                                          # mode / size 取值
SIZE_BYTES = {W: 4, 1: 2, 2: 1, DW: 8}                        # sz 字段 → 字节数
STACK, FP = 512, 10                                           # 栈大小 / r10 帧指针
MAP_LOOKUP, MAP_UPDATE, KTIME_NS, TRACE_PRINTK = 1, 2, 5, 6   # helper 白名单(节选)
MASK64 = (1 << 64) - 1


@dataclass
class Ins:
    """一条 eBPF 基本指令 = 8 字节"""
    opcode: int
    dst: int = 0
    src: int = 0
    offset: int = 0
    imm: int = 0

    @property
    def cls(self) -> int:
        return self.opcode & 0x07

    @property
    def code(self) -> int:
        return (self.opcode >> 4) & 0x0F

    @property
    def source(self) -> int:
        return (self.opcode >> 3) & 1

    @property
    def size(self) -> int:
        # LD/ST 类:mode 占 bit 5-7,size 占 bit 3-4
        return SIZE_BYTES[(self.opcode >> 3) & 0x03]

    def encode(self) -> bytes:
        # 小端主机:regs 字节 = |src_reg(高 4) | dst_reg(低 4)|
        return struct.pack("<BBhi", self.opcode, (self.src << 4) | self.dst,
                           self.offset, self.imm)

    @staticmethod
    def decode(raw: bytes) -> "Ins":
        o, regs, off, imm = struct.unpack("<BBhi", raw)
        return Ins(o, dst=regs & 0x0F, src=regs >> 4, offset=off, imm=imm)


def _op(cls, code, source):
    """ALU/JMP 类:code 占 bit 4-7,source 占 bit 3"""
    return cls | (code << 4) | (source << 3)


def _mem_op(cls, mode, size):
    """LD/ST 类:mode 占 bit 5-7,size 占 bit 3-4"""
    return cls | (mode << 5) | (size << 3)


# ---------------------------------------------------------------- 汇编小工具
def mov64i(dst, imm):
    return Ins(_op(ALU64, MOV, K), dst, imm=imm)


def mov64r(dst, src):
    return Ins(_op(ALU64, MOV, X), dst, src=src)


def alu64(code, dst, imm=None, src=0):
    return Ins(_op(ALU64, code, K if imm is not None else X), dst, src=src, imm=imm or 0)


def ldxdw(dst, ptr, off):
    return Ins(_mem_op(LDX, MEM, DW), dst, src=ptr, offset=off)


def stxdw(ptr, off, val):
    return Ins(_mem_op(STX, MEM, DW), ptr, src=val, offset=off)


def jmp(code, dst, off, imm=None, src=0):
    return Ins(_op(JMP, code, K if imm is not None else X), dst, src=src,
               offset=off, imm=imm or 0)


def call_helper(nr):
    return Ins(0x85, imm=nr)          # {CALL, K, JMP}


EXIT_INS = Ins(0x95)                  # {EXIT, K, JMP}


# ---------------------------------------------------------------- 验证器
def verify(prog):
    """返回 (ok, reason)。抽象解释:状态 = (pc, 已初始化寄存器位图)。"""
    if not prog or prog[-1].cls != JMP or prog[-1].code != EXIT:
        return False, "last instruction must be EXIT"
    worklist, seen = [(0, 1 << FP)], set()          # r10 天生可用
    while worklist:
        pc, init = worklist.pop()
        if pc >= len(prog):
            return False, f"fallthrough off the end at pc={pc}"
        if (pc, init) in seen:                       # 不动点,不再展开
            continue
        seen.add((pc, init))
        ins, cls, code = prog[pc], prog[pc].cls, prog[pc].code
        ready = lambda r: (init >> r) & 1            # noqa: E731

        # ① r10 只读:任何 ALU/ALU64 写 r10 都非法
        if cls in (ALU, ALU64) and ins.dst == FP:
            return False, f"pc={pc}: r10 is a read-only frame pointer"

        # ② 读的寄存器必须先初始化(MOV 覆盖 dst,只需读 src)
        if cls in (ALU, ALU64) and code == MOV:
            reads = () if ins.source == K else (ins.src,)
        elif cls in (ALU, ALU64, JMP):               # 读 dst(操作数/条件寄存器)
            reads = (ins.dst,) + ((ins.src,) if ins.source == X else ())
        elif cls == LDX:                             # 读 src(基址指针)
            reads = (ins.src,)
        elif cls == STX:                             # 读 dst(基址指针)+ src(待存值)
            reads = (ins.dst, ins.src)
        else:
            reads = ()
        for r in reads:
            if not ready(r):
                return False, f"pc={pc}: use of uninitialized r{r}"

        # ③ 栈访存落在 [-512, -1]:LDX 的指针在 src,STX 的指针在 dst
        ptr = ins.src if cls == LDX else (ins.dst if cls == STX else None)
        if ptr == FP and not (-STACK <= ins.offset <= -ins.size):
            return False, f"pc={pc}: stack access {ins.offset} outside [-512,-1]"

        # ④ 反向跳转必须有可证明的有界循环计数器
        if cls == JMP and code == JA and ins.offset < 0:
            ok, why = bounded_loop(prog, pc)
            if not ok:
                return False, f"pc={pc}: {why}"

        nxt = init | (1 << ins.dst) if cls in (ALU, ALU64, LDX) else init
        if cls != JMP:
            worklist.append((pc + 1, nxt))
        elif code == EXIT:
            if not ready(0):
                return False, f"pc={pc}: EXIT with uninitialized r0"
        elif code == JA:
            worklist.append((pc + 1 + ins.offset, nxt))
        else:
            worklist.append((pc + 1, nxt))
            worklist.append((pc + 1 + ins.offset, nxt))
    return True, "ok"


def bounded_loop(prog, pc):
    """循环体内必须存在对被计数寄存器的 SUB imm(常量上界),否则视为无界循环。"""
    target = pc + 1 + prog[pc].offset
    for i in range(target, pc):
        ins = prog[i]
        if ins.cls == ALU64 and ins.code == SUB and ins.source == K:
            for j in range(max(0, target - 4), target):
                m = prog[j]
                if m.cls == ALU64 and m.code == MOV and m.source == K and m.dst == ins.dst:
                    return True, f"bounded: r{ins.dst} <= {m.imm}"
    return False, "unbounded loop: no decrementing loop counter"


# ---------------------------------------------------------------- 执行引擎
def run(prog, ctx, limit=200000):
    r, stack, steps, pc = [0] * 11, bytearray(STACK), 0, 0
    r[FP] = STACK
    while True:
        if steps > limit:
            raise RuntimeError("instruction budget exhausted (= 复杂度上限)")
        steps += 1
        ins, cls, code = prog[pc], prog[pc].cls, prog[pc].code
        if cls in (ALU, ALU64):
            bits = 64 if cls == ALU64 else 32
            m = (1 << bits) - 1
            a = r[ins.dst] & m
            b = ins.imm if ins.source == K else r[ins.src] & m
            sh = b & (63 if bits == 64 else 31)
            if code == MOV:
                r[ins.dst] = (ins.imm & m) if ins.source == K else b
                pc += 1
                continue
            vals = {ADD: a + b, SUB: a - b, MUL: a * b, OR: a | b, AND: a & b,
                    XOR: a ^ b, LSH: a << sh, RSH: a >> sh,
                    ARSH: ((a - (1 << bits)) if (a >> (bits - 1)) else a) >> sh,
                    NEG: -a, DIV: 0 if b == 0 else a // b, MOD: 0 if b == 0 else a % b}
            r[ins.dst] = vals[code] & m
        elif cls == LDX:
            if ins.src == FP:
                raw = stack[STACK + ins.offset:STACK + ins.offset + 8]
                r[ins.dst] = int.from_bytes(raw, "little", signed=True)
            else:
                r[ins.dst] = r[ins.src]
        elif cls == STX:
            if ins.dst == FP:
                raw = (r[ins.src] & MASK64).to_bytes(8, "little")
                stack[STACK + ins.offset:STACK + ins.offset + 8] = raw
        elif cls == JMP:
            if code == EXIT:
                return r[0]
            if code == CALL:
                r[0] = helper(ins.imm, r, ctx)
                pc += 1
                continue
            if code == JA:
                pc += 1 + ins.offset
                continue
            a0 = r[ins.dst]
            b0 = ins.imm if ins.source == K else r[ins.src]
            taken = {JEQ: a0 == b0, JNE: a0 != b0, JGT: a0 > b0, JGE: a0 >= b0,
                     JLT: a0 < b0, JLE: a0 <= b0, JSET: bool(a0 & b0)}[code]
            pc += 1 + (ins.offset if taken else 0)
            continue
        pc += 1


def helper(nr, r, ctx):
    """helper 白名单:内核只暴露稳定 ABI 的函数,BPF 不能调任意内核函数。"""
    if nr == KTIME_NS:
        return ctx["time_ns"]
    if nr in (MAP_LOOKUP, MAP_UPDATE, TRACE_PRINTK):
        return 0
    raise RuntimeError(f"unknown helper id {nr}")


# ---------------------------------------------------------------- 测试程序
def prog_count_events():
    """r7 从 100 递减到 0,每次给 r8 加 1,结果经栈传出。"""
    p = [mov64i(6, 0), mov64i(7, 100), mov64i(8, 0)]
    loop = len(p)
    p.append(alu64(ADD, 8, imm=1))
    p.append(alu64(SUB, 7, imm=1))                   # ← 有界循环的"证据"
    p.append(jmp(JNE, 7, loop - (len(p) + 1), imm=0))
    p += [stxdw(FP, -8, 8), ldxdw(9, FP, -8), mov64r(0, 9), EXIT_INS]
    return p


def prog_uninit():
    return [alu64(ADD, 3, imm=1), mov64i(0, 0), EXIT_INS]


def prog_stack_oob():
    return [mov64i(0, 0), stxdw(FP, -520, 0), EXIT_INS]


def prog_unbounded():
    return [mov64i(0, 0), jmp(JA, 0, -1), EXIT_INS]


def main():
    # 0) 编码自检:我们算出的 opcode 必须与真实 BPF 助记符常量一致
    cases = [(mov64i(0, 1), 0xB7, "BPF_MOV64_IMM"), (mov64r(0, 1), 0xBF, "BPF_MOV64_REG"),
             (alu64(ADD, 1, imm=1), 0x07, "BPF_ADD_IMM"), (alu64(SUB, 1, imm=1), 0x17, "BPF_SUB_IMM"),
             (ldxdw(2, 10, -8), 0x79, "BPF_LDX_MEM(DW)"), (stxdw(10, -8, 1), 0x7B, "BPF_STX_MEM(DW)"),
             (jmp(JNE, 7, -3, imm=0), 0x55, "BPF_JNE_IMM"), (call_helper(5), 0x85, "BPF_CALL"),
             (EXIT_INS, 0x95, "BPF_EXIT")]
    for ins, want, name in cases:
        assert ins.opcode == want, f"{name}: got 0x{ins.opcode:02x} want 0x{want:02x}"
    print(f"[编码自检] {len(cases)} 条指令 opcode 与 BPF 助记符常量全部一致")

    prog = prog_count_events()
    raw = b"".join(i.encode() for i in prog)
    back = [Ins.decode(raw[i:i + 8]) for i in range(0, len(raw), 8)]
    print(f"[编码] {len(prog)} 条指令 → {len(raw)} 字节(定长 8),解码往返一致 = {back == prog}")
    for i, ins in enumerate(prog[:4]):
        print(f"   pc={i} opcode=0x{ins.opcode:02x} class={ins.cls} "
              f"dst=r{ins.dst} src=r{ins.src} off={ins.offset} imm={ins.imm}")

    for name, p in [("count_events(合法)", prog), ("uninit_read", prog_uninit()),
                    ("stack_oob(-520)", prog_stack_oob()), ("unbounded_loop", prog_unbounded())]:
        ok, why = verify(p)
        print(f"[验证器] {name:20s} -> {'PASS  ' if ok else 'REJECT'} ({why})")

    ctx = {"map_values": [0], "time_ns": 1699000000000000000}
    print(f"[执行] prog_count_events() = {run(prog, ctx)}  (期望 100:循环体每次 +1)")


if __name__ == "__main__":
    main()
