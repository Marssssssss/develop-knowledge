"""裸机固件的加载基址定位：以 Cortex-M 中断向量表为锚。

事实来源：
  - ARM-software/CMSIS_5 `CMSIS/Core/Include/core_cm3.h`
        SCB->VTOR（偏移 0x08）
        #define SCB_VTOR_TBLBASE_Pos   29
        #define SCB_VTOR_TBLOFF_Pos     7        -> 重定位表至少 128 字节对齐
        TBLOFF 掩码有两档：0x1FFFFFF<<7（默认）与 0x3FFFFF<<7
    `core_cm0.h` 里 SCB 结构体**没有** VTOR —— ARMv6-M 的表固定在 0x0。
  - zephyrproject-rtos/zephyr `arch/arm/core/cortex_m/vector_table.S`
        第 0 项是初始栈顶 `z_main_stack + CONFIG_MAIN_STACK_SIZE`，
        其后依次 z_arm_reset / nmi / hard_fault / mpu_fault / bus_fault /
        usage_fault / (secure_fault 或 0) / 0 0 0 / svc / debug_monitor /
        0 / pendsv / systick

基址定位的核心：向量表里存的是**绝对地址**。若向量表位于文件偏移 f，
那么表里第 k 项的值 v 满足 v = base + (该项指向的代码在文件里的偏移)。
对第 1 项（reset）而言 reset 代码通常紧跟向量表，于是
base ≈ v1 - (f + 4*n_checked)；更稳的做法是枚举 base，统计"能落回镜像内
且是奇数（Thumb）"的向量条数，条数最多的 base 即真值。
"""

SCB_VTOR_TBLOFF_POS = 7
SCB_VTOR_TBLBASE_POS = 29
SCB_VTOR_TBLOFF_MASK_DEFAULT = (0x1FFFFFF << SCB_VTOR_TBLOFF_POS) & 0xFFFFFFFF
SCB_VTOR_TBLOFF_MASK_SMALL = (0x3FFFFF << SCB_VTOR_TBLOFF_POS) & 0xFFFFFFFF  # 0x1FFFFF80
SCB_VTOR_TBLBASE_MASK = 1 << SCB_VTOR_TBLBASE_POS
MIN_VECTOR_ALIGN = 1 << SCB_VTOR_TBLOFF_POS          # 128

# vector_table.S 里 ARMv7-M/ARMv8-M mainline 的前 16 项
SYSTEM_EXCEPTIONS = [
    "initial_sp", "reset", "nmi", "hard_fault",
    "mpu_fault", "bus_fault", "usage_fault", "secure_fault_or_reserved",
    "reserved7", "reserved8", "reserved9", "svc",
    "debug_monitor", "reserved13", "pendsv", "systick",
]

NVIC_EXTERNAL_OFFSET = 16          # 外部中断从第 16 项开始

PROFILE_HAS_VTOR = {
    "armv6-m": False,              # core_cm0.h：SCB 无 VTOR
    "armv7-m": True,
    "armv8-m-mainline": True,
}


def vector_table_size(n_external):
    return (NVIC_EXTERNAL_OFFSET + n_external) * 4


def required_align(n_vectors):
    """ARM 要求表按不小于 (异常数 * 4) 的 2 的幂对齐；CMSIS 侧的下限是 128。"""
    a = 1
    while a < n_vectors * 4:
        a <<= 1
    return max(a, MIN_VECTOR_ALIGN)


def vtor_value(base, in_code_region):
    """把基址编码成 VTOR：TBLOFF 在 [31:7]，TBLBASE（bit29）选代码区。"""
    v = base & SCB_VTOR_TBLOFF_MASK_DEFAULT
    if in_code_region:
        v |= SCB_VTOR_TBLBASE_MASK
    return v


def vtor_decode(v):
    return {
        "tblbase": 1 if v & SCB_VTOR_TBLBASE_MASK else 0,
        "tbloff": v & SCB_VTOR_TBLOFF_MASK_DEFAULT,
    }


def vtor_relocatable(base, profile="armv7-m"):
    """该基址能不能被 VTOR 指向。ARMv6-M 只能老老实实放 0。"""
    if not PROFILE_HAS_VTOR.get(profile, False):
        return base == 0
    return (base & (MIN_VECTOR_ALIGN - 1)) == 0


def read_u32(image, off):
    return int.from_bytes(image[off:off + 4], "little")


def candidate_vectors(image, off, count=32):
    """在文件偏移 off 处按小端读 count 个字，当作候选向量表。"""
    out = []
    for i in range(count):
        p = off + i * 4
        if p + 4 > len(image):
            break
        out.append(read_u32(image, p))
    return out


def score_base(vectors, base, image_len, ram_lo, ram_hi, thumb_only=True):
    """给一个候选基址打分。

    第 0 项是初始栈顶，必须落在 RAM 窗口里且一般是偶数；
    其余非零项必须是**奇数**（Thumb 的 bit0=1）且落在镜像范围内。
    """
    score = 0
    if not vectors:
        return 0
    if (base & (MIN_VECTOR_ALIGN - 1)) == 0:
        score += 1          # 能被 VTOR 指向的基址才可能是真值（打平分时使用）
    sp = vectors[0]
    if ram_lo <= sp <= ram_hi:
        score += 10
    if sp and (sp & 3) == 0:
        score += 2
    for v in vectors[1:]:
        if v == 0:
            score += 1
            continue
        rel = v - base
        if rel < 0 or rel >= image_len:
            continue
        if thumb_only and not (v & 1):
            continue
        score += 3
    return score


def infer_base(image, ram_lo, ram_hi, cands=None, scan=32):
    """枚举基址，返回 (best_base, best_score, table_offset)。

    技巧：向量表在文件里的偏移 f 与它的加载地址 base + f 一一对应，
    所以对"表在偏移 0"的裸镜像，base 就是让最多向量落回镜像内的那个值。
    """
    if cands is None:
        cands = []
        seeds = set()
        vecs0 = candidate_vectors(image, 0, scan)
        for v in vecs0[1:]:
            if v:
                # 入口地址要清掉 Thumb 位；向量表第 i 项对应"加载地址 + 4*i"
                for i in range(1, min(scan, len(vecs0))):
                    seeds.add((v & ~1) - i * 4)
        cands = sorted(s for s in seeds if s > 0)
    best = (0, -1, 0)
    for off in (0,):
        vecs = candidate_vectors(image, off, scan)
        for b in cands:
            s = score_base(vecs, b, len(image), ram_lo, ram_hi)
            if s > best[1]:
                best = (b, s, off)
    return best


def entry_point(vectors):
    """reset 是第 1 项；真入口要清掉 Thumb 位。"""
    return vectors[1] & ~1


def vector_names(n):
    out = []
    for i in range(n):
        if i < NVIC_EXTERNAL_OFFSET:
            out.append(SYSTEM_EXCEPTIONS[i])
        else:
            out.append("irq%d" % (i - NVIC_EXTERNAL_OFFSET))
    return out


def build_sample_image(base=0x08000000, n_ext=8, ram_lo=0x20000000,
                       ram_hi=0x20010000, n_code=16):
    """造一个裸机镜像：向量表 + 若干 Thumb 桩函数。

    每个桩函数按 4 字节排布，入口地址强制置 Thumb 位。
    """
    n_vec = NVIC_EXTERNAL_OFFSET + n_ext
    table_size = n_vec * 4
    body = bytearray()
    handlers = []
    for i in range(n_code):
        handlers.append(base + table_size + len(body))   # 桩函数在向量表之后
        body.extend(b"\x00\xbf" * 4)          # nop.w 占位
    vt = bytearray()
    vt.extend((ram_lo + 0x1000).to_bytes(4, "little"))       # 初始栈顶
    for i in range(1, n_vec):
        if i - 1 < len(handlers):
            vt.extend((handlers[i - 1] | 1).to_bytes(4, "little"))
        else:
            vt.extend((0).to_bytes(4, "little"))
    return bytes(vt) + bytes(body)
