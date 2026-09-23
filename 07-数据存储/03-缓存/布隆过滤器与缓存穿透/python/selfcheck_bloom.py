"""自检：布隆过滤器（bloom.py）。哈希值用确定性源注入，位宽/哈希数逐项手算核对。"""

import math

from bloom import (
    BLOOM_OPT_ENTS_IS_BITS, BLOOM_OPT_FORCE64, BLOOM_OPT_NO_SCALING, BLOOM_OPT_NOROUND,
    BF_ERROR_RATE_CAP, DEFAULT_BF_ERROR_RATE, DEFAULT_BF_EXPANSION_FACTOR,
    DEFAULT_BF_INITIAL_SIZE, ERROR_TIGHTENING_RATIO, LN2_SQUARED, SB_ERR, SB_FULL, Bloom,
    BloomError, bf_reserve_validate, calc_bpe, sb_chain_add, sb_chain_check, sb_new_chain,
)

PASS = 0
FAIL = []


def check(label, got, expect):
    global PASS
    if got == expect:
        PASS += 1
    else:
        FAIL.append(f"{label}: got {got!r}, expect {expect!r}")


def close(label, got, expect, tol=1e-6):
    global PASS
    if abs(got - expect) <= tol:
        PASS += 1
    else:
        FAIL.append(f"{label}: got {got!r}, expect ~{expect!r}")


def raises(label, fn, *a, **kw):
    global PASS
    try:
        fn(*a, **kw)
    except BloomError:
        PASS += 1
    except Exception as e:
        FAIL.append(f"{label}: raised {type(e).__name__} instead of BloomError")
    else:
        FAIL.append(f"{label}: expected BloomError")


# ------------------------------------------------------------------ 常量
check("ln(2)^2 常量", LN2_SQUARED, 0.480453013918201)
check("误差收紧比", ERROR_TIGHTENING_RATIO, 0.5)
check("BF 误差上限", BF_ERROR_RATE_CAP, 0.25)
check("默认 bf-error-rate", DEFAULT_BF_ERROR_RATE, 0.01)
check("默认 bf-initial-size", DEFAULT_BF_INITIAL_SIZE, 100)
check("默认 bf-expansion-factor", DEFAULT_BF_EXPANSION_FACTOR, 2)
close("bpe(0.01) ≈ 9.585058", calc_bpe(0.01), 9.5850584, tol=1e-5)
close("bpe(0.001) = 1.5 倍 bpe(0.01)", calc_bpe(0.001) / calc_bpe(0.01), 1.5, tol=1e-9)

# --------------------------------------- NOROUND 分支（RedisBloom 实际使用的）
b = Bloom(100, 0.01, BLOOM_OPT_FORCE64 | BLOOM_OPT_NOROUND)
check("NOROUND: n2 保持 0", b.n2, 0)
check("NOROUND: entries 不改写", b.entries, 100)
check("NOROUND: hashes = ceil(ln2*bpe)", b.hashes, 7)
check("NOROUND: bytes 对齐到 8 的倍数", b.bytes, 120)
check("NOROUND: bits = bytes*8", b.bits, 960)
check("NOROUND: 空过滤器 popcount", b.popcount(), 0)

# --------------------------------------------------- 取整分支（默认老行为）
r = Bloom(100, 0.01, 0)
check("取整: n2 = floor(log2(958.5))+1", r.n2, 10)
check("取整: bits = 1<<n2", r.bits, 1024)
check("取整: bytes = bits/8（1024 已是 8 的倍数）", r.bytes, 128)
check("取整: 多出的位认领成额外容量", r.entries, 106)
check("取整: hashes 同为 7", r.hashes, 7)

# -------------------------------------------- ENTS_IS_BITS：entries 其实是 log2(bits)
e = Bloom(10, 0.01, BLOOM_OPT_ENTS_IS_BITS)
check("ENTS_IS_BITS: n2 直接取参数", e.n2, 10)
check("ENTS_IS_BITS: bits = 1<<10", e.bits, 1024)
check("ENTS_IS_BITS: entries 由位宽反推", e.entries, 106)
raises("ENTS_IS_BITS 超过 64 位被拒", Bloom, 65, 0.01, BLOOM_OPT_ENTS_IS_BITS)

# ---------------------------------------------------------------- 参数边界
raises("entries=0 被拒", Bloom, 0, 0.01)
raises("error=0 被拒", Bloom, 100, 0.0)
raises("error=1 被拒", Bloom, 100, 1.0)
raises("error>1 被拒", Bloom, 100, 1.5)

# ----------------------------------------------------- 双哈希定位与读写语义
def hv(i):
    """每个元素占 8 个连续位（i*8 .. i*8+7），保证 100 个元素互不重叠。"""
    return i * 8, 1


f = Bloom(100, 0.01, BLOOM_OPT_FORCE64 | BLOOM_OPT_NOROUND)
check("未加入前 check 为 0", f.check(*hv(0)), 0)
check("首次 add 返回 found_unset=1", f.add(*hv(0)), 1)
check("加入后 check 为 1", f.check(*hv(0)), 1)
check("重复 add 返回 0（没有新位被置上）", f.add(*hv(0)), 0)
check("置位数 = hashes", f.popcount(), 7)
check("另一个元素仍为 0", f.check(*hv(1)), 0)

# ------------------------------------------- BF.RESERVE 参数校验（成对）
err, cap, exp, opts = bf_reserve_validate(0.01, 100)
check("默认 expansion", exp, 2)
check("默认不带 NO_SCALING", bool(opts & BLOOM_OPT_NO_SCALING), False)
err2, _, _, _ = bf_reserve_validate(0.5, 100)
check("error_rate 超过 0.25 被截断", err2, BF_ERROR_RATE_CAP)
_, _, exp0, opts0 = bf_reserve_validate(0.01, 100, expansion=0)
check("expansion=0 等价 NONSCALING", bool(opts0 & BLOOM_OPT_NO_SCALING), True)
_, _, _, opts1 = bf_reserve_validate(0.01, 100, nonscaling=True)
check("显式 NONSCALING", bool(opts1 & BLOOM_OPT_NO_SCALING), True)
raises("NONSCALING 与 EXPANSION 互斥", bf_reserve_validate, 0.01, 100,
       expansion=2, nonscaling=True)
raises("capacity=0 被拒", bf_reserve_validate, 0.01, 0)
raises("capacity 超过 2^30 被拒", bf_reserve_validate, 0.01, (1 << 30) + 1)
raises("error_rate=1 被拒", bf_reserve_validate, 1.0, 100)

# ------------------------------------------------------------ scalable 链
chain = sb_new_chain(100, 0.01)
check("链起手 1 个 link", chain.nfilters, 1)
close("首链 error 已乘 tightening 0.5", chain.cur.inner.error, 0.005, tol=1e-12)
check("首链 entries 仍是 100", chain.cur.inner.entries, 100)
check("首链 hashes = 8（error 收紧后变严）", chain.cur.inner.hashes, 8)

for i in range(100):
    sb_chain_add(chain, *hv(i))
check("加满 100 个后仍只有 1 个 link", chain.nfilters, 1)
check("链总 size", chain.size, 100)
check("当前 link size", chain.cur.size, 100)

sb_chain_add(chain, *hv(100))
check("第 101 个触发扩容", chain.nfilters, 2)
check("新链 error 再收紧一半", math.isclose(chain.cur.inner.error, 0.0025), True)
check("新链容量 = 旧的 × growth", chain.cur.inner.entries, 200)
check("扩容后链总 size", chain.size, 101)
check("旧元素仍查得到", sb_chain_check(chain, *hv(0)), True)
check("新加入的也查得到", sb_chain_check(chain, *hv(100)), True)
check("负控：从未加入的返回 0（位区间不重叠，确定性不误报）",
      sb_chain_check(chain, *hv(1000)), False)
check("重复加入返回 0（已在旧链里）", sb_chain_add(chain, *hv(0)), 0)

# ------------------------------- NONSCALING：error 不收紧，且满后返回 SB_FULL
ns = sb_new_chain(100, 0.01, options=BLOOM_OPT_FORCE64 | BLOOM_OPT_NOROUND | BLOOM_OPT_NO_SCALING)
close("NONSCALING 时首链 error 不收紧", ns.cur.inner.error, 0.01, tol=1e-12)
for i in range(100):
    sb_chain_add(ns, *hv(i))
check("NONSCALING 加满后仍是 1 个 link", ns.nfilters, 1)
check("NONSCALING 溢出返回 SB_FULL", sb_chain_add(ns, *hv(100)), SB_FULL)

# -------------------------------------------- growth=0 时的溢出保护返回 SB_ERR
g0 = sb_new_chain(100, 0.01, options=BLOOM_OPT_FORCE64 | BLOOM_OPT_NOROUND, growth=0)
for i in range(100):
    sb_chain_add(g0, *hv(i))
check("growth=0 时溢出返回 SB_ERR", sb_chain_add(g0, *hv(100)), SB_ERR)

if FAIL:
    print(f"FAILED {len(FAIL)} / {PASS + len(FAIL)}")
    for f in FAIL:
        print("  -", f)
    raise SystemExit(1)
print(f"bloom selfcheck: {PASS} assertions passed")
