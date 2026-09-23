"""660 CPython dict 自检：所有期望值都来自实读的 dictobject.c / pycore_dict.h。"""

from __future__ import annotations

import sys

from cpython_dict import (
    DKIX_DUMMY,
    DKIX_EMPTY,
    PYDICT_LOG_MINSIZE,
    PYDICT_MINSIZE,
    CompactDict,
    calculate_log2_keysize,
    documented_probe_order,
    estimate_log2_keysize,
    get_log2_bytes,
    growth_rate,
    index_bytes_per_slot,
    probe_indices,
    usable_fraction,
)

PASS = 0
TOL = 1e-9


def check(cond, msg):
    global PASS
    assert cond, msg
    PASS += 1


def near(a, b, tol=TOL):
    return abs(a - b) <= tol


# ---------------------------------------------------------------- 一、常量
check(PYDICT_MINSIZE == 8 and PYDICT_LOG_MINSIZE == 3, "PyDict_MINSIZE 应为 8，LOG 为 3")
check(DKIX_EMPTY == -1 and DKIX_DUMMY == -2, "DKIX_EMPTY=-1 / DKIX_DUMMY=-2")
check(usable_fraction(8) == 5, "USABLE_FRACTION(8) == (8<<1)/3 == 5")
check(usable_fraction(16) == 10, "USABLE_FRACTION(16) == 10")
check(usable_fraction(32) == 21, "USABLE_FRACTION(32) == 21（不是 21.3，整除截断）")
check(usable_fraction(64) == 42, "USABLE_FRACTION(64) == 42")

# ------------------------------------------------- 二、calculate_log2_keysize
# bit_length(minsize - 1)：落在 2 的幂上时不往上跳一档
check(calculate_log2_keysize(1) == 3, "任何请求都先被 Py_MAX 提到 PyDict_MINSIZE=8 -> 3")
check(calculate_log2_keysize(8) == 3, "8 -> bit_length(7) == 3")
check(calculate_log2_keysize(9) == 4, "9 -> bit_length(8) == 4")
check(calculate_log2_keysize(15) == 4, "15 -> bit_length(14) == 4")
check(calculate_log2_keysize(16) == 4, "16 -> bit_length(15) == 4（不是 5）")
check(calculate_log2_keysize(17) == 5, "17 -> bit_length(16) == 5")
check(calculate_log2_keysize(24) == 5, "24 -> bit_length(23) == 5")
check(calculate_log2_keysize(25) == 5, "25 -> bit_length(24) == 5（得 32）")
check(calculate_log2_keysize(43) == 6, "43 -> bit_length(42) == 6（得 64）")

# -------------------------------------------------- 三、estimate_log2_keysize
# (n*3+1)//2 再走同一函数；得到的表必须真能装下 n 个
for n in range(1, 200):
    k = estimate_log2_keysize(n)
    check(
        usable_fraction(1 << k) >= n,
        f"estimate_log2_keysize({n}) == {k} 的容量 {(1 << k)} 装不下 {n}",
    )
check(estimate_log2_keysize(1) == 3, "n=1 -> (1*3+1)//2 = 2 -> 被抬到 8 -> 3")
check(estimate_log2_keysize(5) == 3, "n=5 -> (16)//2 = 8 -> 3（容量 5 刚好装满）")
check(estimate_log2_keysize(6) == 4, "n=6 -> (19)//2 = 9 -> 4（容量 10）")
check(estimate_log2_keysize(25) == 6, "n=25 -> (76)//2 = 38 -> 6（容量 42）")

# ---------------------------------------------------------- 四、GROWTH_RATE
check(growth_rate(5) == 15, "GROWTH_RATE = used*3")
check(growth_rate(85) == 255, "used=85 -> 255 -> calculate 得 256")
# 无删除时 used*3 正好让表翻倍：used = USABLE_FRACTION(2^k) 时
check(
    calculate_log2_keysize(growth_rate(usable_fraction(1 << 5))) == 6,
    "used=21(=USABLE(32)) 时 used*3=63 -> bit_length(62)=6 -> 64，正好翻倍",
)
for k in range(3, 20):
    used = usable_fraction(1 << k)
    check(
        calculate_log2_keysize(growth_rate(used)) == k + 1,
        f"k={k}: 无删除扩容应恰好翻倍",
    )

# ------------------------------------------------------ 五、get_log2_bytes
# 每槽字节数 = 2**(log2_bytes - log2_size)
check(get_log2_bytes(3) == 3 and index_bytes_per_slot(3) == 1, "size 8 -> 1 字节/槽")
check(get_log2_bytes(7) == 7 and index_bytes_per_slot(7) == 1, "size 128 -> 1 字节/槽")
check(get_log2_bytes(8) == 9 and index_bytes_per_slot(8) == 2, "size 256 -> 2 字节/槽")
check(get_log2_bytes(15) == 16 and index_bytes_per_slot(15) == 2, "size 32768 -> 2 字节/槽")
check(get_log2_bytes(16) == 18 and index_bytes_per_slot(16) == 4, "size 65536 -> 4 字节/槽")
check(get_log2_bytes(31) == 33 and index_bytes_per_slot(31) == 4, "size 2^31 -> 4 字节/槽")
check(get_log2_bytes(32) == 35 and index_bytes_per_slot(32) == 8, "size 2^32 -> 8 字节/槽")
# 分档边界：8 与 16 与 32 都往上一档
check(
    [index_bytes_per_slot(k) for k in (3, 7, 8, 15, 16, 31, 32)]
    == [1, 1, 2, 2, 4, 4, 8],
    "索引宽度随 log2_size 单调分四档",
)

# --------------------------------------------------------- 六、探测序列
check(
    documented_probe_order(8) == [0, 1, 6, 7, 4, 5, 2, 3],
    "源码注释的 2**3 探测序：0->1->6->7->4->5->2->3",
)
# 起始位是 hash & mask，且第一跳**不加** perturb
check(list(probe_indices(0b1011, 8))[0] == 0b011, "起始 i = hash & mask")
# 纯 5*j+1（hash=0，perturb 恒 0）必须遍历全部 2**k 个槽
for k in range(3, 12):
    seq = list(probe_indices(0, 1 << k, limit=1 << k))
    check(len(set(seq)) == 1 << k, f"k={k}: 5*j+1 递推应在 2**{k} 上生成全部槽位")
    check(set(seq) == set(range(1 << k)), f"k={k}: 探测序应是槽位的一个排列")

# perturb 右移若干轮后必然归零 —— 归零后再走就是纯 5*j+1
h = 0xDEADBEEFCAFEF00D
p = h
rounds = 0
while p != 0:
    p >>= 5
    rounds += 1
check(rounds == 13, f"64 位 hash 右移 5 位需 13 轮归零（实测 {rounds}）")
# 归零之后的一段必须等于纯 5*j+1 的一段
tail_actual = list(probe_indices(h, 1024, limit=13 + 8))[13:]
i0 = tail_actual[0]
tail_expected = []
i = i0
for _ in range(8):
    tail_expected.append(i)
    i = (i * 5 + 1) & 1023
check(tail_actual == tail_expected, "perturb 归零后退化成纯 5*j+1 递推")

# --------------------------------------------------- 七、插入 / 扩容触发
d = CompactDict()
check(d.snapshot()["dk_size"] == 8 and d.capacity == 5, "新表 size=8、capacity=5")
for n in range(5):
    d.insert(f"k{n}", n * 7919)
check(d.ma_used == 5 and d.dk_usable == 0 and d.resizes == 0,
      "装到 capacity=5 仍未扩容，dk_usable 归零")
d.insert("k5", 5 * 7919)
check(d.resizes == 1, "第 6 个 key 触发扩容（dk_usable <= 0）")
check(d.dk_size == 16, "GROWTH_RATE(5)=15 -> calculate(15)=4 -> size 16")
check(d.dk_usable == 10 - 6 and d.dk_nentries == 6,
      "扩容后 dk_usable = USABLE(16) - 6 = 4，nentries = 6")
check(d.iteration_order() == [f"k{n}" for n in range(6)],
      "迭代顺序 = 插入顺序（扩容后仍保持）")

# 无删除序列下的扩容点：capacity 到达即翻倍
d2 = CompactDict()
sizes_at = {}
for n in range(1, 60):
    d2.insert(f"x{n}", n * 104729)
    sizes_at[n] = d2.dk_size
check(sizes_at[5] == 8, "5 个还在 8")
check(sizes_at[6] == 16, "第 6 个跳到 16")
check(sizes_at[11] == 32, "第 11 个跳到 32（USABLE(16)=10 满）")
check(sizes_at[22] == 64, "第 22 个跳到 64（USABLE(32)=21 满）")
check(sizes_at[43] == 128, "第 43 个跳到 128（USABLE(64)=42 满）")

# ------------------------------------------- 八、删除不回增 dk_usable
d3 = CompactDict()
for n in range(5):
    d3.insert(f"a{n}", n)
before = d3.snapshot()
check(before["dk_usable"] == 0, "5 个插满 8 槽表，usable 归零")
d3.delete("a0", 0)
d3.delete("a1", 1)
check(d3.ma_used == 3, "used 减到 3")
check(d3.dk_size == 8, "删除既不扩容也不缩表（表仍是 8）")
check(d3.dk_usable == 0, "删除**不**回增 dk_usable（源码注释明确）")
check(d3.dk_nentries == 5, "dk_nentries 仍是 5（洞留在 entries 里）")
check(d3.dummy_count() == 2, "索引表里留下 2 个 DKIX_DUMMY")
# 此时插入仍会扩容，而且因为 used=3，算出来的新表反而更小
d3.insert("a5", 5)
check(d3.resizes == 1, "删除后插入照样扩容")
check(d3.dk_size == 16, "GROWTH_RATE(3)=9 -> calculate(9)=bit_length(8)=4 -> 16")
check(d3.dk_nentries == 4, "压实后只剩 3 个老条目 + 1 个新条目")

# ------------------------------------------------ 九、扩容会把表"压实变小"
d4 = CompactDict(log2_size=6)  # size 64，capacity 42
for n in range(42):
    d4.insert(f"b{n}", n * 31)
check(d4.dk_size == 64 and d4.dk_usable == 0, "64 槽表装满 42 个")
for n in range(36):
    d4.delete(f"b{n}", n * 31)
check(d4.ma_used == 6 and d4.dk_nentries == 42 and d4.dummy_count() == 36,
      "删掉 36 个：used=6、nentries 仍是 42、36 个 dummy")
d4.insert("b42", 42 * 31)
check(d4.dk_size == 32, "GROWTH_RATE(6)=18 -> calculate(18)=bit_length(17)=5 -> 32（比原表 64 更小）")
check(d4.dk_nentries == 7, "压实后 nentries == used（洞被挤掉）")
check(d4.dummy_count() == 0, "压实后一个 dummy 都不剩")
check(d4.iteration_order() == [f"b{n}" for n in range(36, 42)] + ["b42"],
      "压实保持剩余条目的相对顺序")

# ------------------------------------------------------ 十、查找与空槽终止
d5 = CompactDict()
for n in range(5):
    d5.insert(f"c{n}", n * 17)
check(d5.lookup("c3", 3 * 17) == ("c3", 51), "存在的 key 能查到")
check(d5.lookup("zz", 999) is None, "不存在的 key 返回 None（撞 EMPTY 即停）")
d5.delete("c2", 2 * 17)
check(d5.lookup("c2", 2 * 17) is None, "删除后查不到")
check(d5.lookup("c3", 3 * 17) == ("c3", 51), "dummy 不阻断后续探测：c3 仍可查")
d5.insert("c2b", 2 * 17)
check(d5.lookup("c2b", 34) == ("c2b", 34), "同 hash 的新 key 可以复用 dummy 之后的空槽")

# -------------------------------------------- 十一、索引宽度的实际内存账
d6 = CompactDict(log2_size=3)
check(d6.index_bytes() == 1, "size 8 索引用 1 字节，能表示 -2..4")
# size 256（log2=8）时索引变 2 字节，即便 capacity 只有 170
d7 = CompactDict(log2_size=8)
check(d7.dk_size == 256 and d7.index_bytes() == 2, "size 256 索引已是 2 字节/槽")
check(usable_fraction(256) == 170, "size 256 的 capacity 是 170，索引只需 0..169 却用 2 字节")

print(f"OK: {PASS} assertions passed")
sys.exit(0)
