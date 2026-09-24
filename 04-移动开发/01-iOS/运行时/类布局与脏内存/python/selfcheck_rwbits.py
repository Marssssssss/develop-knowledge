"""demo 684 自检（前半：位域侧）。

每条断言对照 objc4 `runtime/objc-runtime-new.h` 与 `runtime/PointerUnion.h`
的原文手算期望值。后半部分在 selfcheck_rwmodel.py，由本文件一并跑。
"""

from rwbits_bits import AuthFailure, ClassDataBits, Violation, bad_signature_probe
from rwbits_check import ok, raises, report, section
from rwbits_const import *
from rwbits_model import (ClassRo, ClassRw, RoOrRwExt, extAllocIfNeeded)


# ============================================================
# 1. FAST_* 位布局
# ============================================================
section("1 FAST 位布局")
A, B = FAST_DATA_MASK_IPHONE, FAST_DATA_MASK_OTHER
ok(FAST_IS_SWIFT_LEGACY == 1, "FAST_IS_SWIFT_LEGACY == 1<<0")
ok(FAST_IS_SWIFT_STABLE == 2, "FAST_IS_SWIFT_STABLE == 1<<1")
ok(FAST_HAS_DEFAULT_RR == 4, "FAST_HAS_DEFAULT_RR == 1<<2")
ok(FAST_FLAGS_MASK_64 == 7, "FAST_FLAGS_MASK(64) == 0x7")
ok(FAST_FLAGS_MASK_32 == 3, "FAST_FLAGS_MASK(32) == 0x3")
ok(A & FAST_FLAGS_MASK_64 == 0, "真机 DATA_MASK 与 FLAGS_MASK 不相交")
ok(B & FAST_FLAGS_MASK_64 == 0, "非真机 DATA_MASK 与 FLAGS_MASK 不相交")
ok(FAST_DATA_MASK_32 & FAST_FLAGS_MASK_32 == 0, "32 位两者也不相交")
ok(FAST_IS_RW_POINTER_64 == 1 << 63, "FAST_IS_RW_POINTER == 1<<63")
ok(A & FAST_IS_RW_POINTER_64 == 0, "RW 标记位不在真机 DATA_MASK 里")
ok(FAST_IS_RW_POINTER_32 == 0, "32 位没有 RW 标记位")
ok(A & ~B == 0, "真机掩码是非真机掩码的子集")
ok(B & ~A == 0x7F8000000000, "非真机多留 bit39..46 共 8 位")
ok(bin(B).count("1") - bin(A).count("1") == 8, "两者位数差 8")
ok(LP64_IPHONE.pac_mask & A == 0, "PAC 位落在真机 DATA_MASK 之外")
ok(LP64_IPHONE.pac_mask & FAST_FLAGS_MASK_64 == 0, "PAC 位不含 FAST 标志位")
ok(LP64_IPHONE.pac_mask & FAST_IS_RW_POINTER_64 == 0, "PAC 位不含 RW 标记位")
ok(ILP32.pac_mask == 0, "32 位无 ptrauth，PAC 位为空")

# ============================================================
# 2. has_rw_pointer 的 32 / 64 分歧（成对构造）
# ============================================================
section("2 has_rw_pointer")
ro_a = ClassRo(flags=0, name="Foo")
rw_a = ClassRw(LP64_IPHONE, ro_a, flags=RW_REALIZED)
b64 = ClassDataBits(LP64_IPHONE, disc=0x1111)
ok(not b64.has_rw_pointer(), "bits==0 时 has_rw_pointer 为假(64)")
b64.setData(rw_a)
ok(b64.has_rw_pointer(), "setData 后 has_rw_pointer 为真(64)")
ok(b64.data() is rw_a, "data() 解出同一个 class_rw_t")

rw_plain = ClassRw(LP64_IPHONE, ro_a, flags=0)
ok(b64.has_rw_pointer(addr_of(rw_plain, LP64_IPHONE) | FAST_IS_RW_POINTER_64),
   "64 位只看 bit63，与 class_rw_t->flags 无关")
b64b = ClassDataBits(LP64_IPHONE, disc=0x1112)
b64b.bits = b64b.ptra.sign(addr_of(rw_plain, LP64_IPHONE))
ok(not b64b.has_rw_pointer(),
   "64 位缺 bit63 即假，哪怕 class_rw_t->flags 带 RW_REALIZED")

rw32 = ClassRw(ILP32, ro_a, flags=RW_REALIZED)
b32 = ClassDataBits(ILP32, disc=0x1113)
ok(not b32.has_rw_pointer(), "bits==0 时 has_rw_pointer 为假(32)")
b32.setData(rw32)
ok(b32.has_rw_pointer(), "32 位靠 class_rw_t->flags 里的 RW_REALIZED 判真")
rw32n = ClassRw(ILP32, ro_a, flags=0)
b32n = ClassDataBits(ILP32, disc=0x1114)
b32n.setData(rw32n)
ok(not b32n.has_rw_pointer(),
   "32 位下 setData 一个不带 RW_REALIZED 的 rw，has_rw_pointer 仍是假")

# ============================================================
# 3. setData 的位合成
# ============================================================
section("3 setData")
ro_s = ClassRo(flags=0, name="Bar")
rw_s = ClassRw(LP64_IPHONE, ro_s, flags=RW_REALIZED)
b3 = ClassDataBits(LP64_IPHONE, disc=0x2221)
b3.bits = b3.ptra.sign(addr_of(ro_s, LP64_IPHONE) | FAST_HAS_DEFAULT_RR | FAST_IS_SWIFT_STABLE)
b3.setData(rw_s)
raw3 = b3.ptra.auth(b3.bits)
ok(raw3 & FAST_FLAGS_MASK_64 == 6, "旧字的 FAST 标志(SWIFT_STABLE|DEFAULT_RR)被留下")
ok(raw3 & LP64_IPHONE.data_mask == addr_of(rw_s, LP64_IPHONE), "指针换成新的 class_rw_t")
ok(raw3 & LP64_IPHONE.data_mask != addr_of(ro_s, LP64_IPHONE), "旧的 class_ro_t 地址被丢掉")
ok(bool(raw3 & FAST_IS_RW_POINTER_64), "结果必带 FAST_IS_RW_POINTER")
ok(b3.flags() == RW_REALIZED, "flags(bits) 读的是 class_rw_t->flags")

ro_s32 = ClassRo(flags=0, name="Bar32")
rw_s32 = ClassRw(ILP32, ro_s32, flags=RW_REALIZED)
b3b = ClassDataBits(ILP32, disc=0x2222)
b3b.bits = b3b.ptra.sign(addr_of(ro_s32, ILP32) | 3)
b3b.setData(rw_s32)
raw3b = b3b.ptra.auth(b3b.bits)
ok(raw3b & FAST_FLAGS_MASK_32 == 3, "32 位只留低 2 位")
ok(raw3b & FAST_DATA_MASK_32 == addr_of(rw_s32, ILP32), "32 位指针同样被换掉")

rw_fut = ClassRw(LP64_IPHONE, ro_s, flags=0)
raises(Violation, lambda: b3.setData(rw_fut),
       "已有 rw 指针时 setData 不带 RW_REALIZING|RW_FUTURE 会触发 ASSERT")
rw_fut2 = ClassRw(LP64_IPHONE, ro_s, flags=RW_FUTURE)
b3.setData(rw_fut2)
ok(b3.data() is rw_fut2, "带 RW_FUTURE 时允许覆盖")

# ============================================================
# 4. flags(bits) 不验签 vs data() 验签
# ============================================================
section("4 验签差异")
b4 = ClassDataBits(LP64_IPHONE, disc=0x3331)
ro4 = ClassRo(flags=0, name="T")
rw4 = ClassRw(LP64_IPHONE, ro4, flags=RW_REALIZED)
b4.setData(rw4)
bit_in_pac = (LP64_IPHONE.pac_mask & -LP64_IPHONE.pac_mask).bit_length() - 1
tampered = b4.bits ^ (1 << bit_in_pac)
ok(bad_signature_probe(b4, tampered), "翻转一个 PAC 位后验签失败")
ok(b4.flags(tampered) == RW_REALIZED,
   "flags(bits) 走 ptrauth_strip 不校验，被篡改也能读出正确 flags")
ok(b4.flags() == b4.data().flags, "未篡改时两者一致")


def _tampered_data():
    saved = b4.bits
    b4.bits = tampered
    try:
        return b4.data()
    finally:
        b4.bits = saved


raises(AuthFailure, _tampered_data, "data() 走 auth，被篡改会失败")
ok(b4.ptra.strip(tampered) == b4.ptra.auth(b4.bits), "strip 与 auth 得到同一裸值")

# ============================================================
# 5. safe_ro 只 load 一次 + 路径探针
# ============================================================
section("5 safe_ro")
ro5 = ClassRo(flags=0, name="R")
rw5 = ClassRw(LP64_IPHONE, ro5, flags=RW_REALIZED)
b5 = ClassDataBits(LP64_IPHONE, disc=0x4441)
b5.bits = b5.ptra.sign(addr_of(ro5, LP64_IPHONE))
b5.probe = []
ok(b5.safe_ro() is ro5, "RO 态 safe_ro 直接给出 class_ro_t")
ok("safe_ro:ro" in b5.probe and "auth:ro" in b5.probe, "RO 态走 RO 路径并验签")
b5.probe = []
b5.setData(rw5)
ok(b5.safe_ro() is ro5, "RW 态 safe_ro 取 data()->ro()，仍是同一个 ro")
ok("safe_ro:rw" in b5.probe and "auth:ro" not in b5.probe,
   "RW 态不会用 RO 签名方案去解 RW 指针")
b5.disable_enforce = True
b5.probe = []
b5.safe_ro()
ok("strip" in b5.probe and "auth" not in b5.probe,
   "disableEnforceClassRXPtrAuth 时改走 strip")
b5.disable_enforce = False
b5.bits = b5.ptra.sign(addr_of(ro5, LP64_IPHONE))
b5.probe = []
b5.safe_ro(authenticate=False)
ok("strip:ro" in b5.probe and "auth:ro" not in b5.probe,
   "Authentication::Strip 也不校验签名")
ok(b32n.safe_ro() is rw32n,
   "32 位下不带 RW_REALIZED 的 rw 会被 safe_ro 当成 ro 解出（正是源码注释警告的情形）")
raises(Violation, b32n.data, "同一情形下 data() 的 ASSERT 会拦住")

# ============================================================
# 6. ro_or_rw_ext 的 PointerUnion 标签
# ============================================================
section("6 PointerUnion")
ok(RoOrRwExt(0, PtrAuth(LP64_IPHONE, 1)).is_null(), "全零才算 isNull")
v0 = rw_a.get_ro_or_rwe()
ok(v0.tag() == 0 and v0.is_ro() and not v0.is_rwe(), "class_ro_t 的标签是 0")
ok(v0.addr() == addr_of(ro_a, LP64_IPHONE), "取 ro 时 & ~1 不改变地址")
raises(Violation, v0.get_rwe, "标签为 0 时取 rwe 触发 ASSERT")
rwe_a = extAllocIfNeeded(rw_a)
v1 = rw_a.get_ro_or_rwe()
ok(v1.tag() == 1 and v1.is_rwe() and not v1.is_ro(), "class_rw_ext_t 的标签是 1")
ok(v1.addr() == addr_of(rwe_a, LP64_IPHONE), "取 rwe 时先 auth 再 & ~1")
raises(Violation, v1.get_ro, "标签为 1 时取 ro 触发 ASSERT")
pu = PtrAuth(LP64_IPHONE, 0x555)
z = RoOrRwExt(pu.sign(1), pu)      # rwe 指针为空、但打了标签
ok(not z.is_null(), "空 rwe 指针打了标签后 isNull() 为假")
ok(z.is_rwe() and z.addr() == 0, "标签仍为 1，取出的地址是 0")
ok(not z.truthy(), "但 operator bool() 为假——两个语义不一致的边角")
ok(RoOrRwExt(pu.sign(addr_of(ro_a, LP64_IPHONE)), pu).truthy(), "正常 ro 为真")


# ------------------------------------------------------------
# 后半部分（模型侧：extAlloc / list_array_tt / flags / 重签名）
import selfcheck_rwmodel  # noqa: E402

report()
