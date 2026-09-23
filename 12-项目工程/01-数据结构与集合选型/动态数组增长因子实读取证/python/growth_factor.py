"""六家标准库的动态数组增长策略：把「倍增」这件事从 folklore 变成源码级事实。

逐行对齐的原文（全部实读）：

- **libstdc++** `bits/stl_vector.h`：`_M_check_len` → `if (__n < size()) __n = size(); return size() + __n;`
- **libc++** `include/__vector/vector.h`：`__recommend` → `max(2 * __cap, __new_size)`
  （`__cap >= __ms / 2` 时直接给 `max_size`）
- **MSVC STL** `stl/inc/vector`：`_Calculate_growth` → `_Oldcapacity + _Oldcapacity / 2`（**1.5 倍**）
- **Go** `runtime/slice.go`：`nextslicecap` → 小于 256 翻倍，之后 `newcap += (newcap + 3*256) >> 2`
- **CPython** `Objects/listobject.c`：`list_resize` → `((newsize + (newsize>>3) + 6) & ~3)`，
  带一条「宁可精确分配」的覆盖分支与一条「缩到一半以下才真缩」的旁路
- **Rust** `library/alloc/src/raw_vec/mod.rs`：`grow_amortized` → `max(cap*2, required)`，
  再 `max(min_non_zero_cap(elem_size), cap)`，后者按元素大小取 8 / 4 / 1

附带的两个可算结论：
1. **摊销搬移次数**：`ceil` 出的容量序列决定了 N 次 append 总共搬了多少个元素；
2. **旧块复用**：令增长因子 r，第 i 次扩容时"已释放块之和"能否覆盖新块，
   判据是 `r^i · (2 - r) >= 1`。r = 2 时左边恒为 0，**永远不可能复用**；
   `r^2(2-r) = 1` 的正根恰好是黄金比例 φ ≈ 1.618。
"""

from __future__ import annotations

import math
from fractions import Fraction

# ------------------------------------------------------------- libstdc++
def libstdcxx_check_len(size: int, n: int, max_size: int = (1 << 62)) -> int:
    """`_M_check_len`：`if (__n < size()) __n = size(); ... return size() + __n;`

    注意是 `size() + max(n, size())`，也就是 **push_back 时恰好 2×**，
    而一次性 `insert` 大批元素时是 `size() + n`（精确，不超额）。
    """
    room = max_size - size
    if room < n:
        raise OverflowError("vector::_M_range_insert would exceed max_size")
    if n < size:
        n = size                      # Grow by (at least) doubling
    if n > room:
        n = room
    return size + n


# ----------------------------------------------------------------- libc++
def libcpp_recommend(new_size: int, cap: int, max_size: int = (1 << 62)) -> int:
    """`__recommend`：`max(2 * __cap, __new_size)`，`__cap >= __ms/2` 时直接给 max_size。"""
    if new_size > max_size:
        raise OverflowError("length_error")
    if cap >= max_size // 2:
        return max_size
    return max(2 * cap, new_size)


# ------------------------------------------------------------------- MSVC
def msvc_calculate_growth(new_size: int, cap: int, max_size: int = (1 << 62)) -> int:
    """`_Calculate_growth`：`_Oldcapacity + _Oldcapacity / 2`，即 **1.5×**。"""
    if cap > max_size - cap // 2:
        return max_size               # geometric growth would overflow
    geometric = cap + cap // 2
    if geometric < new_size:
        return new_size               # geometric growth would be insufficient
    return geometric


# --------------------------------------------------------------------- Go
GO_THRESHOLD = 256


def go_nextslicecap(new_len: int, old_cap: int) -> int:
    """`nextslicecap`：小于 256 翻倍，之后按 `newcap += (newcap + 3*256) >> 2` 平滑过渡。"""
    doublecap = old_cap + old_cap
    if new_len > doublecap:
        return new_len
    if old_cap < GO_THRESHOLD:
        return doublecap
    new_cap = old_cap
    for _ in range(64):
        new_cap += (new_cap + 3 * GO_THRESHOLD) >> 2
        if new_cap >= new_len:
            break
    if new_cap <= 0:                  # 溢出
        return new_len
    return new_cap


# ----------------------------------------------------------------- CPython
def cpython_list_resize(newsize: int, allocated: int, old_size: int) -> int:
    """`list_resize`：返回新的 `allocated`（不是"容量上限"而是"已分配槽数"）。

    三条规则：
      1. 旁路：`allocated >= newsize && newsize >= allocated>>1` ⇒ 不动（含缩容判据）
      2. 主式：`((size_t)newsize + (newsize >> 3) + 6) & ~3`
      3. 覆盖：若"这次增长的量"比"超额分配出来的量"还大 ⇒ 改成 `((size_t)newsize + 3) & ~3`
    """
    if allocated >= newsize and newsize >= (allocated >> 1):
        return allocated              # 含"缩到一半以下才真缩"的判据
    new_allocated = ((newsize + (newsize >> 3) + 6)) & ~3
    if newsize - old_size > (new_allocated - newsize):
        new_allocated = ((newsize + 3)) & ~3
    if newsize == 0:
        new_allocated = 0
    return new_allocated


# ------------------------------------------------------------------- Rust
def rust_min_non_zero_cap(elem_size: int) -> int:
    """`min_non_zero_cap`：size == 1 → 8；<= 1024 → 4；否则 1。"""
    if elem_size == 1:
        return 8
    if elem_size <= 1024:
        return 4
    return 1


def rust_grow_amortized(cap: int, length: int, additional: int, elem_size: int) -> int:
    """`grow_amortized`：`cap = max(cap*2, len+additional)`，再 `max(min_non_zero_cap, cap)`。"""
    required = length + additional
    cap = max(cap * 2, required)
    return max(rust_min_non_zero_cap(elem_size), cap)


# -------------------------------------------------------------- 统一驱动
GROWERS = {
    "libstdc++": lambda cap, size, n: libstdcxx_check_len(size, n),
    "libc++": lambda cap, size, n: libcpp_recommend(size + n, cap),
    "MSVC": lambda cap, size, n: msvc_calculate_growth(size + n, cap),
    "Go": lambda cap, size, n: go_nextslicecap(size + n, cap),
    "CPython": lambda cap, size, n: cpython_list_resize(size + n, cap, size),
    "Rust<u64>": lambda cap, size, n: rust_grow_amortized(cap, size, n, 8),
}


# CPython 的 `list_resize` 在每次 append 时都会被调用（内部自带"旁路"判据）；
# C++ / Go / Rust 只在容量真的不够时才走增长路径。
ALWAYS_CALL = {"CPython"}


def append_sequence(name: str, count: int) -> list[int]:
    """模拟从空容器开始连续 append `count` 次，返回每次操作后的容量。"""
    grow = GROWERS[name]
    cap, size = 0, 0
    out: list[int] = []
    for _ in range(count):
        if name in ALWAYS_CALL or size + 1 > cap:
            new_cap = grow(cap, size, 1)
            if new_cap != cap:
                cap = new_cap
        size += 1
        out.append(cap)
    return out


def growth_points(seq: list[int]) -> list[tuple[int, int, int]]:
    """返回 (第几次 append, 旧容量, 新容量) 的扩容点列表。"""
    pts: list[tuple[int, int, int]] = []
    prev = 0
    for i, cap in enumerate(seq, start=1):
        if cap != prev:
            pts.append((i, prev, cap))
            prev = cap
    return pts


def amortized_moves(seq: list[int]) -> int:
    """扩容时把旧元素搬到新块的总次数（每次搬 min(旧容量, 当前 size) 个）。"""
    total = 0
    prev = 0
    for i, cap in enumerate(seq, start=1):
        if cap != prev:
            total += min(prev, i - 1)     # 搬的是扩容前已有的元素
            prev = cap
    return total


def peak_memory_ratio(seq: list[int]) -> float:
    """扩容瞬间新旧两块同时在世 ⇒ 峰值 = (旧 + 新) / 新。"""
    worst = 1.0
    prev = 0
    for cap in seq:
        if cap != prev and prev > 0:
            worst = max(worst, (prev + cap) / cap)
        prev = cap
    return worst


# -------------------------------------------------- 旧块复用（碎片模型）
def reuse_generation(factor: float, start: float = 1.0, max_steps: int = 200) -> int | None:
    """第几次扩容起，"此前释放的所有块加起来"能覆盖新块。

    序列 `s_{i+1} = r · s_i`（这里按**实数**推演，不取整 —— 取整会把 1.9 变成 2.0，
    见下面 `reuse_generation_ceil`）。第 i 次扩容要 `s_i`，已释放的是 `s_0..s_{i-1}`，
    判据 `Σ_{j<i} s_j >= s_i` 化简后就是 `r^i (2 - r) >= 1`。
    """
    # 用 Fraction 做精确算术：float 在 i > 53 之后 `s_0+…+s_{i-1}` 会与 `s_i` 相等
    # （2^200 - 1 在 float 里就是 2^200），会把"永不复用"误判成"能复用"。
    r = Fraction(factor).limit_denominator(10 ** 12)
    freed = Fraction(0)
    cap = Fraction(start).limit_denominator(10 ** 12)
    for k in range(1, max_steps + 1):
        avail = freed + cap          # s_0 .. s_{k-1} 都已释放
        nxt = cap * r                # 这一轮要分配 s_k
        if avail >= nxt:
            return k
        freed = avail
        cap = nxt
    return None


def reuse_generation_ceil(factor: float, start: int = 1, max_steps: int = 64) -> int | None:
    """同上，但每步向上取整 —— 标准库真正的行为。

    取整会让小容量处的实际倍数被抬高到 2×（如 `ceil(1.9 * 1) = 2`），
    所以 r = 1.9 在真实实现里退化成"永不复用"。
    """
    freed = 0
    cap = start
    for k in range(1, max_steps + 1):
        avail = freed + cap
        nxt = math.ceil(factor * cap)
        if avail >= nxt:
            return k
        freed = avail
        cap = nxt
    return None


def golden_ratio() -> float:
    return (1 + math.sqrt(5)) / 2


def reuse_threshold_root() -> float:
    """解 `r^2 (2 - r) = 1`（即 `-(r-1)(r^2 - r - 1) = 0` 的正根）。"""
    return golden_ratio()
