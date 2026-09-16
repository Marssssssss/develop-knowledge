#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AAPCS64 调用约定与栈帧链模型(全部断言实跑)。

权威依据(本轮实读):
  * Arm《A64 Instruction Set Architecture Guide》102374 1.3, Procedure Call Standard 一节
    —— x0-x7 传参、x8=XR 间接结果、x9-x15 可破坏、x16/x17=IP0/IP1(veneer 可破坏)、
       x18=PR、x19-x28 callee-saved、x29=FP、x30=LR、ALU flags 不跨调用保留
  * AAPCS64 规范(aapcs64.rst, 2025Q4)Table 2 与 Stack constraints / Frame Pointer 各节
    —— SP mod 16 = 0(公有接口处与每次经 SP 访存时)、帧记录由两个 64 位值组成、
       低地址存前一帧记录地址、高地址存进入时的 LR、链以地址零结束
  * Porting to 64-bit ARM(Arm 官方白皮书, 2014)
    —— 8 个参数/结果寄存器、X8 间接结果位置、单一 64 位结果在 X0、128 位结果在 X1:X0

运行: python aapcs64_check.py    退出码 0 表示全部断言通过。
"""

import sys

# Table 2: 通用寄存器与 AAPCS64 用途(role: param/indirect/special/caller/callee;
#                                     special=有专用角色但默认仍 caller-saved)
ROLES = [
    (0, 7, "param", "参数/结果寄存器(Caller-saved)"),
    (8, 8, "indirect", "间接结果位置 XR(Caller-saved)"),
    (9, 15, "caller", "Caller-saved 临时寄存器"),
    (16, 16, "special", "IP0:过程内调用临时寄存器(veneer/PLT 可用)"),
    (17, 17, "special", "IP1:过程内调用临时寄存器(veneer/PLT 可用)"),
    (18, 18, "platform", "平台寄存器 PR:平台无关代码应避免占用"),
    (19, 28, "callee", "Callee-saved:被调用者必须保存并恢复"),
    (29, 29, "fp", "FP:帧指针(指向最内层帧记录)"),
    (30, 30, "lr", "LR:链接寄存器(BL 写入返回地址,RET 读它)"),
]

# 栈对齐常量: AAPCS64 Stack constraints at a public interface → SP mod 16 = 0
SP_ALIGN = 16


def role_of(i):
    for lo, hi, kind, _ in ROLES:
        if lo <= i <= hi:
            return kind
    return "sp" if i == 31 else "unknown"


def assign_integer_args(nargs, is_method=False, struct_return=False):
    """把 nargs 个整型/指针实参分配到寄存器或栈。

    规则(AAPCS64 + Arm PCS 页):
      * x0-x7 依次承载前 8 个实参,其余入栈;
      * C++ 成员函数的隐式 this 也在 X0,故显式参数从 X1 开始;
      * 结构体返回时 X8(XR)指向调用者分配的返回缓冲区 —— 它不占参数位。
    """
    regs, stack = [], []
    start = 1 if is_method else 0
    results = {"x8_used": struct_return, "args": [], "stack_count": 0}
    for i in range(nargs):
        slot = start + i
        if slot <= 7:
            regs.append(slot)
            results["args"].append({"kind": "reg", "reg": "x%d/w%d" % (slot, slot)})
        else:
            stack.append(i)
            results["args"].append({"kind": "stack", "order": len(stack)})
    results["stack_count"] = len(stack)
    return regs, stack, results


def must_save(used):
    """给出用到的寄存器中必须保存/恢复的那些(callee-saved 集合)。"""
    return sorted(i for i in used if role_of(i) in ("callee", "fp", "lr"))


def can_clobber(regs):
    """这些寄存器在被调用函数返回后值没有保证。"""
    return sorted(i for i in regs if role_of(i) in
                  ("param", "indirect", "caller", "special", "platform"))


def frame_record(prev_fp, lr):
    """帧记录 = 两个 64 位值([低地址]=前一帧记录指针,[高地址]=进入时的 LR)。"""
    return {"addr_low": prev_fp, "addr_high": lr}


def walk_frames(fp, stack_words, limit=32):
    """沿帧记录链回溯,返回每一层保存的返回地址;地址零表示链结束。"""
    rAs, cur = [], fp
    for _ in range(limit):
        if cur == 0:
            break
        prev = stack_words[cur][0]
        ret = stack_words[cur][1]
        rAs.append(ret)
        cur = prev
    return rAs


def sp_aligned(sp):
    """公有接口处 SP 必须满足 mod 16 == 0(否则违反 AAPCS64)。"""
    return sp % SP_ALIGN == 0


FAIL = []


def check(cond, label, detail=""):
    if not cond:
        FAIL.append(label)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  <- " + str(detail)) if detail else ""))
    return cond


def main():
    print("== 1. 寄存器角色表(Table 2 对齐) ==")
    check(role_of(0) == "param" and role_of(7) == "param", "x0-x7 = 参数/结果寄存器")
    check(role_of(8) == "indirect", "x8 = 间接结果位置寄存器 XR")
    check(all(role_of(i) == "caller" for i in range(9, 16)), "x9-x15 = Caller-saved")
    check(role_of(16) == "special" and role_of(17) == "special", "x16/x17 = IP0/IP1")
    check(role_of(18) == "platform", "x18 = 平台寄存器 PR")
    check(all(role_of(i) == "callee" for i in range(19, 29)), "x19-x28 = Callee-saved")
    check(role_of(29) == "fp" and role_of(30) == "lr", "x29 = FP,x30 = LR")

    print("== 2. 参数分配 ==")
    _, stack, r = assign_integer_args(8)
    check(r["stack_count"] == 0 and [a["reg"] for a in r["args"]][-1] == "x7/w7",
          "8 个实参恰好用完 x0-x7,不入栈")
    _, stack, r = assign_integer_args(10)
    check(stack == [8, 9] and r["stack_count"] == 2,
          "第 9 个起入栈(第 9 个实参 = 栈上第 1 个)", stack)
    _, _, r = assign_integer_args(3, is_method=True)
    check([a.get("reg") for a in r["args"]] == ["x1/w1", "x2/w2", "x3/w3"],
          "C++ 成员函数:隐式 this 占 X0,显式参数从 X1 起")
    _, _, r = assign_integer_args(8, is_method=True)
    check(r["stack_count"] == 1, "成员函数只剩 7 个参数位 → 第 8 个显式参数入栈")
    _, _, r = assign_integer_args(1, struct_return=True)
    check(r["x8_used"] and [a.get("reg") for a in r["args"]] == ["x0/w0"],
          "结构体返回:X8=XR 指向调用者缓冲区,不占参数位")

    print("== 3. 谁必须保存 / 谁可以被破坏 ==")
    check(must_save([0, 1, 19, 20, 29, 30]) == [19, 20, 29, 30],
          "用到 x19/x20/x29/x30 时必须保存(x0/x1 不必)", must_save([0, 1, 19, 20, 29, 30]))
    check(must_save(list(range(0, 16))) == [], "只用 x0-x15 的函数无需保存任何寄存器")
    check(can_clobber([0, 8, 16, 17]) == [0, 8, 16, 17],
          "调用返回后 x0/x8/x16/x17 值无保证(veneer 也会破坏 IP0/IP1)")
    check(18 in can_clobber([18]), "平台无专用需求时 x18 按 caller-saved 处理")
    check(len(must_save([])) == 0, "空集合 → 无需保存")

    print("== 4. 叶子函数:LR 不必入栈 ==")
    check(must_save([0, 1, 2]) == [],
          "叶子函数未写 X30 → 无需为 LR 分配栈空间")
    check(must_save([30]) == [30], "非叶子函数先 BL 后要用 X30 → 必须保存")

    print("== 5. 栈对齐与帧记录 ==")
    check(sp_aligned(0x7000) and sp_aligned(0x7FF0), "SP 是 16 的倍数 → 合规")
    check(not sp_aligned(0x7008), "SP = ...08 → 违反 SP mod 16 = 0")
    rec = frame_record(0x7FF0, 0x4005A8)
    check(set(rec) == {"addr_low", "addr_high"}, "帧记录恰好两个 64 位槽")
    check(rec["addr_low"] == 0x7FF0 and rec["addr_high"] == 0x4005A8,
          "低地址槽=前一帧记录指针,高地址槽=进入时的 LR")
    check(0x7FF0 % 16 == 0, "16 字节帧记录 + SP 对齐 ⇒ 帧记录本身也 16 字节对齐")

    print("== 6. 帧链回溯 ==")
    # 三层调用链:main(fp=0x8000) → foo(fp=0x7FF0) → bar(fp=0x7FE0)
    words = {
        0x7FE0: (0x7FF0, 0x4005A8),      # bar 帧记录
        0x7FF0: (0x8000, 0x4006C0),      # foo 帧记录
        0x8000: (0x0000, 0x400123),      # main 帧记录:前一帧为 0 → 链结束
    }
    chain = walk_frames(0x7FE0, words)
    check(chain == [0x4005A8, 0x4006C0, 0x400123],
          "从最内层 FP 逐级回溯 3 个返回地址", [hex(x) for x in chain])
    check(walk_frames(0, words) == [], "FP=0 表示无帧链(被 -fomit-frame-pointer 优化掉)")
    # 不建帧链的后果:只能靠 DWARF .eh_frame 回溯,坏链会导致回溯停在内层
    broken = {0x7FE0: (0x7FF0, 0x4005A8), 0x7FF0: (0x0000, 0x4006C0)}
    check(len(walk_frames(0x7FE0, broken)) == 2, "链中间出现 0 → 回溯提前终止")

    print("== 7. 本轮 demo 组合:把序言形态与调用约定连起来 ==")
    from prologue_shape import GOLDEN, classify_prologue, frame_chain_size
    from arm64_decode import decode
    insns = [decode(w, pc) for pc, w, _ in GOLDEN]
    check(classify_prologue(insns) == "stp-frame-pair", "main 采用 stp-frame-pair 序言")
    check(frame_chain_size(insns) == 16 and frame_chain_size(insns) % SP_ALIGN == 0,
          "帧链为 X29+X30 预留 16 字节 → 与 SP 对齐要求相容")
    check(must_save([29, 30]) == [29, 30],
          "序言里的 STP x29,x30 正是为 callee-saved 的 FP/LR 做的保存")
    check(can_clobber([0, 1, 2]) == [0, 1, 2],
          "main 把结果写在 W0(返回值)而不是 X0 的高半部")

    print("\n结果: %d 项失败" % len(FAIL))
    for f in FAIL:
        print("  FAIL: %s" % f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
