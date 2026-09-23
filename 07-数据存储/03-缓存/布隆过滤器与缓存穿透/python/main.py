"""demo 633 主程序：布隆过滤器与布谷鸟过滤器的容量账本与缓存穿透防护演示。"""

import math

from bloom import (
    BLOOM_OPT_FORCE64, BLOOM_OPT_NOROUND, Bloom, bf_reserve_validate, calc_bpe,
    sb_chain_add, sb_chain_check, sb_new_chain,
)
from cuckoo import CuckooFilter, get_lookup_params


def hv(i):
    """演示用的确定性哈希对：第 i 个元素占第 i*8 .. i*8+7 号位。"""
    return i * 8, 1


def main():
    print("=== 1. 位宽与哈希数：为什么 error 减半要付 1.5 倍代价 ===")
    for err in (0.1, 0.01, 0.001, 0.0001):
        bpe = calc_bpe(err)
        n = 10000
        bits = int(n * bpe)
        print(f"  error={err:<8} bpe={bpe:6.3f} bits/元素  "
              f"1万元素={bits:>7} 位 ≈ {bits / 8 / 1024:6.2f} KB  "
              f"hashes={math.ceil(math.log(2) * bpe)}")

    print("\n=== 2. BF.RESERVE 的参数命运 ===")
    for err, cap, kw in [(0.01, 100, {}), (0.5, 100, {}),
                         (0.01, 100, {"expansion": 0}), (0.01, 100, {"expansion": 4})]:
        try:
            e, c, x, opts = bf_reserve_validate(err, cap, **kw)
            nonscaling = "NONSCALING" if opts & 8 else "scaling"
            print(f"  error={err} cap={cap} {kw} -> error={e}, expansion={x}, {nonscaling}")
        except Exception as exc:
            print(f"  error={err} cap={cap} {kw} -> 拒绝：{exc}")

    print("\n=== 3. scalable 链：误差逐代收紧、容量按 growth 翻倍 ===")
    chain = sb_new_chain(100, 0.01)
    print(f"  首链: error={chain.cur.inner.error}, 容量={chain.cur.inner.entries}, "
          f"hashes={chain.cur.inner.hashes}, bits={chain.cur.inner.bits}")
    for i in range(100):
        sb_chain_add(chain, *hv(i))
    sb_chain_add(chain, *hv(100))
    for i, link in enumerate(chain.filters):
        print(f"  链[{i}]: error={link.inner.error:.4f}, 容量={link.inner.entries}, "
              f"hashes={link.inner.hashes}, 已装={link.size}")

    print("\n=== 4. 缓存穿透防护：先查过滤器，未命中直接返回 ===")
    # 模拟一批「数据库里真实存在的 key」(id 0..99) 与一批恶意随机 key
    seen = [sb_chain_check(chain, *hv(i)) for i in range(100)]
    print(f"  已写入的 100 个 key 全部命中过滤器: {all(seen)}")
    misses = [i for i in range(1000, 1100) if not sb_chain_check(chain, *hv(i))]
    print(f"  1000~1099 号 key 被判定「一定不存在」的有 {len(misses)} 个（其余为假阳性）")
    print(f"  -> 这些请求不必打到数据库，穿透流量在此被截断")

    print("\n=== 5. 布谷鸟过滤器：指纹、候选桶与踢出 ===")
    cf = CuckooFilter(1024, bucket_size=2, max_iterations=20, expansion=2)
    print(f"  容量 1024 / bucketSize 2 -> numBuckets={cf.num_buckets}, "
          f"槽位={cf.num_buckets * cf.bucket_size}")
    h1, h2, fp = get_lookup_params(123456)
    print(f"  hash=123456 -> fp={fp}, h1桶={h1 % cf.num_buckets}, h2桶={h2 % cf.num_buckets}")
    for i in range(300):
        cf.insert_unique(i * 2654435761 % (1 << 32))
    print(f"  插入 300 个不重复元素后: numItems={cf.num_items}, "
          f"子过滤器={cf.num_filters}")
    print(f"  查 1*2654435761 存在: {cf.check(1 * 2654435761 % (1 << 32))}")
    print(f"  查一个从未插入的值: {cf.check(0xDEADBEEF)}（False 表示确定不存在）")

    print("\n=== 6. 两者怎么选 ===")
    print("  布隆：只增不删、内存最省、跨 key 只做存在性判断（BF.ADD / BF.EXISTS）")
    print("  布谷鸟：支持删除（CF.DEL），代价是槽位按 2 的幂分配、装满前要踢来踢去")


if __name__ == "__main__":
    main()
