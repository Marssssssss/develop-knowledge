"""660 演示入口：把"dict 为什么这么快、什么时候会抖一下"跑给你看。"""

from __future__ import annotations

from cpython_dict import (
    CompactDict,
    calculate_log2_keysize,
    documented_probe_order,
    estimate_log2_keysize,
    growth_rate,
    index_bytes_per_slot,
    usable_fraction,
)


def show_probe() -> None:
    print("== 探测序列（源码注释里那张表） ==")
    print("  size=8, hash=0 :", " -> ".join(map(str, documented_probe_order(8))))
    print("  size=16, hash=0:", " -> ".join(map(str, documented_probe_order(16))))
    print("  注意：起始位 = hash & mask，之后每轮先 perturb >>= 5 再 i = (i*5+perturb+1) & mask")


def show_growth() -> None:
    print("\n== 无删除时的扩容轨迹（capacity 一满就翻倍） ==")
    d = CompactDict()
    prev = d.dk_size
    print(f"  start      size={prev:<5} capacity={d.capacity}")
    for n in range(1, 44):
        d.insert(f"k{n}", n * 104729)
        if d.dk_size != prev:
            print(f"  第 {n:>2} 个插入 size {prev} -> {d.dk_size}  (GROWTH_RATE({n-1})={growth_rate(n-1)})")
            prev = d.dk_size


def show_delete_does_not_free() -> None:
    print("\n== 删除不会腾出槽位 ==")
    d = CompactDict()
    for n in range(5):
        d.insert(f"a{n}", n)
    print("  插满 5 个:", d.snapshot())
    d.delete("a0", 0)
    d.delete("a1", 1)
    print("  删掉 2 个:", d.snapshot())
    d.insert("a5", 5)
    print("  再插 1 个:", d.snapshot())
    print("  -> dk_usable 删完仍是 0，所以下一次插入照样走 dictresize")


def show_shrink_on_resize() -> None:
    print("\n== 扩容反而把表变小（压实） ==")
    d = CompactDict(log2_size=6)
    for n in range(42):
        d.insert(f"b{n}", n * 31)
    print(f"  64 槽装满 42 个: size={d.dk_size} nentries={d.dk_nentries} used={d.ma_used}")
    for n in range(36):
        d.delete(f"b{n}", n * 31)
    print(f"  删掉 36 个     : size={d.dk_size} nentries={d.dk_nentries} used={d.ma_used} dummies={d.dummy_count()}")
    d.insert("b42", 42 * 31)
    print(f"  再插 1 个       : size={d.dk_size} nentries={d.dk_nentries} used={d.ma_used} dummies={d.dummy_count()}")
    print(f"  -> GROWTH_RATE(6)={growth_rate(6)} -> log2={calculate_log2_keysize(growth_rate(6))} -> size={1 << calculate_log2_keysize(growth_rate(6))}")


def show_index_width() -> None:
    print("\n== 索引槽宽度（1/2/4/8 字节） ==")
    for k in (3, 8, 16, 32):
        size = 1 << k
        print(
            f"  log2_size={k:>2} size={size:<12} 索引 {index_bytes_per_slot(k)} 字节/槽"
            f"  capacity={usable_fraction(size):<10}"
            f"  索引表 {size * index_bytes_per_slot(k)} 字节"
        )


def show_presized() -> None:
    print("\n== 预分配：estimate_log2_keysize(n) = calculate_log2_keysize((n*3+1)//2) ==")
    for n in (1, 5, 6, 10, 21, 100, 1000):
        k = estimate_log2_keysize(n)
        print(f"  n={n:<5} -> size={1 << k:<6} capacity={usable_fraction(1 << k)}")


if __name__ == "__main__":
    show_probe()
    show_growth()
    show_delete_does_not_free()
    show_shrink_on_resize()
    show_index_width()
    show_presized()
