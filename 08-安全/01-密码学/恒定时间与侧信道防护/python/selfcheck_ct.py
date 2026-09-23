"""恒定时间自检：OpenSSL 原语的真值表 + 「泄漏是可观测的」成对对照。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ct import (MASK32, Barrier, eq32, ge32, is_zero32, le32, lt32, msb32,
                select32, select8, select_int, value_barrier32, ct_memcmp,
                ct_lookup)
from leak import (Compiler, Observer, ct_lookup_observed, ct_memcmp_observed,
                  ct_padding_check, ladder_modexp, naive_lookup,
                  naive_memcmp, naive_modexp, naive_padding_check)

PASS = 0
FAIL = 0
BAD = []


def ck(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        BAD.append(msg)


# ============================================ A. 原语的真值表（对照 Python 语义）
for a in (0, 1, 2, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF):
    ck(msb32(a) in (0, MASK32), "msb 只能是 0 或全 1, a=%#x" % a)
    ck(msb32(a) == (MASK32 if a >> 31 else 0), "msb = 最高位复制, a=%#x" % a)
for a in (0, 1, 5, 0xFFFFFFFF):
    ck(is_zero32(a) == (MASK32 if a == 0 else 0), "is_zero a=%#x" % a)
for a, b in ((0, 0), (1, 2), (2, 1), (7, 7), (0, 0xFFFFFFFF), (0x80000000, 1)):
    ck(lt32(a, b) == (MASK32 if a < b else 0), "lt(%#x,%#x)" % (a, b))
    ck(ge32(a, b) == (MASK32 if a >= b else 0), "ge(%#x,%#x)" % (a, b))
    ck(le32(a, b) == (MASK32 if a <= b else 0), "le(%#x,%#x)" % (a, b))
    ck(eq32(a, b) == (MASK32 if a == b else 0), "eq(%#x,%#x)" % (a, b))
# 小整数全枚举，确保没有边界错误
for a in range(0, 40):
    for b in range(0, 40):
        ck(lt32(a, b) == (MASK32 if a < b else 0), "lt32 枚举 %d,%d" % (a, b))
ck(lt32(0, 0x80000000) == MASK32, "无符号语义：0 < 2^31 成立（有符号解释会判反，所以要修符号位）")
ck(lt32(0x80000000, 1) == 0, "2^31 不小于 1（有符号会判反，这正是要修符号位的原因）")
for m, x, y in ((MASK32, 0xAA, 0x55), (0, 0xAA, 0x55), (MASK32, 7, 9)):
    ck(select32(m, x, y) == (x if m else y), "select 取值 m=%#x" % m)
ck(select8(MASK32, 0x12, 0x34) == 0x12, "select8 只保留低 8 位")
ck(select_int(MASK32, -5, 9) == -5, "select_int 能返回负数（补码回解释）")
ck(select_int(0, -5, 9) == 9, "select_int 掩码为 0 时取后者")
ck(isinstance(value_barrier32(1), Barrier), "value_barrier 返回屏障类型")
ck(int(select32(MASK32, 0xAB, 0xCD)) == 0xAB, "屏障不参与数值计算之外的语义")

# ================================= B. memcmp：朴素版泄漏「首个差异位置」
tail = bytes(range(16))
cases = {}
for pos in (0, 1, 8, 15):
    x = bytes([0x5A] * 16)
    y = bytearray(x)
    y[pos] ^= 1
    o1 = Observer()
    naive_memcmp(o1, x, bytes(y))
    o2 = Observer()
    ct_memcmp_observed(o2, x, bytes(y))
    cases[pos] = (len(o1.branches) + o1.steps, len(o2.branches) + o2.steps)
ck(len({v[0] for v in cases.values()}) == len(cases),
   "朴素 memcmp 的观测步数**随首个差异位置变化**（这就是泄漏）")
ck(len({v[1] for v in cases.values()}) == 1,
   "常量时间 memcmp 的观测步数与差异位置无关")
ck(ct_memcmp(b"abc", b"abc") == 1 and ct_memcmp(b"abc", b"abd") == 0,
   "ct_memcmp 的返回值语义正确")
ck(ct_memcmp(b"abc", b"ab") == 0, "长度不等直接返回 0（与 Go subtle 一致）")
ck(Observer() is not None and len(Observer().branches) == 0, "观测器初始为空")
o = Observer()
naive_memcmp(o, b"aaa", b"aaa")
ck(len(o.branches) == 3, "朴素版逐字节产生一次分支记录")
o = Observer()
ct_memcmp_observed(o, b"aaa", b"aaa")
ck(len(o.branches) == 0, "常量时间版不产生任何分支记录")

# ================================ C. 表查找：朴素版把索引送进缓存序列
table = [i * 7 % 256 for i in range(256)]
seen_naive, seen_ct = set(), set()
for idx in (0, 3, 200, 255):
    o = Observer()
    naive_lookup(o, table, idx)
    seen_naive.add(tuple(o.addr))
    o = Observer()
    ck(ct_lookup_observed(o, table, idx) == table[idx], "ct_lookup 取值正确 idx=%d" % idx)
    seen_ct.add(tuple(o.addr))
ck(len(seen_naive) == 4, "朴素查找的访问序列随索引变化（缓存侧信道）")
ck(len(seen_ct) == 1, "常量时间查找的访问序列恒定不变")
ck(len(next(iter(seen_ct))) == 256, "代价：每次查找要读完整张表")

# ======================= D. 模幂：平方-乘 vs Montgomery 阶梯
for exp in (0b1011, 0b1000, 0b0111, 0b1111):
    o = Observer()
    r1, b1 = naive_modexp(o, 3, exp, 65537)
    o2 = Observer()
    r2, b2 = ladder_modexp(o2, 3, exp, 65537)
    ck(r1 == pow(3, exp, 65537), "朴素模幂结果正确 exp=%#x" % exp)
    ck(r2 == pow(3, exp, 65537), "阶梯模幂结果正确 exp=%#x" % exp)
    ck(b1 == list(reversed(b2)), "朴素从低位开始、阶梯从高位开始，比特序列互为逆序")
    ck(o.steps == len(b1) + sum(b1), "朴素版步数 = 比特数 + 其中 1 的个数 + 1")
    ck(o2.steps == 3 * len(b2), "阶梯版步数只与比特**长度**有关，与取值无关")
ck(naive_modexp(Observer(), 3, 0b1000, 65537)[0] == pow(3, 8, 65537), "exp 只有高位有 1 时也对")

# ================================ E. PKCS#7 去填充：朴素版泄漏填充长度
steps = set()
for n in (1, 4, 8, 16):
    data = bytes(16 - n) + bytes([n]) * n
    o = Observer()
    ck(naive_padding_check(o, data) == n, "朴素版正确识别填充长度 %d" % n)
    steps.add(o.steps + len(o.branches))
    o2 = Observer()
    ck(ct_padding_check(o2, data) == n, "常量时间版正确识别填充长度 %d" % n)
ck(len(steps) == 4, "朴素版观测到的步数随填充长度单调变化（泄漏）")
o3 = Observer()
ct_padding_check(o3, bytes(15) + b"\x01")
o4 = Observer()
ct_padding_check(o4, bytes(1) * 15 + b"\x0f")
ck(o3.steps == o4.steps, "常量时间版步数与填充长度无关")
ck(ct_padding_check(Observer(), bytes(16) + b"\x00" * 0 or bytes(16)) == -1,
   "填充长度 0 判为非法")
ck(ct_padding_check(Observer(), bytes(15) + b"\x00") == -1, "末字节 0 时非法")
ck(ct_padding_check(Observer(), bytes(14) + b"\x03\x03") == -1, "填充字节不一致时非法")

# ================================== F. value_barrier：挡住"折叠成分支"的优化
obs_c = Observer()
comp = Compiler(obs_c)
for m in (0, MASK32):
    comp.select(m, 0xAA, 0x55)                       # 无屏障 → 折叠成跳转
ck(len(obs_c.branches) == 2, "掩码不带屏障时，优化器折叠出 2 次条件跳转")
ck(obs_c.branches == [False, True], "分支走向直接暴露了掩码（=秘密）")
obs_b = Observer()
comp2 = Compiler(obs_b)
comp2.select(value_barrier32(0), 0xAA, 0x55)
comp2.select(value_barrier32(MASK32), 0xAA, 0x55)
ck(len(obs_b.branches) == 0, "带上 value_barrier 后不再产生跳转")
ck(obs_b.steps == 6, "只剩固定次数的算术运算（每次 select 记 3 步）")

print("PASS=%d FAIL=%d" % (PASS, FAIL))
for m in BAD[:12]:
    print("  FAIL:", m)
sys.exit(1 if FAIL else 0)
