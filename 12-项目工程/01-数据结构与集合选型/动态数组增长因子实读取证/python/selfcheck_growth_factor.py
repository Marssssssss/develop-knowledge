"""664 自检：六家标准库的每个数字都来自实读源码。"""

from __future__ import annotations

import math
import sys

from growth_factor import (
    GO_THRESHOLD,
    GROWERS,
    amortized_moves,
    append_sequence,
    cpython_list_resize,
    go_nextslicecap,
    golden_ratio,
    growth_points,
    libcpp_recommend,
    libstdcxx_check_len,
    msvc_calculate_growth,
    peak_memory_ratio,
    reuse_generation,
    reuse_generation_ceil,
    reuse_threshold_root,
    rust_grow_amortized,
    rust_min_non_zero_cap,
)

PASS = 0


def check(cond, msg):
    global PASS
    assert cond, msg
    PASS += 1


# ------------------------------------------------------ 一、libstdc++ 2×
check(libstdcxx_check_len(0, 1) == 1, "空 vector push_back 第一次得 1（size 0 + max(1,0)）")
check(libstdcxx_check_len(1, 1) == 2, "size=1 push_back -> 1 + max(1,1) = 2")
check(libstdcxx_check_len(2, 1) == 4, "size=2 push_back -> 2 + max(1,2) = 4（**翻倍**）")
check(libstdcxx_check_len(100, 1) == 200, "size=100 -> 200")
check(libstdcxx_check_len(100, 300) == 400, "一次插 300 个：100 + 300 = 400（精确，不超额）")
check(libstdcxx_check_len(100, 30) == 200, "一次插 30 个：30 < 100 ⇒ 抬到 100 ⇒ 200")
try:
    libstdcxx_check_len(10, 5, max_size=12)
    raise AssertionError("room 不足应抛 length_error")
except OverflowError:
    PASS += 1

# ---------------------------------------------------------- 二、libc++ 2×
check(libcpp_recommend(1, 0) == 1, "cap=0, new_size=1 -> max(0, 1) = 1")
check(libcpp_recommend(2, 1) == 2, "cap=1, new_size=2 -> max(2, 2) = 2")
check(libcpp_recommend(3, 2) == 4, "cap=2, new_size=3 -> max(4, 3) = 4")
check(libcpp_recommend(101, 100) == 200, "cap=100 -> 200，也是 2×")
# 与大块请求取 max，不是简单翻倍
check(libcpp_recommend(1000, 100) == 1000, "cap=100 但要 1000 ⇒ max(200, 1000) = 1000")
# cap 接近 max_size/2 时直接给 max_size
check(libcpp_recommend(60, 50, max_size=100) == 100, "cap 50 >= max_size/2 50 ⇒ 直接给 max_size")
# 辟谣：libc++ 源码是 2×，不是流传的 1.5×
seq = append_sequence("libc++", 40)
check(seq[:4] == [1, 2, 4, 4], f"libc++ 容量序列前四个应为 1,2,4,4，实得 {seq[:4]}")

# ------------------------------------------------------------- 三、MSVC 1.5×
check(msvc_calculate_growth(1, 0) == 1, "cap=0: geometric=0 < 1 ⇒ 给 1")
check(msvc_calculate_growth(2, 1) == 2, "cap=1: 1 + 0 = 1 < 2 ⇒ 给 2")
check(msvc_calculate_growth(3, 2) == 3, "cap=2: 2 + 1 = 3 ⇒ 3（**不是 4**）")
check(msvc_calculate_growth(4, 3) == 4, "cap=3: 3 + 1 = 4 ⇒ 4")
check(msvc_calculate_growth(5, 4) == 6, "cap=4: 4 + 2 = 6 ⇒ 6")
check(msvc_calculate_growth(101, 100) == 150, "cap=100 -> 150")
check(msvc_calculate_growth(1000, 100) == 1000, "geometric 150 < 1000 ⇒ 精确给 1000")
check(msvc_calculate_growth(10, 80, max_size=100) == 100, "80 > 100-40=60 ⇒ 溢出保护给 max_size")
seq = append_sequence("MSVC", 40)
check(seq[:6] == [1, 2, 3, 4, 6, 6], f"MSVC 容量序列前六个应为 1,2,3,4,6,6，实得 {seq[:6]}")

# ------------------------------------------------------------------ 四、Go
check(go_nextslicecap(1, 0) == 1, "oldCap=0: doublecap=0 < 1 ⇒ 给 1")
check(go_nextslicecap(2, 1) == 2, "oldCap=1 < 256 ⇒ 翻倍到 2")
check(go_nextslicecap(3, 2) == 4, "oldCap=2 ⇒ 4")
check(go_nextslicecap(257, 256) == 512, "oldCap=256 不再直接翻倍，第一轮循环给 512")
check(go_nextslicecap(1000, 0) == 1000, "newLen 远超 doublecap ⇒ 精确给 newLen")
# 256 之后的平滑过渡：ratio 单调降到 1.25
cap = 256
ratios = []
for _ in range(6):
    nxt = go_nextslicecap(cap + 1, cap)
    ratios.append(nxt / cap)
    cap = nxt
check(ratios[0] == 2.0, f"第一次仍是 2×，实得 {ratios[0]}")
check(all(ratios[i] > ratios[i + 1] for i in range(len(ratios) - 1)), "之后比值单调下降")
check(ratios[-1] < 1.35, f"若干轮后逼近 1.25，实得 {ratios[-1]:.4f}")
check(ratios[-1] > 1.25, "但恒大于 1.25（因为每轮还加了一个 192 的常数项）")
# 常数项来自 (3 * threshold) >> 2 = 192
check((3 * GO_THRESHOLD) >> 2 == 192, "3*256/4 = 192")

# -------------------------------------------------------------- 五、CPython
# 源码注释给的序列：0, 4, 8, 16, 24, 32, 40, 52, 64, 76
seq = append_sequence("CPython", 80)
pts = growth_points(seq)
first_caps = [c for _, _, c in pts]
check(first_caps[:9] == [4, 8, 16, 24, 32, 40, 52, 64, 76],
      f"CPython 分配序列应为 4,8,16,24,32,40,52,64,76，实得 {first_caps[:9]}")
check(pts[0][0] == 1, "第 1 次 append 就拿到 4 个槽")
check(pts[1][0] == 5, "第 5 次 append 从 4 抬到 8")
check(pts[2][0] == 9, "第 9 次 append 从 8 抬到 16")
check(pts[3][0] == 17, "第 17 次 append 从 16 抬到 24")
check(pts[4][0] == 25, "第 25 次 append 从 24 抬到 32")
# 旁路：缩到一半以下才真缩
check(cpython_list_resize(10, 100, 10) == 16, "allocated=100、newsize=10 ⇒ 缩到 16")
check(cpython_list_resize(60, 100, 60) == 100, "newsize=60 >= 100>>1=50 ⇒ 旁路不动")
check(cpython_list_resize(49, 100, 49) == 60, "newsize=49 < 50 ⇒ 真缩：(49+6+6)=61 -> 60")
# 覆盖分支：一次扩张很大时改成精确分配
check(cpython_list_resize(100, 8, 4) == 100, "extend 到 100：96 > 16 ⇒ 精确给 100")
check(cpython_list_resize(9, 8, 8) == 16, "普通 append：1 > 7 不成立 ⇒ 走主式")
# 主式手算
check(((65 + (65 >> 3) + 6)) & ~3 == 76, "newsize=65 -> (65+8+6)=79 -> &~3 = 76")
check(((41 + (41 >> 3) + 6)) & ~3 == 52, "newsize=41 -> (41+5+6)=52 -> 52")
check(((53 + (53 >> 3) + 6)) & ~3 == 64, "newsize=53 -> (53+6+6)=65 -> 64")
check(cpython_list_resize(0, 4, 4) == 0, "newsize == 0 ⇒ 0")

# ---------------------------------------------------------------- 六、Rust
check(rust_min_non_zero_cap(1) == 8, "元素 1 字节 ⇒ 8")
check(rust_min_non_zero_cap(8) == 4, "元素 8 字节（<=1024）⇒ 4")
check(rust_min_non_zero_cap(1024) == 4, "1024 字节仍属第二档（<=）")
check(rust_min_non_zero_cap(1025) == 1, "超过 1024 ⇒ 1")
check(rust_grow_amortized(0, 0, 1, 1) == 8, "Vec<u8> 首次分配直接给 8")
check(rust_grow_amortized(0, 0, 1, 8) == 4, "Vec<u64> 首次分配给 4")
check(rust_grow_amortized(0, 0, 1, 4096) == 1, "大元素首次只给 1")
check(rust_grow_amortized(8, 8, 1, 1) == 16, "cap 8 -> max(16, 9) = 16")
check(rust_grow_amortized(100, 100, 1, 8) == 200, "cap 100 -> max(200, 101) = 200")
check(rust_grow_amortized(100, 100, 500, 8) == 600, "一次 reserve 500 ⇒ max(200, 600) = 600")
# 元素大小只影响下限，超过之后就纯 2×
seq8 = append_sequence("Rust<u64>", 40)
check(seq8[:5] == [4, 4, 4, 4, 8], f"Vec<u64> 前五次应为 4,4,4,4,8，实得 {seq8[:5]}")

# ------------------------------------- 七、六家的扩容点与摊销搬移
for name in GROWERS:
    s = append_sequence(name, 1000)
    check(s == sorted(s), f"{name} 的容量序列必须单调不减")
    check(s[-1] >= 1000, f"{name} 最终容量要装得下 1000 个")
    moves = amortized_moves(s)
    check(moves >= 1000, f"{name} 至少搬了 1000 次（每个元素至少落位一次）")
    check(moves < 10 * 1000, f"{name} 的摊销搬移应远小于 10N，实得 {moves}")

libstdc_moves = amortized_moves(append_sequence("libstdc++", 1000))
msvc_moves = amortized_moves(append_sequence("MSVC", 1000))
cpython_moves = amortized_moves(append_sequence("CPython", 1000))
check(libstdc_moves < msvc_moves < cpython_moves,
      f"同样 N 次 append，倍数越大搬得越少：{libstdc_moves} < {msvc_moves} < {cpython_moves}")
check(900 <= libstdc_moves <= 1100, f"2× 的摊销搬移约等于 N（等比求和），实得 {libstdc_moves}")
check(7000 <= cpython_moves <= 9000, f"CPython 约 1.125× ⇒ 摊销搬移约 7.5N，实得 {cpython_moves}")
check(1800 <= msvc_moves <= 2400, f"MSVC 1.5× ⇒ 摊销搬移约 2N，实得 {msvc_moves}")
check(msvc_moves < 3 * 1000, "1.5× 的摊销搬移 < 3N")

# ------------------------------- 八、峰值内存：扩容瞬间新旧两块同时在世
for name in GROWERS:
    r = peak_memory_ratio(append_sequence(name, 2000))
    check(r <= 3.0 + 1e-9, f"{name} 的瞬时峰值不超过 3× 稳态，实得 {r:.3f}")
check(abs(peak_memory_ratio(append_sequence("libstdc++", 2000)) - 1.5) < 1e-9,
      "2× 的峰值是 (1+2)/2 = 1.5×")
check(abs(peak_memory_ratio(append_sequence("MSVC", 2000)) - 1.75) < 1e-9,
      "1.5× 的峰值：小容量处整数截断给出 (3+4)/4 = 1.75，大容量处趋近 (1+1.5)/1.5 ≈ 1.667")
check(peak_memory_ratio(append_sequence("CPython", 2000)) > 1.88,
      "CPython 约 1.125× ⇒ 峰值接近 2×（扩容更频繁、每次更省）")

# --------------------------------------- 九、旧块复用：r=2 永远不可能
# 实数模型（不取整）：判据 r^i (2 - r) >= 1
check(reuse_generation(2.0) is None, "**2× 增长永远不会复用旧块**（左边恒为 0）")
check(reuse_generation(1.99) is not None, "1.99× 只是很晚才够用，而不是不行")
check(reuse_generation(1.9) == 4, "1.9× 第 4 次扩容起够用")
check(reuse_generation(1.8) == 3, "1.8× 第 3 次")
check(reuse_generation(1.7) == 3, "1.7× 第 3 次")
check(reuse_generation(1.5) == 2, "1.5× 从**第 2 次**扩容起就能复用")
check(reuse_generation(1.25) == 2, "1.25× 也是第 2 次")
check(reuse_generation(1.1) == 2, "1.1× 也是第 2 次（倍数越小越早）")
# 闭式判据 r^i (2-r) >= 1 与模拟必须一致
for r in (1.05, 1.1, 1.25, 1.5, 1.6, 1.7, 1.8, 1.9, 1.99):
    gen = reuse_generation(r)
    check(gen is not None, f"r={r} 最终一定能复用")
    check(r ** gen * (2 - r) >= 1, f"r={r}: 第 {gen} 次应满足 r^i(2-r) >= 1")
    if gen > 1:
        check(r ** (gen - 1) * (2 - r) < 1, f"r={r}: 第 {gen-1} 次还不满足")
# 取整模型：标准库的真实行为，小容量处会被抬高到 2×
check(reuse_generation_ceil(2.0) is None, "取整后 2× 依然永不复用")
check(reuse_generation_ceil(1.9) == 5, "**1.9× 取整后从第 5 次才够**（ceil 把 s1..s4 抬成 2/4/8/16，比实数模型晚一档）")
check(reuse_generation_ceil(1.8) == 4, "1.8× 取整后第 4 次（ceil(1.8*8)=15 <= 7+8）")
check(reuse_generation_ceil(1.5) == 2, "1.5× 取整后仍是第 2 次")
# 取整只会让复用**更晚**：ceil 抬高了新块，放低了可用总量占比
for r in (1.25, 1.5, 1.7, 1.8, 1.9):
    ge, gc = reuse_generation(r), reuse_generation_ceil(r)
    check(gc is not None and gc >= ge, f"r={r}: 取整代次 {gc} >= 实代数次 {ge}")
# 黄金比例：r^2 (2-r) = 1 的正根，即"第 2 次扩容就能复用"的最大倍数
phi = reuse_threshold_root()
check(abs(phi - golden_ratio()) < 1e-12, "判据在 i=2 处的阈值就是黄金比例 φ")
check(abs(phi ** 2 * (2 - phi) - 1) < 1e-9, f"φ^2(2-φ) = 1（φ={phi:.6f}）")
check(reuse_generation(phi - 1e-6) == 2, "略小于 φ 时第 2 次就够")
check(reuse_generation(phi + 1e-6) == 3, f"略大于 φ 只能推迟到第 3 次（实得 {reuse_generation(phi + 1e-6)}）")
# 因式分解验证：r^2(2-r) - 1 = -(r-1)(r^2 - r - 1)
for r in (1.0, 1.3, 1.618, 2.0):
    lhs = r ** 2 * (2 - r) - 1
    rhs = -(r - 1) * (r * r - r - 1)
    check(abs(lhs - rhs) < 1e-9, f"r={r}: r^2(2-r)-1 == -(r-1)(r^2-r-1)")
check(abs(golden_ratio() ** 2 - golden_ratio() - 1) < 1e-12, "φ^2 = φ + 1")
# CPython 的实际序列也要能算
cseq = append_sequence("CPython", 200)
check(cseq[-1] >= 200, "CPython 装得下 200 个")
check(len(set(cseq)) >= 8, "CPython 在 200 个以内至少扩了 8 次")

print(f"OK: {PASS} assertions passed")
sys.exit(0)
