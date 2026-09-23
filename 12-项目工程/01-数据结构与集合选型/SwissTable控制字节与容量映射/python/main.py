"""663 演示入口：把 Abseil 与 hashbrown 的"同族不同参"摆在一起。"""

from __future__ import annotations

from swiss_table import (
    ABSL_DELETED,
    ABSL_EMPTY,
    ABSL_SENTINEL,
    HB_DELETED,
    HB_EMPTY,
    MASK64,
    absl_h2,
    absl_probe_seq,
    as_u8,
    capacity_to_growth,
    hb_bucket_mask_to_capacity,
    hb_capacity_to_buckets,
    hb_probe_seq,
    hb_tag_full,
    max_capacity_for_load_factor_one,
    normalize_capacity,
    size_to_capacity,
)


def show_sentinels() -> None:
    print("== 控制字节哨兵：两派正好互换 ==")
    print(f"  Abseil    kEmpty=0x{as_u8(ABSL_EMPTY):02X}  kDeleted=0x{as_u8(ABSL_DELETED):02X}"
          f"  kSentinel=0x{as_u8(ABSL_SENTINEL):02X}")
    print(f"  hashbrown EMPTY =0x{HB_EMPTY:02X}  DELETED  =0x{HB_DELETED:02X}  （没有 sentinel）")
    print("  Abseil 的 kEmpty 就是 hashbrown 的 DELETED；Abseil 的 kSentinel 就是 hashbrown 的 EMPTY")
    print("  两派都靠「最高位为 1 ⇒ special」这条判据，所以 full tag 都在 0x00..0x7F")


def show_hash_split() -> None:
    print("\n== 哈希切分：两派都取最高 7 位，Go 取最低 7 位 ==")
    for h in (0x7F, 0x80, 1 << 57, 0xFF00000000000000, MASK64):
        print(f"  hash=0x{h:016X}  Abseil H2=0x{absl_h2(h):02X}"
              f"  hashbrown tag=0x{hb_tag_full(h):02X}  Go h2=0x{h & 0x7F:02X}")


def show_capacity_shape() -> None:
    print("\n== 容量形状：Abseil 是 2^k-1（多一个 sentinel 槽），hashbrown 是 2^k ==")
    print("  Abseil NormalizeCapacity(n):")
    for n in (1, 2, 4, 8, 9, 100, 1000):
        print(f"    {n:>5} -> {normalize_capacity(n)}")
    print("  hashbrown capacity_to_buckets(cap)（元素 8 字节）:")
    for n in (1, 3, 7, 14, 15, 28, 100, 1000):
        print(f"    {n:>5} -> {hb_capacity_to_buckets(n)}")


def show_growth() -> None:
    print(f"\n== Abseil CapacityToGrowth（kMaxCapacityForLoadFactorOne="
          f"{max_capacity_for_load_factor_one(16)}）==")
    for cap_ in (3, 7, 15, 31, 63, 127, 255, 1023):
        print(f"  capacity={cap_:<6} growth={capacity_to_growth(cap_):<6}"
              f" ({capacity_to_growth(cap_) / cap_:.3f})")
    print("  小表（容量 < 15）一个空槽都不留；15..63 只留一个；再大就按 7/8")

    print("\n== hashbrown bucket_mask_to_capacity ==")
    for buckets in (4, 8, 16, 32, 128, 1024):
        mask = buckets - 1
        cap_ = hb_bucket_mask_to_capacity(mask)
        print(f"  桶数={buckets:<6} capacity={cap_:<6} ({cap_ / buckets:.3f})")


def show_sizetocapacity() -> None:
    print("\n== Abseil SizeToCapacity（装载率的反函数）==")
    for size in (1, 7, 8, 14, 15, 62, 63, 64, 100, 1000):
        cap_ = size_to_capacity(size)
        print(f"  size={size:<6} -> capacity={cap_:<6} growth={capacity_to_growth(cap_)}")


def show_probe() -> None:
    print("\n== 探测序列：两派的组起点完全一致 ==")
    a = list(absl_probe_seq(0, 127, kwidth=16, limit=8))
    b = list(hb_probe_seq(0, 127, width=16, limit=8))
    print(f"  Abseil   (掩码 127, 步长 16): {a}")
    print(f"  hashbrown(掩码 127, 步长 16): {b}")
    print(f"  相等={a == b}")
    c = list(absl_probe_seq(0, 127, kwidth=8, limit=8))
    print(f"  Abseil kWidth=8 (步长变 8)  : {c}")


if __name__ == "__main__":
    show_sentinels()
    show_hash_split()
    show_capacity_shape()
    show_growth()
    show_sizetocapacity()
    show_probe()
