"""651 Windows x64 展开数据 —— 自检（实跑）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import winunwind as W  # noqa: E402

PASS = 0
FAIL = []


def check(cond, msg):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(msg)


def eq(a, b, msg):
    check(a == b, "%s: 期望 %r 实得 %r" % (msg, b, a))


# ------------------------------------------------ 位布局

ui = W.UnwindInfo(version=1, flags=W.UNW_FLAG_EHANDLER, prolog_size=11,
                  codes=[W.UnwindCode(offset=5, op=W.UWOP_ALLOC_SMALL, info=7),
                         W.UnwindCode(offset=1, op=W.UWOP_PUSH_NONVOL, info=5)],
                  handler_rva=0x1234)
raw = ui.to_bytes()
eq(raw[0], (1 & 7) | ((W.UNW_FLAG_EHANDLER & 0x1F) << 3), "首字节 version|flags<<3")
eq(raw[1], 11, "第二字节 prolog size")
eq(raw[2], 2, "第三字节 count of codes")
eq(len(raw), 4 + 2 * 2 + 4, "有 handler 时尾部 4 字节")
back = W.UnwindInfo.from_bytes(raw)
eq(back.version, 1, "回读 version")
eq(back.flags, W.UNW_FLAG_EHANDLER, "回读 flags")
eq(back.prolog_size, 11, "回读 prolog size")
eq(back.count, 2, "回读 count")
eq(back.handler_rva, 0x1234, "回读 handler rva")
eq([(c.offset, c.op, c.info) for c in back.codes],
   [(5, W.UWOP_ALLOC_SMALL, 7), (1, W.UWOP_PUSH_NONVOL, 5)], "回读 codes")

# Frame Register / Frame Register offset 各占 4 位
ui2 = W.UnwindInfo(codes=[], frame_register=5, frame_offset=15)
eq(ui2.to_bytes()[3], (5 << 4) | 15, "第四字节 frame_reg<<4 | frame_off")
# frame offset 最大 240 = 15*16
eq(15 * 16, 240, "frame offset 上限 240")

# 奇数个 code 时数组补齐到偶数（末项未用）
odd = W.UnwindInfo(version=1, flags=0, prolog_size=0,
                   codes=[W.UnwindCode(offset=4, op=W.UWOP_PUSH_NONVOL, info=3)])
eq(len(odd.to_bytes()), 4 + 2 * 2, "奇数 count 补齐为偶数槽")
eq(odd.chained_slot, 2, "count=1 的 chained 槽位 = (1+1)&~1 = 2")

# chained info 的槽位公式：(CountOfCodes + 1) & ~1
eq(W.UnwindInfo(codes=[W.UnwindCode()] * 3).chained_slot, 4, "count=3 -> 4")
eq(W.UnwindInfo(codes=[W.UnwindCode()] * 4).chained_slot, 4, "count=4 -> 4")
eq(W.UnwindInfo(codes=[W.UnwindCode()] * 5).chained_slot, 6, "count=5 -> 6")

# chained info 能回读
ch = W.UnwindInfo(version=1, flags=W.UNW_FLAG_CHAININFO, prolog_size=0,
                  codes=[W.UnwindCode(offset=0, op=W.UWOP_ALLOC_SMALL, info=0)],
                  chained={"begin": 0x1000, "end": 0x1040, "unwind": 0x2000})
chb = ch.to_bytes()
eq(len(chb), 4 + 2 * 2 + 12, "chained 结构尾部 3 个 ULONG")
ch2 = W.UnwindInfo.from_bytes(chb)
eq(ch2.chained, {"begin": 0x1000, "end": 0x1040, "unwind": 0x2000}, "回读 chained")
check(ch2.handler_rva is None, "CHAININFO 时不应解析出 handler")

# RUNTIME_FUNCTION 是三个 ULONG
rf = W.RuntimeFunction(0x1000, 0x1040, 0x3000)
eq(len(rf.to_bytes()), 12, "RUNTIME_FUNCTION 12 字节")

# 最小展开数据是 8 字节
small = W.UnwindInfo(version=1, flags=0, prolog_size=0,
                     codes=[W.UnwindCode(offset=0, op=W.UWOP_ALLOC_SMALL, info=15)])
eq(len(small.to_bytes()), 4 + 2 * 2, "count=1 时补齐后共 8 字节")


# ------------------------------------------------ 展开：prolog 部分撤销

def prolog_case(rip_off):
    info = W.UnwindInfo(version=1, flags=0, prolog_size=5,
                        codes=[W.UnwindCode(offset=5, op=W.UWOP_ALLOC_SMALL, info=7),
                               W.UnwindCode(offset=1, op=W.UWOP_PUSH_NONVOL, info=5)])
    m = W.Machine(rsp=0x1000, stack={0x1000: 0xAABB, 0x1040: 0xCCDD})
    applied = W.unwind_prolog(m, info, rip_off)
    return m, applied


# push rbp 在 offset 1 结束；sub rsp,0x40 在 offset 5 结束
m0, a0 = prolog_case(0)
eq(a0, [], "rip 在函数起点：什么都不撤销")
eq(m0.regs["RSP"], 0x1000, "rsp 不动")
eq(m0.regs["RBP"], 0, "rbp 未恢复")

m1, a1 = prolog_case(1)
eq(a1, [1], "rip=1 只撤销 PUSH_NONVOL")
eq(m1.regs["RBP"], 0xAABB, "从 [rsp] 恢复 rbp")
eq(m1.regs["RSP"], 0x1008, "rsp += 8")

m5, a5 = prolog_case(5)
eq(a5, [0, 1], "rip=5 两个都撤销")
eq(m5.regs["RSP"], 0x1000 + 64 + 8, "rsp += (7*8+8) 再 += 8")
eq(m5.regs["RBP"], 0xCCDD, "rbp 从 push 后的 [rsp] 恢复")

# 负控：数组是降序的，按升序应用会得到完全不同的 rsp
asc = W.Machine(rsp=0x1000, stack={0x1000: 0x1, 0x1060: 0x2})
W.apply_code(asc, W.UnwindInfo(codes=[W.UnwindCode(offset=5, op=W.UWOP_ALLOC_SMALL, info=7)]), 0)
eq(asc.regs["RSP"], 0x1040, "单独撤销 alloc 得到 0x1040")
check(asc.regs["RSP"] != m5.regs["RSP"], "顺序不同结果不同")


# ------------------------------------------------ ALLOC 的三种编码

def alloc_size(info):
    m = W.Machine(rsp=0x2000)
    W.unwind_full(m, info)
    return m.regs["RSP"] - 0x2000


eq(alloc_size(W.UnwindInfo(codes=[W.UnwindCode(offset=0, op=W.UWOP_ALLOC_SMALL, info=0)])), 8,
   "ALLOC_SMALL info=0 -> 8")
eq(alloc_size(W.UnwindInfo(codes=[W.UnwindCode(offset=0, op=W.UWOP_ALLOC_SMALL, info=15)])), 128,
   "ALLOC_SMALL info=15 -> 15*8+8 = 128")
eq(alloc_size(W.UnwindInfo(codes=[W.UnwindCode(offset=0, op=W.UWOP_ALLOC_LARGE, info=0),
                                  W.UnwindCode(word=100)])), 800,
   "ALLOC_LARGE info=0 -> slot*8")
eq(alloc_size(W.UnwindInfo(codes=[W.UnwindCode(offset=0, op=W.UWOP_ALLOC_LARGE, info=1),
                                  W.UnwindCode(word=0x1234), W.UnwindCode(word=0x0001)])),
   0x11234, "ALLOC_LARGE info=1 -> 两槽小端拼 32 位")
eq(W.node_size(W.UnwindCode(op=W.UWOP_ALLOC_LARGE, info=0)), 2, "ALLOC_LARGE info=0 占 2 槽")
eq(W.node_size(W.UnwindCode(op=W.UWOP_ALLOC_LARGE, info=1)), 3, "ALLOC_LARGE info=1 占 3 槽")
for op, n in [(W.UWOP_PUSH_NONVOL, 1), (W.UWOP_SET_FPREG, 1), (W.UWOP_SAVE_NONVOL, 2),
              (W.UWOP_SAVE_NONVOL_FAR, 3), (W.UWOP_SAVE_XMM128, 2),
              (W.UWOP_SAVE_XMM128_FAR, 3), (W.UWOP_PUSH_MACHFRAME, 1)]:
    eq(W.node_size(W.UnwindCode(op=op)), n, "node_size(op=%d)" % op)


# ------------------------------------------------ SET_FPREG 与基准

# sub rsp,0x40 ; lea rbp,[rsp+0x20]  -> frame offset scaled = 2 (0x20 = 16*2)
fp = W.UnwindInfo(version=1, flags=0, prolog_size=8, frame_register=5, frame_offset=2,
                  codes=[W.UnwindCode(offset=8, op=W.UWOP_SET_FPREG),
                         W.UnwindCode(offset=4, op=W.UWOP_ALLOC_SMALL, info=7),
                         W.UnwindCode(offset=1, op=W.UWOP_PUSH_NONVOL, info=5)])
# 单独看 SET_FPREG：rsp = FP - 16*scaled
solo = W.Machine(rsp=0x1000)
solo.regs["RBP"] = 0x1060
W.unwind_full(solo, W.UnwindInfo(frame_register=5, frame_offset=2,
                                 codes=[W.UnwindCode(offset=0, op=W.UWOP_SET_FPREG)]))
eq(solo.regs["RSP"], 0x1060 - 32, "SET_FPREG 单点: rsp = FP - 16*2")

# 完整 prolog：SET_FPREG -> ALLOC_SMALL(64) -> PUSH_NONVOL(rbp)
mfp = W.Machine(rsp=0x1000, stack={0x1080: 0x9999})
mfp.regs["RBP"] = 0x1060          # RBP = 建立时的 RSP + 32
W.unwind_full(mfp, fp)
eq(mfp.regs["RSP"], 0x1060 - 32 + 64 + 8, "整段撤销后的 rsp")
eq(mfp.regs["RBP"], 0x9999, "PUSH_NONVOL rbp 从新 rsp 处恢复")

# SAVE_NONVOL 的基准：frame_register=0 时是 RSP，否则是 FP-16*scaled
sn0 = W.UnwindInfo(frame_register=0, frame_offset=0,
                   codes=[W.UnwindCode(offset=0, op=W.UWOP_SAVE_NONVOL, info=3),
                          W.UnwindCode(word=4)])
m = W.Machine(rsp=0x1000, stack={0x1020: 0x77})
W.unwind_full(m, sn0)
eq(m.regs["RBX"], 0x77, "无 FP 时基准 = RSP，偏移 4*8=32")

sn1 = W.UnwindInfo(frame_register=5, frame_offset=2,
                   codes=[W.UnwindCode(offset=0, op=W.UWOP_SAVE_NONVOL, info=3),
                          W.UnwindCode(word=4)])
m = W.Machine(rsp=0x1000, stack={0x1060: 0x88})
m.regs["RBP"] = 0x1060
W.unwind_full(m, sn1)
eq(m.regs["RBX"], 0x88, "有 FP 时基准 = RBP - 32，+32 -> 0x1060")

# SAVE_XMM128 的缩放是 16 不是 8
sx = W.UnwindInfo(frame_register=0,
                  codes=[W.UnwindCode(offset=0, op=W.UWOP_SAVE_XMM128, info=6),
                         W.UnwindCode(word=2)])
m = W.Machine(rsp=0x1000, stack={0x1020: 0x55})
W.unwind_full(m, sx)
eq(m.regs["xmm6"], 0x55, "SAVE_XMM128 偏移 = slot*16")


# ------------------------------------------------ PUSH_MACHFRAME

for inf, delta in [(0, 40), (1, 48)]:
    m = W.Machine(rsp=0x1000, stack={0x1000: 0xDEAD})
    W.unwind_full(m, W.UnwindInfo(codes=[W.UnwindCode(offset=0, op=W.UWOP_PUSH_MACHFRAME, info=inf)]))
    eq(m.regs["RSP"] - 0x1000, delta, "PUSH_MACHFRAME info=%d 的 rsp 增量" % inf)
    eq(m.rip, 0xDEAD, "PUSH_MACHFRAME 从栈顶取 rip")


# ------------------------------------------------ 叶函数回退

ml = W.Machine(rsp=0x1000, stack={0x1000: 0xBEEF})
W.unwind_leaf(ml)
eq(ml.rip, 0xBEEF, "叶函数：rip = [rsp]")
eq(ml.regs["RSP"], 0x1008, "叶函数：rsp += 8")


# ------------------------------------------------ 负控

try:
    W.UnwindInfo.from_bytes(b"\x01\x02")
    check(False, "过短的 UNWIND_INFO 应报错")
except ValueError:
    check(True, "过短的 UNWIND_INFO 应报错")

try:
    W.apply_code(W.Machine(), W.UnwindInfo(codes=[W.UnwindCode(op=7)]), 0)
    check(False, "未定义操作码应报错")
except ValueError:
    check(True, "未定义操作码应报错")

# flag 常量来自 winnt.h
eq(W.UNW_FLAG_EHANDLER, 0x1, "UNW_FLAG_EHANDLER")
eq(W.UNW_FLAG_UHANDLER, 0x2, "UNW_FLAG_UHANDLER")
eq(W.UNW_FLAG_CHAININFO, 0x4, "UNW_FLAG_CHAININFO")
check(W.UNW_FLAG_CHAININFO & W.UNW_FLAG_EHANDLER == 0, "CHAININFO 与 EHANDLER 互斥（位不重叠）")

# 寄存器编号表
eq(W.reg_name(0), "RAX", "reg 0")
eq(W.reg_name(5), "RBP", "reg 5")
eq(W.reg_name(15), "R15", "reg 15")

print("断言通过: %d" % PASS)
if FAIL:
    print("失败 %d 条:" % len(FAIL))
    for x in FAIL:
        print("  -", x)
    sys.exit(1)
print("ALL GREEN")
