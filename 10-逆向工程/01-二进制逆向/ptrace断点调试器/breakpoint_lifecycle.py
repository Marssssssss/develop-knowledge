#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""breakpoint_lifecycle.py —— 软件断点生命周期的可执行模型（跨平台）

C 版 mini_dbg.c 必须跑在 Linux 上；本文件用一台"玩具机"把 ptrace 断点的
四个步骤（埋点 → 命中 → 修复 → 复位）完整建模，可在任何平台运行，
并顺带演示两种实现错误的后果：

  * 错误 A：命中后不回退 PC  → 从指令中间开始执行，语义错乱
  * 错误 B：命中后不恢复原字节 → 单步再次踩到 0xCC，死循环

正确实现的结果必须与"不断点直接跑"的参考执行逐字节一致 —— 这是所有
调试器最核心的不变量（断点对程序语义必须透明）。

运行：python3 breakpoint_lifecycle.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

# ------------------------------------------------------------ 玩具指令集

LOADI = 0x10   # acc = imm8
ADDI  = 0x20   # acc += imm8
MULI  = 0x30   # acc *= imm8
JNZ   = 0x40   # if acc != 0: pc = imm8
OUT   = 0x50   # 输出 acc
HALT  = 0xF0   # 停机

INT3 = 0xCC    # 断点指令（单字节）

OPNAME = {LOADI: "loadi", ADDI: "addi", MULI: "muli",
          JNZ: "jnz", OUT: "out", HALT: "halt", INT3: "int3"}

#: 每条指令 2 字节（opcode, arg）——真实 x86 变长，这里固定长度只为让模型干净
INS_LEN = 2


# ------------------------------------------------------------------ 机器

@dataclass
class Machine:
    """一台只有 acc / pc / 输出缓冲区的小机器"""

    mem: bytearray
    acc: int = 0
    pc: int = 0
    out: list[int] = field(default_factory=list)
    steps: int = 0
    halted: bool = False

    def fetch_op(self) -> int:
        return self.mem[self.pc]

    def step(self) -> tuple[str, bool]:
        """执行一条指令，返回 (痕迹, 是否陷入断点)。

        忠实建模：0xCC 会被真正"执行"，PC 前进 1 字节后陷入 trap ——
        这正是真实 x86 上 INT3 为**单字节**指令的直接后果，
        也是"修复时必须把 PC 回退 1 字节"的唯一原因。
        """
        if self.halted:
            return ("halted", False)

        addr = self.pc
        op = self.fetch_op()

        if op == INT3:
            self.pc = addr + 1
            return (f"0x{addr:02x}: int3  -> trap, pc=0x{self.pc:02x}", True)

        arg = self.mem[addr + 1]
        self.steps += 1

        if op == LOADI:
            self.acc = arg
        elif op == ADDI:
            self.acc = (self.acc + arg) & 0xFF
        elif op == MULI:
            self.acc = (self.acc * arg) & 0xFF
        elif op == JNZ:
            if self.acc != 0:
                self.pc = arg
                return (f"0x{addr:02x}: jnz 0x{arg:02x} -> taken, pc=0x{self.pc:02x}", False)
        elif op == OUT:
            self.out.append(self.acc)
        elif op == HALT:
            self.halted = True
            self.pc = addr + INS_LEN
            return (f"0x{addr:02x}: halt", False)
        else:
            raise ValueError(f"非法 opcode 0x{op:02x} @ 0x{addr:02x}（PC 落在指令中间）")

        self.pc = addr + INS_LEN
        return (f"0x{addr:02x}: {OPNAME[op]:<5} {arg}  -> acc={self.acc} pc=0x{self.pc:02x}", False)


# --------------------------------------------------------------- 断点管理

@dataclass
class Breakpoint:
    addr: int
    orig: Optional[int] = None
    armed: bool = False

    def arm(self, mem: bytearray) -> None:
        """埋点：保存原始字节，写入 0xCC（对应 PTRACE_PEEKTEXT + POKETEXT）"""
        self.orig = mem[self.addr]
        mem[self.addr] = INT3
        self.armed = True

    def disarm(self, mem: bytearray) -> None:
        """摘点：原始字节写回"""
        assert self.orig is not None
        mem[self.addr] = self.orig
        self.armed = False

    def rearm(self, mem: bytearray) -> None:
        mem[self.addr] = INT3
        self.armed = True


# ------------------------------------------------------------------ 执行

TraceHook = Callable[[str], None]
FailMode = Optional[str]   # None | 'no_rewind' | 'no_restore'


def run_program(mem: bytearray, bp: Optional[Breakpoint] = None,
                trace: TraceHook = lambda _: None,
                fail: FailMode = None,
                max_steps: int = 200) -> Machine:
    """带（或不带）断点地执行程序。

    fail 参数用于演示"故意写错"的实现会怎样崩溃：
      'no_rewind'  —— 命中后不回退 PC
      'no_restore' —— 命中后不恢复原字节
    """
    m = Machine(mem=bytearray(mem))   # 拷贝，保证每次运行互不污染
    if bp is not None:
        bp = Breakpoint(bp.addr)
        bp.arm(m.mem)

    hits = 0
    while not m.halted and m.steps < max_steps:
        text, trapped = m.step()
        trace("  " + text)

        if not trapped:
            continue
        if bp is None:
            trace("     !! 未托管 0xCC：等价于真实环境里没有调试器，进程会崩")
            break

        hits += 1
        trace(f"  ** 断点命中 #{hits}: 陷入 trap 于 pc=0x{m.pc:02x}（已越过 0xCC 1 字节）**")

        # 步骤 1：恢复原字节（错误 B：跳过此步 → 单步仍踩到 0xCC，死循环）
        if fail != "no_restore":
            bp.disarm(m.mem)
            trace(f"     [1] 恢复原字节 mem[0x{bp.addr:02x}]={bp.orig:#04x}")
        else:
            trace("     [1] (错误实现) 不恢复原字节")

        # 步骤 2：PC 回退 —— INT3 是单字节指令，trap 时 PC 已前进 1
        if fail == "no_rewind":
            trace(f"     [2] (错误实现) 不回退 PC，pc 仍为 0x{m.pc:02x}")
        else:
            m.pc = bp.addr
            trace(f"     [2] PC 回退到 0x{m.pc:02x}")

        # 步骤 3：执行那一条原始指令（真实调试器用 PTRACE_SINGLESTEP）
        one, one_trapped = m.step()
        trace("     [3] single-step：" + one)

        # 错误 B 的下场：单步又踩中同一个 0xCC，永远走不出去
        if one_trapped:
            hits += 1
            trace(f"     !!! 单步再次踩中 0xCC（第 {hits} 次）—— 原字节未恢复，"
                  f"断点自锁，调试器光标纹丝不动")
            if hits >= 3:
                trace("     !!! 判定死循环，终止演示")
                break
            m.pc = bp.addr      # 回到断点处，重复同一个循环
            continue

        # 步骤 4：重新埋点
        if fail != "no_restore":
            bp.rearm(m.mem)
            trace(f"     [4] 重新埋入 0xCC @ 0x{bp.addr:02x}")
        else:
            trace("     [4] (错误实现) 内存中仍是 0xCC")

    return m


# ------------------------------------------------------------------- demo

def build_program() -> bytearray:
    """目标程序： 3*4 + 5 = 17，输出后停机"""
    code = [
        (LOADI, 3),
        (MULI, 4),
        (ADDI, 5),
        (OUT, 0),
        (HALT, 0),
    ]
    mem = bytearray(0x20)
    for i, (op, arg) in enumerate(code):
        mem[i * INS_LEN] = op
        mem[i * INS_LEN + 1] = arg
    return mem


def disasm(mem: bytes) -> None:
    print("反汇编：")
    for i in range(0, len(mem) - 1, INS_LEN):
        op = mem[i]
        if op in (0, None):
            continue
        name = OPNAME.get(op, f"?{op:#04x}")
        print(f"  0x{i:02x}: {name:<5} {mem[i + 1]}")


def main() -> None:
    mem = build_program()
    disasm(mem)

    # ---- 参考运行（无断点） ----
    print("\n=== 参考运行（无断点）===")
    ref = run_program(mem, bp=None, trace=print)
    print(f"结果：输出={ref.out} steps={ref.steps} halted={ref.halted}")

    # ---- 正确实现断点：语义必须与参考完全一致 ----
    print("\n=== 正确实现（埋点 @ 0x04 = ADDI 指令）===")
    bp = Breakpoint(0x04)
    got = run_program(mem, bp=bp, trace=print)
    print(f"结果：输出={got.out} steps={got.steps} halted={got.halted}")
    assert got.out == ref.out, f"断点破坏了语义！{got.out} != {ref.out}"
    assert got.steps == ref.steps, f"步数也不该变！{got.steps} != {ref.steps}"
    print("✅ 不变量成立：断点对程序语义透明（输出与步数均与参考一致）")

    # ---- 错误 A：不回退 PC ----
    print("\n=== 错误 A：命中后不回退 PC ===")
    try:
        bad = run_program(mem, bp=Breakpoint(0x04), trace=print, fail="no_rewind")
        print(f"结果：输出={bad.out} steps={bad.steps} halted={bad.halted}")
        print(f"❌ 输出 {bad.out} ≠ 参考 {ref.out} —— 指令边界错位")
    except ValueError as e:
        print(f"❌ 抛异常：{e}")
        print("   原因：0xCC 占 1 字节，PC=0x05 落在 ADDI 的参数字节上，"
              "把数据当 opcode 解 —— 真实 CPU 上表现为 SIGILL")

    # ---- 错误 B：不恢复原字节 ----
    print("\n=== 错误 B：命中后不恢复原字节（死循环）===")
    stuck = run_program(mem, bp=Breakpoint(0x04), trace=print, fail="no_restore")
    print(f"结果：输出={stuck.out} halted={stuck.halted} —— 单步永远走不出 0xCC")
    print("   真实调试器若犯此错，表现就是 gdb 里按 stepi 后光标纹丝不动")


if __name__ == "__main__":
    main()
