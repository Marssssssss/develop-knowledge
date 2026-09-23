"""把六家标准库的增长策略并排打印出来，看「倍增」到底增了多少。"""

from growth_factor import (
    GROWERS,
    amortized_moves,
    append_sequence,
    golden_ratio,
    peak_memory_ratio,
    reuse_generation,
    reuse_generation_ceil,
)

N = 1000


def main() -> None:
    print("=" * 68)
    print(f"连续 append {N} 次：容量序列前 12 项 / 扩容次数 / 摊销搬移 / 峰值内存")
    print("=" * 68)
    for name in GROWERS:
        seq = append_sequence(name, N)
        head = ", ".join(str(c) for c in seq[:12])
        steps = sum(1 for i in range(1, len(seq)) if seq[i] != seq[i - 1])
        print(f"\n[{name}]")
        print(f"  容量前 12 项 : {head}")
        print(f"  扩容次数     : {steps}")
        print(f"  摊销搬移     : {amortized_moves(seq)} 个元素（{amortized_moves(seq)/N:.2f}×N）")
        print(f"  峰值内存比   : {peak_memory_ratio(seq):.3f}× 最终容量")

    print()
    print("=" * 68)
    print("旧块复用：判据 r^i (2 - r) >= 1")
    print("=" * 68)
    phi = golden_ratio()
    for r in (2.0, 1.9, 1.8, phi, 1.5, 1.25, 1.1):
        ge = reuse_generation(r)
        gc = reuse_generation_ceil(r)
        ge_txt = "永不复用" if ge is None else f"第 {ge} 次"
        gc_txt = "永不复用" if gc is None else f"第 {gc} 次"
        label = "φ" if abs(r - phi) < 1e-9 else f"{r:g}"
        print(f"  r = {label:<6} 实数模型 {ge_txt:<8} | 取整模型 {gc_txt}")
    print(f"\n  φ = {phi:.12f} 是「第 2 次扩容就能复用」的上界：r^2(2-r) = 1 的正根。")
    print("  r = 2 时左边恒为 0 —— 倍增策略在数学上永远无法复用旧块。")


if __name__ == "__main__":
    main()
