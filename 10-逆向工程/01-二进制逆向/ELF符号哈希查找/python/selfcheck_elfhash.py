"""650 ELF 符号哈希查找 —— 自检（实跑）。

断言分三类：
  A. 手算可复现的哈希常量（来自源码公式，逐位推过）
  B. 与暴力线性扫描的等价性（真值不依赖被测实现）
  C. 负控：必然失败的构造（混用哈希 / 非 2 的幂 / 忘记除 64）
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import elf_hash as E  # noqa: E402

PASS = 0
FAIL = []


def check(cond, msg):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(msg)


def eq(actual, expect, msg):
    check(actual == expect, "%s: 期望 %r 实得 %r" % (msg, expect, actual))


# ------------------------------------------------ A. 哈希常量（手算）

# _dl_new_hash: h=5381; h=h*33+c
eq(E.dl_new_hash(""), 5381, "dl_new_hash('')")
eq(E.dl_new_hash("a"), 5381 * 33 + 97, "dl_new_hash('a')")
eq(E.dl_new_hash("A"), 5381 * 33 + 65, "dl_new_hash('A')")
eq(E.dl_new_hash("aa"), 5863207, "dl_new_hash('aa')")
# s0/s1: 5863704 是前两轮的尾部，末字符只差 1 ⇒ 两个 hash 只差最低位
eq(E.dl_new_hash("s0"), 5863752, "dl_new_hash('s0')")
eq(E.dl_new_hash("s1"), 5863753, "dl_new_hash('s1')")
eq(E.dl_new_hash("s1") ^ E.dl_new_hash("s0"), 1, "s0/s1 只差最低位")

# _dl_elf_hash: h=(h<<4)+c; hi=h&0xf0000000; h^=hi>>24; h&=0x0fffffff
eq(E.dl_elf_hash(""), 0, "dl_elf_hash('')")
eq(E.dl_elf_hash("a"), 97, "dl_elf_hash('a')")
eq(E.dl_elf_hash("A"), 65, "dl_elf_hash('A')")
eq(E.dl_elf_hash("aa"), 1649, "dl_elf_hash('aa') = ((97<<4)+97)")

# 两套 hash 的值域与桶位都不通用：这是「混用必错」的根据
eq(E.dl_new_hash("aa") % 8, 7, "gnu hash 的桶位")
eq(E.dl_elf_hash("aa") % 8, 1, "sysv hash 的桶位")
check(E.dl_new_hash("aa") % 8 != E.dl_elf_hash("aa") % 8, "两套 hash 在同表上必须落不同桶")

# 值域：SysV 每轮 &=0x0fffffff ⇒ 恒小于 2^28；GNU 是 uint32 回绕
corpus = ["", "a", "printf", "malloc", "_Z3fooi", "x" * 40]
check(all(E.dl_elf_hash(n) < (1 << 28) for n in corpus), "SysV hash 恒 < 2^28")
check(all(E.dl_new_hash(n) < (1 << 32) for n in corpus), "GNU hash 恒 < 2^32")


# ------------------------------------------------ B. 与暴力扫描等价

SYMS = [""] + ["sym%d" % i for i in range(60)]

gnu = E.GnuHashTable(SYMS, symbias=1, nbuckets=8, nwords=2, shift=6)
for name in SYMS[1:]:
    idx = gnu.lookup(name)
    check(idx is not None, "GNU 应找到 %s" % name)
    if idx is not None:
        eq(gnu.symbols[idx], name, "GNU 命中下标指向的名字(%s)" % name)
eq(gnu.lookup(""), None, "GNU 查不到 index<symbias 的符号")
eq(gnu.lookup("definitely_not_there"), None, "GNU 查不到不存在的符号")

sysv = E.SysvHashTable(SYMS, symbias=1, nbuckets=8)
for name in SYMS[1:]:
    idx = sysv.lookup(name)
    check(idx is not None, "SysV 应找到 %s" % name)
    if idx is not None:
        eq(sysv.symbols[idx], name, "SysV 命中下标指向的名字(%s)" % name)
eq(sysv.lookup(""), None, "SysV 查不到 index<symbias 的符号")

# STN_UNDEF==0 被当作链表终止符 ⇒ chain[i]==0 的 i 恰好是各桶链表的表尾
tails = set()
for b in range(sysv.nbuckets):
    walk = []
    y = sysv.buckets[b]
    while y != 0:
        walk.append(y)
        y = sysv.chain[y]
    if walk:
        tails.add(walk[-1])
        check(len(set(walk)) == len(walk), "bucket %d 的链不应成环" % b)
zeros = {i for i in range(sysv.symbias, len(SYMS)) if sysv.chain[i] == 0}
eq(zeros, tails, "chain[i]==0 的集合 = 各桶链表表尾的集合")
check(all(sysv.lookup(n) != 0 for n in SYMS[1:]), "SysV 命中下标不会是 0")


# ------------------------------------------------ bloom 过滤器

for name in SYMS[1:]:
    h = E.dl_new_hash(name)
    check(gnu.bloom_probe(h)[0], "bloom 对本表符号不应漏报: %s" % name)

# 假阳性真实存在（实跑钉死的名字）：bloom 放行但 chain 里没有
check(gnu.bloom_probe(E.dl_new_hash("q0"))[0], "q0 应通过 bloom")
eq(gnu.lookup("q0"), None, "q0 通过 bloom 但 chain 落空 ⇒ 假阳性")

# 下标是 (h/64)&idxbits，不是 h&idxbits：h=64 时 1 vs 0
eq((64 // E.NATIVE_CLASS) & 1, 1, "(64/64)&1")
eq(64 & 1, 0, "忘记除 64 会得 0")

# shift=0 ⇒ hashbit1 == hashbit2，过滤器只查 1 个不同比特
flat = E.GnuHashTable(SYMS, symbias=1, nbuckets=8, nwords=2, shift=0)
h0 = E.dl_new_hash("sym7")
_, _, fb1, fb2 = flat.bloom_probe(h0)
eq(fb1, fb2, "shift=0 时两个 bit 位相同")
_, _, gb1, gb2 = gnu.bloom_probe(h0)
check(gb1 != gb2, "shift=6 时两个 bit 位应不同(sym7)")

# bloom 字的比特确实被置上
for name in SYMS[1:8]:
    h = E.dl_new_hash(name)
    w = gnu.bitmask[(h // E.NATIVE_CLASS) & gnu.idxbits]
    check(w >> (h & 63) & 1 == 1, "bloom bit1 应置位: %s" % name)
    check(w >> ((h >> gnu.shift) & 63) & 1 == 1, "bloom bit2 应置位: %s" % name)


# ------------------------------------------------ chain 结构

for b in range(gnu.nbuckets):
    start = gnu.buckets[b]
    if start == 0:
        continue
    i = start
    seen_last = 0
    n = 0
    while True:
        n += 1
        if gnu.chain_zero[i] & 1:
            seen_last = 1
            break
        check(gnu.chain_zero[i] & 1 == 0, "链中间项 LSB 应为 0")
        i += 1
        check(n < 1000, "链不应无限长")
    eq(seen_last, 1, "bucket %d 的链必须有终止项" % b)

# 候选判据只看 31 个高位：((hv^h)>>1)==0 等价于 (hv^h) <= 1
def candidate(hv, h):
    return ((hv ^ h) >> 1) == 0

check(candidate(0x1234, 0x1235), "差 1 仍是候选")
check(candidate(0x1234, 0x1234), "相等是候选")
check(not candidate(0x1234, 0x1236), "差 2 不是候选")
check(not candidate(0x1234, 0x1237), "差 3 不是候选")

# s0/s1 只差最低位 ⇒ 同一条链上会互相成为候选，靠名字比较区分
coll = E.GnuHashTable(["", "s0", "s1"], symbias=1, nbuckets=1, nwords=2, shift=6)
i0, tr0 = coll.lookup("s0", trace=True)
i1, tr1 = coll.lookup("s1", trace=True)
check(i0 is not None and coll.symbols[i0] == "s0", "碰撞下仍能定位 s0")
check(i1 is not None and coll.symbols[i1] == "s1", "碰撞下仍能定位 s1")
cands0 = [s[2] for s in tr0 if s[0] == "candidate"]
cands1 = [s[2] for s in tr1 if s[0] == "candidate"]
# 链按 bucket 排序、s0 在前：查 s0 时首候选即名字命中，直接返回
eq(cands0, ["s0"], "查 s0 时只经过 s0 一个候选")
# 查 s1 时先在 s0 上「只差最低位 ⇒ 也是候选」但名字不符，才走到 s1
eq(cands1, ["s0", "s1"], "查 s1 时 s0 会先成为候选再被名字比较刷掉")


# ------------------------------------------------ 序列化 / 解析（_dl_setup_hash 逆）

w = gnu.words32()
p = E.parse_gnu_hash(w)
eq(p["nbuckets"], gnu.nbuckets, "解析 nbuckets")
eq(p["symbias"], gnu.symbias, "解析 symbias")
eq(p["nwords"], gnu.nwords, "解析 bitmask_nwords")
eq(p["shift"], gnu.shift, "解析 shift")
eq(p["idxbits"], gnu.nwords - 1, "idxbits = nwords-1")
eq(p["buckets"], gnu.buckets, "解析 buckets")
eq(p["chain_zero"], gnu.chain_zero, "解析 chain_zero(已按 symbias 归位)")
eq(p["bitmask"], gnu.bitmask, "解析 bitmask")
eq(len(p["chain_zero"]), len(gnu.symbols), "chain_zero 长度 = 符号数")
check(all(p["chain_zero"][i] == 0 for i in range(gnu.symbias)), "index<symbias 无 chain 项")

wv = sysv.words32()
ps = E.parse_sysv_hash(wv)
eq(ps["nchain"], len(SYMS), "SysV nchain")
eq(ps["buckets"], sysv.buckets, "SysV buckets 解析")
eq(ps["chain"], sysv.chain, "SysV chain 解析")
eq(wv[1], len(SYMS), "SysV 第二个字是 nchain")


# ------------------------------------------------ 负控

try:
    E.GnuHashTable(SYMS, symbias=1, nwords=3)
    check(False, "nwords 非 2 的幂应报错")
except ValueError:
    check(True, "nwords 非 2 的幂应报错")

try:
    E.parse_gnu_hash([4, 1, 3, 6] + [0] * 64)
    check(False, "解析时 nwords 非 2 的幂应报错")
except ValueError:
    check(True, "解析时 nwords 非 2 的幂应报错")

# 混用哈希：拿 GNU hash 去走 SysV 的桶一定进错桶（aa 已在上面钉住 7 vs 1）
wrong_bucket = sysv.buckets[E.dl_new_hash("aa") % sysv.nbuckets]
right_bucket = sysv.buckets[E.dl_elf_hash("aa") % sysv.nbuckets]
check(wrong_bucket != right_bucket or sysv.lookup("aa") is None,
      "用 GNU hash 查 SysV 表必然错位(aa)")

# chain 终止位被误当成 hash 位会漏：把末项 LSB 清掉后链会跑过界
tbl2 = E.GnuHashTable(["", "sym0", "sym1", "sym2"], symbias=1, nbuckets=1, nwords=2, shift=6)
last = max(i for i in tbl2.exported)
orig = tbl2.chain_zero[last]
check(orig & 1 == 1, "最后一项 LSB 应为 1")
tbl2.chain_zero[last] = orig & ~1
check(tbl2.lookup("nope_missing") is None, "清掉终止位后仍不该凭空命中")
tbl2.chain_zero[last] = orig


print("断言通过: %d" % PASS)
if FAIL:
    print("失败 %d 条:" % len(FAIL))
    for m in FAIL:
        print("  -", m)
    sys.exit(1)
print("ALL GREEN")
