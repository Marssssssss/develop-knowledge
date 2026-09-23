"""651 · Windows x64 展开数据（.pdata / .xdata）演示。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import winunwind as W  # noqa: E402


def banner(t):
    print("\n== %s ==" % t)


def main():
    banner("1. 一份典型 prolog 的展开数据")
    prolog = W.UnwindInfo(
        version=1, flags=0, prolog_size=5,
        codes=[W.UnwindCode(offset=5, op=W.UWOP_ALLOC_SMALL, info=7),
               W.UnwindCode(offset=1, op=W.UWOP_PUSH_NONVOL, info=5)],
    )
    print("  prolog:  push rbp        (offset 1)")
    print("           sub rsp, 0x40   (offset 5, info=7 -> 7*8+8=64)")
    print("  raw =", prolog.to_bytes().hex())
    print("  hit : ", [(c.offset, c.op, c.info) for c in
                       W.UnwindInfo.from_bytes(prolog.to_bytes()).codes])

    banner("2. rip 落在 prolog 的不同位置")
    for rip in (0, 1, 5):
        m, applied = W.Machine(rsp=0x1000, stack={0x1000: 0xAABB, 0x1040: 0xCCDD}), None
        info = W.UnwindInfo(version=1, flags=0, prolog_size=5, codes=prolog.codes)
        applied = W.unwind_prolog(m, info, rip)
        print("  rip+%d -> 撤销槽 %s; rsp=0x%x rbp=0x%x"
              % (rip, applied, m.regs["RSP"], m.regs["RBP"]))

    banner("3. 带帧指针：sub rsp,0x40 ; lea rbp,[rsp+0x20]")
    fp = W.UnwindInfo(version=1, flags=0, prolog_size=8, frame_register=5, frame_offset=2,
                      codes=[W.UnwindCode(offset=8, op=W.UWOP_SET_FPREG),
                             W.UnwindCode(offset=4, op=W.UWOP_ALLOC_SMALL, info=7),
                             W.UnwindCode(offset=1, op=W.UWOP_PUSH_NONVOL, info=5)])
    m = W.Machine(rsp=0x1000, stack={0x1080: 0x9999})
    m.regs["RBP"] = 0x1060
    W.unwind_full(m, fp)
    print("  frame_register=RBP scaled=2 (16*2=32)")
    print("  SET_FPREG 把 rsp 还原为 RBP-32 = 0x%x" % (0x1060 - 32))
    print("  整段撤销后 rsp=0x%x rbp=0x%x" % (m.regs["RSP"], m.regs["RBP"]))

    banner("4. 三种栈分配编码")
    for label, info in [
        ("ALLOC_SMALL info=15", W.UnwindInfo(codes=[W.UnwindCode(op=W.UWOP_ALLOC_SMALL, info=15)])),
        ("ALLOC_LARGE info=0 slot=100", W.UnwindInfo(
            codes=[W.UnwindCode(op=W.UWOP_ALLOC_LARGE, info=0), W.UnwindCode(word=100)])),
        ("ALLOC_LARGE info=1 0x11234", W.UnwindInfo(
            codes=[W.UnwindCode(op=W.UWOP_ALLOC_LARGE, info=1),
                   W.UnwindCode(word=0x1234), W.UnwindCode(word=0x0001)])),
    ]:
        mm = W.Machine(rsp=0)
        W.unwind_full(mm, info)
        print("  %-28s -> %d 字节" % (label, mm.regs["RSP"]))

    banner("5. chained info 与槽位公式")
    for n in (1, 3, 4, 5):
        u = W.UnwindInfo(codes=[W.UnwindCode()] * n)
        print("  CountOfCodes=%d -> 数组补齐到 %d 槽，chained 落在 %d"
              % (n, n + (n & 1), u.chained_slot))


if __name__ == "__main__":
    main()
