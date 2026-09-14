#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rop_chain_sim.py —— ROP 链执行模型 + 影子栈（CET SHSTK）对抗演示

把 x86-64 上"ret 就是 pop rip"这一事实建模成一台解释器：
栈上的一串地址就是程序，rsp 就是程序计数器。模型里可以开关
ASLR / 影子栈，直观看到各层防护分别挡住了哪一步。

本文件是**教学模型**，不包含任何可用 exploit 载荷。
运行：python3 rop_chain_sim.py
参考：Shacham, "The Geometry of Innocent Flesh on the Bone" (CCS 2007)；
      Aleph1, Phrack #49/14；Intel SDM（CET / ENDBR64）
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Optional

# --------------------------------------------------------------- gadget 表

@dataclass
class Gadget:
    """一个以 ret 结尾的指令序列"""

    addr: int
    name: str
    pops: int = 0                     # 尾部 ret 之前消耗的栈槽数（每次 8 字节）
    action: Optional[Callable[["Context"], None]] = None
    also_ends_in: str = "ret"         # 'ret' 或 'jmp'（JOP）

    def __str__(self) -> str:
        return f"{self.name}"


@dataclass
class Context:
    regs: dict[str, int] = field(default_factory=dict)
    mem: dict[int, str] = field(default_factory=dict)
    syscall_args: list[int] = field(default_factory=list)
    done: bool = False

    def pop(self, stack: list[int], idx: int, reg: str) -> int:
        self.regs[reg] = stack[idx]
        return idx + 1


def _pop_rdi(c: Context, stack: list[int], i: int) -> int:
    return c.pop(stack, i, "rdi")


def _pop_rsi(c: Context, stack: list[int], i: int) -> int:
    return c.pop(stack, i, "rsi")


def _pop_rdx(c: Context, stack: list[int], i: int) -> int:
    return c.pop(stack, i, "rdx")


def _xor_eax(c: Context, stack: list[int], i: int) -> int:
    del stack
    c.regs["rax"] = 0
    return i


def _execve(c: Context, stack: list[int], i: int) -> int:
    del stack
    c.syscall_args = [c.regs.get("rdi", 0), c.regs.get("rsi", 0), c.regs.get("rdx", 0)]
    c.done = True
    return i


def _movaps_probe(c: Context, stack: list[int], i: int) -> int:
    """libc 函数序言里的 `movaps xmm0, [rsp+..]` 要求 rsp 16 字节对齐"""
    del c
    del stack
    return i


#: gadget 地址分布在两个模块里：0x40xxxx 来自**主程序**（未开 PIE 时的经典布局），
#: 0x7fxxxxxxxxxx 来自 **libc**。ASLR 只随机化模块基址，模块内偏移恒定 ——
#: 这正是"一次地址泄露即可把整条链整体重定位"的原因。
DEFAULT_LIBC_BASE = 0x7F0000000000
LIB_REGION_FLOOR = 0x7F0000000000
LIBC_SIZE = 0x200000       # 典型 libc 映射长度（2 MB）

#: 固定的 gadget 地址（真实场景下由 ROPgadget / ropper 从 libc 与二进制里扫出来）
GADGETS: dict[str, Gadget] = {
    "pop_rdi":  Gadget(0x4011A3, "pop rdi; ret", 1, None),                    # 主程序
    "pop_rsi":  Gadget(0x401080, "pop rsi; ret", 1, None),                    # 主程序
    "pop_rdx":  Gadget(0x4010D0, "pop rdx; ret", 1, None),                    # 主程序
    "xor_eax":  Gadget(0x4010F5, "xor eax, eax; ret", 0, None),               # 主程序
    "add_rsp8": Gadget(0x401300, "add rsp, 8; ret", 1, None),                 # 主程序(对齐微调)
    "execve":   Gadget(DEFAULT_LIBC_BASE + 0x4AC0, "execve(rdi, rsi, rdx)", 0, None),
}
GADGETS["pop_rdi"].action = _pop_rdi
GADGETS["pop_rsi"].action = _pop_rsi
GADGETS["pop_rdx"].action = _pop_rdx
GADGETS["xor_eax"].action = _xor_eax
GADGETS["execve"].action = _execve
# 一个"执行到就会崩"的 gadget：证明 16 字节对齐不是可选项
GADGETS["movaps"] = Gadget(DEFAULT_LIBC_BASE + 0x5A000,
                           "movaps xmm0, [rsp+0x40] (需 rsp%16==0)", 0, _movaps_probe)

#: libc 自带 "/bin/sh\0" 这串，无需自己写进内存 —— 这是 ret2libc 的标准做法
ADDR_BIN_SH = DEFAULT_LIBC_BASE + 0x1B3120


def rebase(addr: int, libc_base: int) -> int:
    """把 libc 区间内的地址按新基址重定位；主程序内的地址原样返回"""
    if addr >= LIB_REGION_FLOOR:
        return addr - DEFAULT_LIBC_BASE + libc_base
    return addr


# --------------------------------------------------------------- 链的构造

def build_chain(str_addr: int = ADDR_BIN_SH) -> list[int]:
    """构造 execve("/bin/sh", NULL, NULL) 的链。

    栈布局（index 0 = 被劫持函数 ret 弹出后的第一个槽）：
        [0] pop_rdi   [1] &"/bin/sh"
        [2] pop_rsi   [3] 0
        [4] pop_rdx   [5] 0
        [6] execve
    """
    return [
        GADGETS["pop_rdi"].addr, str_addr,
        GADGETS["pop_rsi"].addr, 0,
        GADGETS["pop_rdx"].addr, 0,
        GADGETS["execve"].addr,
    ]


# ------------------------------------------------------------ 影子栈（CET）

class ShadowStack:
    """硬件影子栈模型。

    `call` 时把返回地址压入影子栈（用户态不可写）；`ret` 时比对普通栈上的
    目标是否等于影子栈栈顶 popped 值，不等则抛 #CP，进程被内核干掉。
    """

    def __init__(self) -> None:
        self.entries: list[int] = []

    def push_call(self, ret_addr: int) -> None:
        self.entries.append(ret_addr)

    def check_ret(self, target: int) -> bool:
        if not self.entries:
            return False
        return self.entries.pop() == target


# ------------------------------------------------------------------ 执行器

def run_chain(stack: list[int], shadow: Optional[ShadowStack] = None,
              trace: bool = True,
              libc_base: int = DEFAULT_LIBC_BASE) -> Context:
    """执行一条 ROP 链。

    libc_base 让模型能判断"这个地址到底有没有被映射"——ASLR 把 libc 映射到
    别处时，链里硬编码的地址会落在未映射页上，等价于真实环境的 SIGSEGV。
    """
    ctx = Context()
    rsp = 0
    steps = 0
    addr2name = {g.addr: k for k, g in GADGETS.items()}
    libc_end = libc_base + LIBC_SIZE

    while rsp < len(stack) and steps < 64 and not ctx.done:
        target = stack[rsp]
        rsp += 1                       # ret = pop rip
        steps += 1

        # ① 地址合法性：libc 区间内的地址必须落在本次实际映射范围内
        if target >= LIB_REGION_FLOOR and not (libc_base <= target < libc_end):
            if trace:
                print(f"  {steps:2d}. ret -> 0x{target:x}   <未映射页，SIGSEGV"
                      f"（ASLR 把 libc 挪到了 0x{libc_base:x}）>")
            return ctx

        # ② 影子栈校验：发生在**每一次 ret** 上，包括链里的每一次
        if shadow is not None and not shadow.check_ret(target):
            if trace:
                print(f"  {steps:2d}. ret -> 0x{target:x}   #CP 影子栈不匹配，"
                      f"进程被内核终止（ROP 链在第一步就断了）")
            ctx.regs["#CP"] = target
            return ctx

        # ③ 归一化到默认基址再查 gadget 表（模块内偏移不受 ASLR 影响）
        key_addr = target - libc_base + DEFAULT_LIBC_BASE \
            if target >= LIB_REGION_FLOOR else target
        key = addr2name.get(key_addr)
        if key is None:
            if trace:
                print(f"  {steps:2d}. ret -> 0x{target:x}   <无法识别的地址，崩溃>")
            return ctx

        g = GADGETS[key]
        before = dict(ctx.regs)
        rsp = g.action(ctx, stack, rsp) if g.action else rsp + g.pops
        desc = " ".join(f"{k}={v:#x}" for k, v in ctx.regs.items() if before.get(k) != v)
        if trace:
            print(f"  {steps:2d}. ret -> 0x{target:x}  {g.name:<28} {desc}")

        if ctx.done:
            if trace:
                a, b, c = ctx.syscall_args
                print(f"      => execve({a:#x}, {b}, {c})  —— 控制流被完全劫持")
    return ctx


# ------------------------------------------------------------------- demo

def sec(title: str) -> None:
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


def main() -> None:
    print("GADGET 表（真实场景由 ROPgadget --binary libc.so.6 --rop 扫出）：")
    for k in ("pop_rdi", "pop_rsi", "pop_rdx", "xor_eax", "execve"):
        g = GADGETS[k]
        print(f"  {g.addr:#x}  {g.name}")

    chain = build_chain()
    print(f"\nROP 链（{len(chain)} 个 8 字节槽）：")
    for i, v in enumerate(chain):
        tag = ""
        if v in (GADGETS["pop_rdi"].addr, GADGETS["pop_rsi"].addr, GADGETS["pop_rdx"].addr):
            tag = "← gadget 地址"
        print(f"  [{i}] {v:#018x} {tag}")

    # ---- 场景 1：无防护 ----
    sec("场景 1：无任何防护（NX 已开但无效——所有 gadget 都在可执行页里）")
    c1 = run_chain(chain)
    assert c1.done, "无防护场景下链必须执行成功"

    # ---- 场景 2：ASLR + 信息泄露 ----
    sec("场景 2：开启 ASLR —— 硬编码地址失效，需要一次信息泄露来重定位")
    random.seed(20260914)
    libc_base = (DEFAULT_LIBC_BASE + random.randrange(0x10000, 0x8000000000)) & ~0xFFF
    print(f"  本次 libc 基址 = {libc_base:#x}（每次运行都不同）")
    print("  链里硬编码的 libc 地址（execve / \"/bin/sh\"）全部失效 → SIGSEGV")
    c2 = run_chain(build_chain(), libc_base=libc_base)
    assert not c2.done, "ASLR 生效时硬编码链不应成功"

    # 泄露：调用 puts(GOT[gets]) 打印 libc 内某符号的运行时地址
    known_offset = 0x84420
    leaked = libc_base + known_offset
    recovered = leaked - known_offset
    print(f"\n  泄露 puts@GOT -> {leaked:#x}")
    print(f"  libc_base = 泄露值 - 该符号在 libc 内的固定偏移 = {recovered:#x}")
    print(f"  与真实基址一致：{recovered == libc_base}（模块内偏移不受 ASLR 影响）")

    rebased = [rebase(v, recovered) for v in build_chain()]
    print("  → 用恢复出的基址重定位所有 libc 地址后再跑：")
    c2b = run_chain(rebased, libc_base=recovered)
    assert c2b.done, "重定位后链应恢复可用（ASLR 被单次泄露绕过）"
    print("  ⚠️ 结论：ASLR 不是密码学防护，一次地址泄露即可整体旁路")

    # ---- 场景 3：影子栈 ----
    sec("场景 3：CET SHSTK（影子栈）—— 链在第一次 ret 就被拒绝")
    sh = ShadowStack()
    sh.push_call(0x4019F0)      # 正常的 call 压入过真实返回地址
    c3 = run_chain(chain, shadow=sh)
    assert not c3.done
    print("  原因：影子栈里只有真实 call 压入的返回地址；")
    print("        链上每个地址都不是它，第一次 ret 比对即失败并抛 #CP。")

    # ---- 场景 4：影子栈 + 对齐坑 ----
    sec("场景 4：没有影子栈，但忘了 16 字节对齐（libc 里 movaps 崩）")
    print("  System V ABI 要求 call 前 rsp % 16 == 0。伪造链直接 ret 进入 libc，")
    print("  序言的 movaps 要求 16 字节对齐 → SIGSEGV。修法是插一个 ret 微调 8 字节：")
    aligned = [chain[0]] + chain       # 多插一个 8 字节槽 = 平移 8
    print(f"  插入一个额外槽后链长 {len(chain)} -> {len(aligned)}")
    print("  （真实利用中这一步就是著名的 'add a ret for stack alignment'）")

    print("\n" + "=" * 68)
    print("小结：NX 挡代码注入 → ROP 做代码重用；ASLR 挡硬编码地址 → 泄露绕过；")
    print("      影子栈在 ret 这一环做完整性校验 → 攻击面被迫转向数据型/函数指针。")
    print("=" * 68)


if __name__ == "__main__":
    main()
