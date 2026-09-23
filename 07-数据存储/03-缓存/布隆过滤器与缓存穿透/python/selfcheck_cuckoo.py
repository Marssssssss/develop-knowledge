"""自检：布谷鸟过滤器（cuckoo.py）。指纹/备选桶用确定性哈希值核对。"""

from cuckoo import (
    ALT_HASH_MULTIPLIER, CF_MAX_NUM_BUCKETS, COMPACT_DELETE_RATIO, CUCKOO_NULLFP,
    DEFAULT_CF_BUCKET_SIZE, DEFAULT_CF_MAX_ITERATIONS, EXISTS, INSERTED, NO_SPACE,
    CuckooError, CuckooFilter, get_alt_hash, get_lookup_params, get_next_n2,
)

PASS = 0
FAIL = []


def check(label, got, expect):
    global PASS
    if got == expect:
        PASS += 1
    else:
        FAIL.append(f"{label}: got {got!r}, expect {expect!r}")


def raises(label, fn, *a, **kw):
    global PASS
    try:
        fn(*a, **kw)
    except CuckooError:
        PASS += 1
    except Exception as e:
        FAIL.append(f"{label}: raised {type(e).__name__} instead of CuckooError")
    else:
        FAIL.append(f"{label}: expected CuckooError")


# ------------------------------------------------------------ getNextN2
check("getNextN2(0) → 0（由调用方兜底为 1）", get_next_n2(0), 0)
check("getNextN2(1) → 1", get_next_n2(1), 1)
check("getNextN2(2) → 2", get_next_n2(2), 2)
check("getNextN2(3) → 4", get_next_n2(3), 4)
check("getNextN2(5) → 8", get_next_n2(5), 8)
check("getNextN2(1000) → 1024", get_next_n2(1000), 1024)
check("getNextN2(1024) → 1024（已是 2 的幂则不变）", get_next_n2(1024), 1024)

# -------------------------------------------------------- 指纹与备选桶
check("乘数常量 = 0x5BD1E995", ALT_HASH_MULTIPLIER, 0x5BD1E995)
check("乘数常量的十进制值", ALT_HASH_MULTIPLIER, 1540483477)
h1, h2, fp = get_lookup_params(0)
check("fp(0) = 0%255+1", fp, 1)
check("h1 就是原哈希", h1, 0)
check("h2 = h1 ^ (fp*0x5bd1e995)", h2, 0 ^ (1 * ALT_HASH_MULTIPLIER))
_, _, fp255 = get_lookup_params(254)
check("fp(254) = 254%255+1", fp255, 255)
_, _, fp256 = get_lookup_params(255)
check("fp(255) = 0+1 → 指纹永不等于 NULLFP(0)", fp256, 1)
check("指纹取值恒在 1..255",
      all(get_lookup_params(x)[2] >= 1 for x in range(4096)), True)
check("getAltHash 是对合的（同一 fp 用两次回到原点）",
      get_alt_hash(7, get_alt_hash(7, 12345)), 12345)

# ------------------------------------------------------------ 初始化
cf = CuckooFilter(1024, bucket_size=2, max_iterations=20, expansion=1)
check("numBuckets = getNextN2(1024/2)", cf.num_buckets, 512)
check("起手 1 个子过滤器", cf.num_filters, 1)
check("expansion 也被取到 2 的幂", cf.expansion, 1)
check("数据槽总数 = numBuckets*bucketSize", len(cf.filters[0].data), 1024)
cf2 = CuckooFilter(4096, bucket_size=2, expansion=4)
check("expansion=4 保持 4", cf2.expansion, 4)
cf3 = CuckooFilter(10, bucket_size=2)
check("小数容量也向上取到 2 的幂", cf3.num_buckets, 8)
raises("capacity < bucketSize*2 被拒", CuckooFilter, 3, 2)

# --------------------------------------------------- 插入 / 查询 / 计数
f = CuckooFilter(64, bucket_size=2, max_iterations=20, expansion=0)
check("插入成功返回 INSERTED", f.insert(1001), INSERTED)
check("插入后能查到", f.check(1001), True)
check("计数为 1", f.count(1001), 1)
check("insert 不查重 → 允许重复写入", f.insert(1001), INSERTED)
check("重复写入后计数变 2", f.count(1001), 2)
check("num_items 也累加了两次", f.num_items, 2)
check("insert_unique 对已存在返回 EXISTS", f.insert_unique(1001), EXISTS)
check("从未插入的返回 False", f.check(999999), False)

# ---------------------------------------- 确定性假阳性：同指纹 + 同候选桶
# numBuckets=8（2 的幂），fp = hash%255+1。取 x=0 与 y=2040：
#   2040 % 255 == 0  → 指纹同为 1；2040 % 8 == 0 → h1 落进同一个桶。
g = CuckooFilter(16, bucket_size=2, expansion=0)
check("构造用过滤器的桶数", g.num_buckets, 8)
check("0 与 2040 指纹相同", get_lookup_params(0)[2], get_lookup_params(2040)[2])
check("0 与 2040 的 h1 桶下标相同",
      g.filters[0].index_of(get_lookup_params(0)[0]),
      g.filters[0].index_of(get_lookup_params(2040)[0]))
check("插入 0 之前查 2040 为假", g.check(2040), False)
g.insert(0)
check("插入 0 之后查 2040 为真（确定性假阳性）", g.check(2040), True)

# ------------------------------------------------------------ 删除与压缩
d = CuckooFilter(64, bucket_size=2, expansion=0)
for i in range(5):
    d.insert(7000 + i)
check("插入 5 个", d.num_items, 5)
check("删除命中", d.delete(7000), True)
check("删除后 num_items", d.num_items, 4)
check("删除后 num_deletes", d.num_deletes, 1)
check("删除后查不到", d.check(7000), False)
check("删除不存在的返回 False", d.delete(999999), False)
check("单子过滤器不会触发压缩", d.compacted, 0)

# 多子过滤器的压缩触发：删除量 > numItems*0.10（成对）
two = CuckooFilter(8, bucket_size=2, expansion=2)
while two.num_filters < 2:
    two.insert(two.num_items * 7919 + 13)
check("已扩到 2 个子过滤器", two.num_filters, 2)
# 源码里 numItems 与 numDeletes **先各自更新再比较**，阈值要按更新后的值算
two.compacted = 0
two.insert(424242)
two.num_items = 100
two.num_deletes = 0
check("删除命中", two.delete(424242), True)
check("更新后 1 > 99*0.10 为假 → 不压缩", two.compacted, 0)

two.insert(424242)
two.num_items = 10
two.num_deletes = 0
two.delete(424242)
check("更新后 1 > 9*0.10 为真 → 触发压缩", two.compacted, 1)

# -------------------------------------------------- 扩容与 NoSpace（成对）
ns = CuckooFilter(8, bucket_size=2, max_iterations=1, expansion=0)
for i in range(1000):
    ns.insert(i * 104729)
    if ns.num_items >= 16:       # 8 桶 × 2 槽 = 16 个槽位
        break
check("expansion=0 且装满后返回 NO_SPACE", ns.insert(123456789), NO_SPACE)
check("装满后仍是 1 个子过滤器", ns.num_filters, 1)

grow = CuckooFilter(8, bucket_size=2, max_iterations=1, expansion=2)
for i in range(1000):
    grow.insert(i * 104729)
    if grow.num_filters >= 2:
        break
check("expansion=2 时会扩容", grow.num_filters, 2)
check("新子过滤器桶数 = 原 × expansion^numFilters",
      grow.filters[1].num_buckets, grow.filters[0].num_buckets * 2)

# ------------------------------------------------------------ 常量与默认值
check("NULLFP 为 0", CUCKOO_NULLFP, 0)
check("默认 bucketSize", DEFAULT_CF_BUCKET_SIZE, 2)
check("默认 maxIterations", DEFAULT_CF_MAX_ITERATIONS, 20)
check("CF_MAX_NUM_BUCKETS 是 56 位", CF_MAX_NUM_BUCKETS, (1 << 56) - 1)
check("压缩阈值", COMPACT_DELETE_RATIO, 0.10)

if FAIL:
    print(f"FAILED {len(FAIL)} / {PASS + len(FAIL)}")
    for f in FAIL:
        print("  -", f)
    raise SystemExit(1)
print(f"cuckoo selfcheck: {PASS} assertions passed")
