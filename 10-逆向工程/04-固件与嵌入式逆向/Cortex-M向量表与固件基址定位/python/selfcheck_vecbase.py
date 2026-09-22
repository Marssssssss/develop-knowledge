"""demo594 自检：VTOR 位域、向量表顺序、基址打分。"""

import sys

import vecbase as vb

PASS = 0
FAIL = []


def ck(c, label):
    global PASS
    if c:
        PASS += 1
    else:
        FAIL.append(label)


def eq(got, want, label):
    ck(got == want, "%s (got=%r want=%r)" % (label, got, want))


BASE = 0x08000000
RAM_LO, RAM_HI = 0x20000000, 0x20010000
img = vb.build_sample_image(BASE, n_ext=8, ram_lo=RAM_LO, ram_hi=RAM_HI, n_code=16)

# ---------------------------------------------------------------- A. VTOR 位域
eq(vb.SCB_VTOR_TBLOFF_POS, 7, "A1 TBLOFF 起始位 7")
eq(vb.SCB_VTOR_TBLBASE_POS, 29, "A2 TBLBASE 起始位 29")
eq(vb.MIN_VECTOR_ALIGN, 128, "A3 由此得到 128 字节下限")
eq(vb.SCB_VTOR_TBLOFF_MASK_DEFAULT, 0xFFFFFF80, "A4 默认掩码")
eq(vb.SCB_VTOR_TBLOFF_MASK_SMALL, 0x1FFFFF80, "A5 窄掩码")
eq(vb.SCB_VTOR_TBLBASE_MASK, 0x20000000, "A6 TBLBASE 掩码")
ck(vb.SCB_VTOR_TBLOFF_MASK_DEFAULT & 0x7F == 0, "A7 低 7 位不可写")
eq(vb.vtor_decode(vb.vtor_value(0x08002000, False))["tbloff"], 0x08002000, "A8 编码解码往返")
eq(vb.vtor_decode(vb.vtor_value(0x08002000, True))["tblbase"], 1, "A9 TBLBASE 置位")
eq(vb.vtor_decode(vb.vtor_value(0x08002000, False))["tblbase"], 0, "A10 不置位（对偶）")
eq(vb.vtor_decode(vb.vtor_value(0x08002004, False))["tbloff"], 0x08002000,
   "A11 低 7 位被吃掉，未对齐基址无法精确表达")

# ---------------------------------------------------------------- B. 可重定位性
ck(vb.vtor_relocatable(0x08000000, "armv7-m"), "B1 128 对齐可重定位")
ck(not vb.vtor_relocatable(0x08000004, "armv7-m"), "B2 未对齐不行")
ck(vb.vtor_relocatable(0x20000000, "armv8-m-mainline"), "B3 RAM 里也能放")
ck(not vb.vtor_relocatable(0x08000000, "armv6-m"), "B4 ARMv6-M 没有 VTOR")
ck(vb.vtor_relocatable(0, "armv6-m"), "B5 ARMv6-M 只能在 0（对偶）")
eq(vb.PROFILE_HAS_VTOR["armv6-m"], False, "B6 armv6-m 无 VTOR")
eq(vb.PROFILE_HAS_VTOR["armv7-m"], True, "B7 armv7-m 有 VTOR")

# ---------------------------------------------------------------- C. 向量表布局
eq(vb.NVIC_EXTERNAL_OFFSET, 16, "C1 16 个系统异常")
eq(len(vb.SYSTEM_EXCEPTIONS), 16, "C2 名字表 16 项")
eq(vb.SYSTEM_EXCEPTIONS[0], "initial_sp", "C3 第 0 项是栈顶")
eq(vb.SYSTEM_EXCEPTIONS[1], "reset", "C4 第 1 项是 reset")
eq(vb.SYSTEM_EXCEPTIONS[2], "nmi", "C5 第 2 项 NMI")
eq(vb.SYSTEM_EXCEPTIONS[3], "hard_fault", "C6 第 3 项 HardFault")
eq(vb.SYSTEM_EXCEPTIONS[4], "mpu_fault", "C7 第 4 项 MemManage")
eq(vb.SYSTEM_EXCEPTIONS[5], "bus_fault", "C8 第 5 项 BusFault")
eq(vb.SYSTEM_EXCEPTIONS[6], "usage_fault", "C9 第 6 项 UsageFault")
eq(vb.SYSTEM_EXCEPTIONS[11], "svc", "C10 第 11 项 SVC")
eq(vb.SYSTEM_EXCEPTIONS[12], "debug_monitor", "C11 第 12 项 DebugMonitor")
eq(vb.SYSTEM_EXCEPTIONS[14], "pendsv", "C12 第 14 项 PendSV")
eq(vb.SYSTEM_EXCEPTIONS[15], "systick", "C13 第 15 项 SysTick")
eq(vb.vector_table_size(8), 96, "C14 8 个外部中断时表长 96")
eq(vb.vector_table_size(0), 64, "C15 无外部中断 64")
eq(vb.required_align(16), 128, "C16 16 项也按 128 对齐（CMSIS 下限）")
eq(vb.required_align(64), 256, "C17 64 项需要 256")
eq(vb.required_align(1), 128, "C18 下限是 128 不是 4")
eq(vb.vector_names(18)[16], "irq0", "C19 外部中断从 irq0 编号")

# ---------------------------------------------------------------- D. 样例镜像
vecs = vb.candidate_vectors(img, 0, 24)
eq(len(vecs), 24, "D1 读出 24 项")
eq(vecs[0], RAM_LO + 0x1000, "D2 第 0 项是栈顶")
ck(RAM_LO <= vecs[0] <= RAM_HI, "D3 栈顶在 RAM 窗口内")
ck(vecs[1] & 1 == 1, "D4 reset 有 Thumb 位")
ck(all(v & 1 == 1 for v in vecs[1:17]), "D5 前 16 个异常入口都是奇数")
eq(vecs[17], 0, "D6 第 17 项起为 0（只造了 16 个桩）")
eq(vb.entry_point(vecs), vecs[1] & ~1, "D7 入口清掉 Thumb 位")
ck(vb.entry_point(vecs) % 2 == 0, "D8 入口是偶地址")
ck(vb.entry_point(vecs) >= BASE, "D9 入口在镜像内")

# ---------------------------------------------------------------- E. 基址打分
s_true = vb.score_base(vecs, BASE, len(img), RAM_LO, RAM_HI)
s_wrong = vb.score_base(vecs, BASE + 0x40000, len(img), RAM_LO, RAM_HI)
ck(s_true > s_wrong, "E1 真基址得分高于随机基址")
ck(s_true > vb.score_base(vecs, 0, len(img), RAM_LO, RAM_HI), "E2 高于 base=0")
for off in (0x1000, 0x8000, 0x100000, 0x40000000):
    ck(s_true >= vb.score_base(vecs, BASE + off, len(img), RAM_LO, RAM_HI),
       "E3 候选 %#x 不优于真值" % off)
ck(vb.score_base([], 0, 10, RAM_LO, RAM_HI) == 0, "E4 空表得 0")

# 打分函数本身的构件：栈顶窗口与 Thumb 位各占一项
only_sp = vb.score_base([RAM_LO + 4], BASE, len(img), RAM_LO, RAM_HI)
ck(only_sp >= 12, "E5 栈顶落在 RAM 且 4 字节对齐")
ck(vb.score_base([BASE - 4], BASE, len(img), RAM_LO, RAM_HI) < only_sp,
   "E6 栈顶不在 RAM 窗口则扣分")
ck(vb.score_base([0, BASE + 0x10 | 1], BASE, len(img), RAM_LO, RAM_HI) > 0,
   "E7 奇数向量落在镜像内才计分")
ck(vb.score_base([0, BASE + 0x10], BASE, len(img), RAM_LO, RAM_HI) <
   vb.score_base([0, BASE + 0x10 | 1], BASE, len(img), RAM_LO, RAM_HI),
   "E8 偶数向量被判为非法（Thumb-only，对偶）")
ck(vb.score_base([0, 0xFFFFFFFF], BASE, len(img), RAM_LO, RAM_HI) < s_true,
   "E9 指向镜像外的向量不计分")

# ---------------------------------------------------------------- F. 端到端推断
best_base, best_score, best_off = vb.infer_base(img, RAM_LO, RAM_HI)
eq(best_base, BASE, "F1 推断出的基址 == 真实基址")
eq(best_off, 0, "F2 表在偏移 0")
ck(best_score == s_true, "F3 得分与直接打分一致")

img2 = vb.build_sample_image(0x08010000, n_ext=4, ram_lo=RAM_LO, ram_hi=RAM_HI,
                             n_code=12)
eq(vb.infer_base(img2, RAM_LO, RAM_HI)[0], 0x08010000, "F4 换一个基址也能找回来")

# 把镜像整体前插一段引导头，向量表不在偏移 0 时，朴素推断会失配——
# 这正是"先找表在哪"再"算基址"两步不能合成一步的原因。
pad = b"\xff" * 64
img3 = pad + img
eq(vb.candidate_vectors(img3, 0, 4), [0xFFFFFFFF] * 4, "F5 前插引导头后偏移 0 全是 0xff")
eq(vb.candidate_vectors(img3, 64, 2)[1], vecs[1], "F6 真正的表在偏移 64")
ck(vb.score_base(vb.candidate_vectors(img3, 64, 24), BASE, len(img3), RAM_LO, RAM_HI)
   > vb.score_base(vb.candidate_vectors(img3, 0, 24), BASE, len(img3), RAM_LO, RAM_HI),
   "F7 扫到正确偏移后得分才上来")

if __name__ == "__main__":
    print("PASS=%d FAIL=%d" % (PASS, len(FAIL)))
    for f in FAIL:
        print("  FAIL:", f)
    sys.exit(1 if FAIL else 0)
