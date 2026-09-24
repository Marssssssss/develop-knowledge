"""dyld 链式修正模型的自检(实跑)。

每条断言都对照 apple-oss-distributions/dyld@main 的
include/mach-o/fixup-chains.h 与 mach_o/ChainedFixups.cpp 手算过期望值。
"""

from chainfix_model import (  # noqa: F401
    MASK64, bits, ins, sign_extend,
    START_NONE, START_MULTI, START_LAST,
    PTR_64, PTR_32, PTR_64_OFFSET, PTR_ARM64E, PTR_ARM64E_KERNEL, PTR_ARM64E_USERLAND,
    IMPORT, IMPORT_ADDEND, IMPORT_ADDEND64,
    Fixup, Error, Fmt64, Fmt64Offset, Fmt32, FmtArm64eRebase, FmtArm64eUserland,
    FmtArm64eKernel, SegmentStarts, Segment, make_format,
    chain_starts_on_page, for_each_fixup_in_chain, parse_import,
)

PREF = 0x1000000  # preferredLoadAddress = 16MB

_PASS = [0]
_FAIL = []


def ok(cond, msg):
    if cond:
        _PASS[0] += 1
    else:
        _FAIL.append(msg)
        print("FAIL:", msg)


def eq(a, b, msg):
    ok(a == b, "%s (got %r want %r)" % (msg, a, b))


def raises(fn, kind, msg):
    try:
        fn()
    except Error as e:
        ok(str(e) == kind, "%s (got %r want %r)" % (msg, str(e), kind))
        return
    ok(False, "%s (no raise)" % msg)


# ---------- 1. 位域工具 ----------
eq(bits(0xFF, 0, 3), 0xF, "bits 低 4 位")
eq(bits(0x8000000000000000, 63, 63), 1, "bits 最高位")
eq(ins(0, 51, 62, 3), 3 << 51, "ins 放进 next 位域")
eq(ins(0xFFFFFFFFFFFFFFFF, 0, 3, 0), 0xFFFFFFFFFFFFFFF0, "ins 清空目标位域")
eq(sign_extend(0xF1, 8), -15, "sign_extend 8 位负数")
eq(sign_extend(0x7F, 8), 127, "sign_extend 8 位正数")

# ---------- 2. PTR_64 rebase:target 是 vmaddr,要减基址 ----------
f64 = Fmt64()
raw = f64.write(Fixup(target=0x234000), 12, PREF)
eq(bits(raw, 0, 35), 0x1234000, "PTR_64 rebase 写入的是 vmaddr 而非 offset")
eq(bits(raw, 51, 62), 3, "PTR_64 next 字段 = delta/4")
eq(bits(raw, 63, 63), 0, "PTR_64 rebase 的 bind 位为 0")
eq(f64.parse(raw, PREF).target, 0x234000, "PTR_64 rebase 解析回 offset")
eq(f64.parse(raw, 0).target, 0x1234000, "基址为 0 时 vmaddr 等于 offset")

# 同一串比特用 64_OFFSET 解释:target 直接是 offset
f64o = Fmt64Offset()
raw_o = f64o.write(Fixup(target=0x234000), 12, PREF)
eq(bits(raw_o, 0, 35), 0x234000, "PTR_64_OFFSET 写入的是 vm offset")
eq(f64o.parse(raw_o, PREF).target, 0x234000, "PTR_64_OFFSET 解析不减基址")
# 口径错配:拿 OFFSET 的二进制当 PTR_64 读,会减出一个负数(u64 回绕)
eq((0x234000 - PREF) & MASK64, 0xFFFFFFFFFF234000, "错配口径下 u64 回绕值")

# ---------- 3. high8: 只用 36 位装不下 64 位指针,高 8 位另存 ----------
hi_target = (0xAB << 56) | 0x2000
raw_hi = f64o.write(Fixup(target=hi_target), 4, PREF)
eq(bits(raw_hi, 36, 43), 0xAB, "high8 存在位 36..43")
eq(bits(raw_hi, 0, 35), 0x2000, "target 低 56 位中超出 36 位的高位不落进位域")
eq(f64o.parse(raw_hi, PREF).target, hi_target, "high8 往返一致")

# ---------- 4. PTR_64 bind:ordinal 24 位、addend 8 位无符号 ----------
raw_b = f64.write(Fixup(is_bind=True, ordinal=0x123456, addend=200), 8, PREF)
fx = f64.parse(raw_b, PREF)
eq(fx.is_bind, True, "bind 位被置起")
eq(fx.ordinal, 0x123456, "ordinal 24 位往返")
eq(fx.addend, 200, "addend 8 位往返")
raises(lambda: f64.write(Fixup(is_bind=True, ordinal=1, addend=300), 8, PREF),
       "badAddend", "addend 超过 255 被拒")
raises(lambda: f64.write(Fixup(is_bind=True, ordinal=0x1000000, addend=0), 8, PREF),
       "badBindOrdinal", "ordinal 超过 24 位被拒")

# ---------- 5. 链距约束 ----------
raises(lambda: f64.write(Fixup(target=0x1000), 6, PREF),
       "badChainDistance", "delta 不是 stride 整数倍被拒")
eq(f64.max_next(), 4 * 0xFFF, "PTR_64 maxNext = 4*0xFFF")
eq(f64.min_next(), 4, "PTR_64 minNext = stride")
raises(lambda: f64.write(Fixup(target=0x1000), 4 * 0x1000, PREF),
       "badChainDistance", "delta 超过 12 位 next 被拒")
eq(f64.max_rebase_target_offset(False), 0xFFFFFFFFF, "PTR_64 rebase target 上限 36 位")
raises(lambda: f64.write(Fixup(target=0x1000000000), 4, PREF),
       "badVmAddr", "vmaddr 超出 36 位被拒")

# ---------- 6. arm64e:next 11 位、stride 由格式决定 ----------
ae = FmtArm64eRebase()
eq(ae.stride, 8, "ARM64E stride = 8")
eq(ae.max_next(), 8 * 0x7FF, "ARM64E maxNext = stride*0x7FF")
aek = FmtArm64eKernel()
eq(aek.stride, 4, "ARM64E_KERNEL stride = 4")
eq(aek.max_next(), 4 * 0x7FF, "ARM64E_KERNEL maxNext 随 stride 缩半")
eq(FmtArm64eUserland().stride, 8, "ARM64E_USERLAND stride = 8")
eq(ae.max_rebase_target_offset(True), 0xFFFFFFFF, "auth rebase target 上限 32 位")
eq(ae.max_rebase_target_offset(False), 0x7FFFFFFFFFF, "unauth rebase target 上限 43 位")
eq(ae.bind_max_addend(False), 0x3FFFF, "arm64e bind addend 上限 19 位")
eq(ae.bind_min_addend(False), -0x3FFFF, "arm64e bind addend 下限 -0x3FFFF")
eq(ae.bind_max_addend(True), 0, "auth bind 不嵌 addend")
eq(ae.max_bind_ordinal(False), 0xFFFF, "arm64e 16 位 ordinal 上限")
eq(Fmt32().max_bind_ordinal(False), 0xFFFFF, "32 位格式 ordinal 上限 20 位")

raw_ae = ae.write(Fixup(authenticated=True, target=0x1234, diversity=0xBEEF,
                        addr_div=1, key=2), 16, PREF)
fx_ae = ae.parse(raw_ae, PREF)
eq(fx_ae.authenticated, True, "auth 位被置起")
eq(fx_ae.target, 0x1234, "auth rebase target 32 位往返")
eq(fx_ae.diversity, 0xBEEF, "diversity 16 位往返")
eq(fx_ae.addr_div, 1, "addrDiv 1 位往返")
eq(fx_ae.key, 2, "key 2 位往返")
eq(bits(raw_ae, 51, 61), 2, "arm64e next = 16/8")
raw_ae2 = ae.write(Fixup(target=0x2000), 24, PREF)
eq(ae.next_location(0, raw_ae2), 24, "arm64e next*stride = 3*8")
eq(ae.parse(raw_ae2, PREF).target, 0x2000, "ARM64E unauth rebase 自身往返")
raw_ul = FmtArm64eUserland().write(Fixup(target=0x2000), 24, PREF)
eq(bits(raw_ul, 0, 42), 0x2000, "USERLAND 写入的是 vm offset")
eq(FmtArm64eUserland().parse(raw_ul, PREF).target, 0x2000,
   "USERLAND 的 unauth rebase 是 vm offset")
eq(ae.parse(raw_ul, PREF).target, 0x2000 - PREF,
   "同一串比特按 ARM64E 读会多减一次基址")
eq((0x2000 - PREF) & MASK64, 0xFFFFFFFFFF002000, "错配口径下 u64 回绕值")
raises(lambda: ae.write(Fixup(is_bind=True, ordinal=1, addend=0x80000), 8, PREF),
       "badAddend", "arm64e bind addend 超过 19 位被拒")
eq(ae.parse(ae.write(Fixup(is_bind=True, ordinal=7, addend=0x3FFFF), 8, PREF),
            PREF).addend, 0x3FFFF, "arm64e bind addend 19 位往返")

# ---------- 7. 32 位格式:next 只有 5 位 ----------
f32 = Fmt32()
raw32 = f32.write(Fixup(target=0x1000), 8, 0x400)
eq(bits(raw32, 0, 25), 0x1400, "PTR_32 rebase 写入 vmaddr")
eq(f32.parse(raw32, 0x400).target, 0x1000, "PTR_32 rebase 解析回 offset")
eq(f32.max_next(), 4 * 0x1F, "PTR_32 maxNext = 4*0x1F")
raises(lambda: f32.write(Fixup(target=0x1000), 128, 0x400),
       "badChainDistance", "PTR_32 delta 超过 5 位 next 被拒")
raises(lambda: f32.write(Fixup(is_bind=True, ordinal=1, addend=64), 4, 0x400),
       "badAddend", "PTR_32 addend 超过 6 位被拒")
eq(f32.parse(f32.write(Fixup(is_bind=True, ordinal=0xFFFFF, addend=63), 4, 0x400),
             0x400).addend, 63, "PTR_32 addend 6 位往返")

# ---------- 8. page_start 三态解码 ----------
eq(chain_starts_on_page(SegmentStarts(0x1000, PTR_64, [START_NONE]), 0), [],
   "START_NONE 表示本页无修正")
eq(chain_starts_on_page(SegmentStarts(0x1000, PTR_64, [0x0100]), 0), [0x100],
   "普通 page_start 就是页内偏移")
multi = SegmentStarts(0x1000, PTR_64, [0x8001, 0x0010, 0x8020])
eq(chain_starts_on_page(multi, 0), [0x10, 0x20],
   "MULTI 走 overflow 列表,直到遇到带 LAST 位的项")
eq(START_MULTI, START_LAST, "MULTI 与 LAST 是同一个位值")
eq(chain_starts_on_page(SegmentStarts(0x1000, PTR_64, [START_NONE, 0x0010]), 0), [],
   "0xFFFF 同时满足 NONE 与 MULTI,先按 NONE 拦截")

# ---------- 9. 链遍历 ----------
seg = Segment(0, {}, 0x1000)
seg.content[0x100] = f64.write(Fixup(target=0x2000), 8, PREF)
seg.content[0x108] = f64.write(Fixup(is_bind=True, ordinal=5, addend=3), 0, PREF)
walk = for_each_fixup_in_chain(f64, seg, 0x100, 0, PREF)
eq(len(walk), 2, "一条链走到 next==0 为止")
eq([o for o, _ in walk], [0x100, 0x108], "链上位置按 next 推进")
eq(walk[0][1].target, 0x2000, "链首是 rebase")
eq(walk[1][1].ordinal, 5, "链尾是 bind")

seg2 = Segment(0, {}, 0x1000)
seg2.content[0xFF8] = f64.write(Fixup(target=0x2000), 16, PREF)
eq(len(for_each_fixup_in_chain(f64, seg2, 0xFF8, 0, PREF)), 1,
   "next 跨出页末尾就断链")
seg3 = Segment(0, {}, 0x1000)
seg3.content[0xFF8] = f64.write(Fixup(target=0x2000), 8, PREF)
seg3.content[0x1000] = f64.write(Fixup(target=0x3000), 0, PREF)
eq(len(for_each_fixup_in_chain(f64, seg3, 0xFF8, 0, PREF)), 2,
   "next 恰好落在页末地址不算出页(判据是严格大于)")
eq(len(for_each_fixup_in_chain(f64, seg3, 0x2000, 0, PREF)), 0,
   "链起点不在本页则整条链被丢弃")

# ---------- 10. imports 三种格式 ----------
w1 = (0x1234 << 9) | (1 << 8) | 0xF1
d, n = parse_import([w1], 0, IMPORT)
eq(d["lib_ordinal"], -15, "IMPORT 的 lib_ordinal 是 8 位有符号")
eq(d["weak_import"], 1, "weak_import 占 1 位")
eq(d["name_offset"], 0x1234, "name_offset 占 23 位")
eq(n, 1, "IMPORT 占一个字")
d2, n2 = parse_import([w1, 0xFFFFFFF8 & MASK64], 0, IMPORT_ADDEND)
eq(d2["addend"], -8, "IMPORT_ADDEND 的 addend 是 32 位有符号")
eq(n2, 2, "IMPORT_ADDEND 占两个字")
w3 = (0x89ABCDEF << 32) | (1 << 16) | 0xFFF1
d3, n3 = parse_import([w3, MASK64], 0, IMPORT_ADDEND64)
eq(d3["lib_ordinal"], -15, "IMPORT_ADDEND64 的 lib_ordinal 是 16 位有符号")
eq(d3["name_offset"], 0x89ABCDEF, "IMPORT_ADDEND64 的 name_offset 是 32 位")
eq(d3["addend"], -1, "IMPORT_ADDEND64 的 addend 是 64 位有符号")
eq(n3, 2, "IMPORT_ADDEND64 占两个字")

# ---------- 11. 格式分派 ----------
eq(make_format(PTR_64).name, "DYLD_CHAINED_PTR_64", "格式 2 分派到 PTR_64")
eq(make_format(PTR_64_OFFSET).name, "DYLD_CHAINED_PTR_64_OFFSET", "格式 6 分派到 64_OFFSET")
raises(lambda: make_format(0), "unknown pointer_format 0", "未知格式被拒")
eq(Fmt64().is64, True, "64 位格式 is64")
eq(Fmt32().is64, False, "32 位格式 is64")

print("assertions passed: %d, failed: %d" % (_PASS[0], len(_FAIL)))
if _FAIL:
    raise SystemExit(1)
