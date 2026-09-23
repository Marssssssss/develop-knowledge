# 633 · 布隆过滤器与缓存穿透防护（RedisBloom）

> 归属：`07-数据存储/03-缓存`。缓存穿透指的是「反复查询一个根本不存在的 key」——缓存永远
> 不命中，压力全部落到数据库。布隆过滤器用极小的内存回答「这个 key 一定不存在吗」，
> 把这部分流量在入口就截断。本 demo 按 **RedisBloom 源码实读**（而不是教科书公式），
> 覆盖位宽/哈希数的真实计算、scalable 链的误差收紧、以及可删除的布谷鸟过滤器。

## 一、事实来源（本轮实读，非凭记忆）

| 来源 | 拿到什么 |
| --- | --- |
| `RedisBloom@master deps/bloom/bloom.h` | `struct bloom` 字段、`BLOOM_OPT_*` 四个选项、`BLOOM_MAX_HASHES` |
| `RedisBloom@master deps/bloom/bloom.c` 66-90 | `bloom_calc_hash64`（MurmurHash64A，种子 `0xc6a4a7935bd1e995`）、`CHECK_ADD_FUNC` 双哈希 |
| `RedisBloom@master deps/bloom/bloom.c` 120-127 | `calc_bpe`：`denom = 0.480453013918201`（即 ln(2)²） |
| `RedisBloom@master deps/bloom/bloom.c` 135-205 | `bloom_init` 三条分支与 64 位字对齐 |
| `RedisBloom@master deps/bloom/bloom.c` 203-233 | `bloom_check_h` / `bloom_add_h` 的分派（`n2 > 0` 用 `1<<n2`，否则用 `bits`） |
| `RedisBloom@master src/sb.h`、`src/sb.c` 29-140 | `SBLink` / `SBChain`、`ERROR_TIGHTENING_RATIO 0.5`、`SBChain_Add` 的扩容判定、`SB_NewChain` 首链误差 |
| `RedisBloom@master src/config.c` 20-64 | 8 个模块默认值（bf-error-rate 0.01 / bf-initial-size 100 / bf-expansion-factor 2 …） |
| `RedisBloom@master src/config.h` 22 | `BF_ERROR_RATE_CAP 0.25` |
| `RedisBloom@master src/rebloom.c` 137-205 | `BF.RESERVE` 的参数校验与 NONSCALING/EXPANSION 互斥 |
| `RedisBloom@master src/cuckoo.h`、`src/cuckoo.c` | 指纹/备选桶/`getNextN2`/踢出插入/删除压缩 |
| `RedisBloom@master src/rebloom.c` 115-121 | `cfCreate` 的 `capacity < bucketSize*2` 校验 |

## 二、核心机制

### 1. 位宽：`bpe = -ln(error) / ln(2)²`

```c
static double calc_bpe(double error) {
    static const double denom = 0.480453013918201; // ln(2)^2
    return -log(error) / denom;
}
```

| error | bpe（位/元素） | 1 万元素 | hashes = ⌈ln2·bpe⌉ |
| --- | --- | --- | --- |
| 0.1 | 4.793 | ≈ 5.85 KB | 4 |
| 0.01 | 9.585 | ≈ 11.70 KB | 7 |
| 0.001 | 14.378 | ≈ 17.55 KB | 10 |
| 0.0001 | 19.170 | ≈ 23.40 KB | 14 |

**误差每降一个数量级，只多付约 4.79 位/元素**（`ln(10)/ln(2)² ≈ 4.79`）——这是布隆过滤器
最划算的地方：想要 10 倍的精度，只要一半的内存增幅。

### 2. 三条初始化分支，RedisBloom 用的是 `NOROUND`

```c
if (options & BLOOM_OPT_ENTS_IS_BITS) { ... bits = 1 << n2; entries = bits / bpe; }
else if (options & BLOOM_OPT_NOROUND) { bits = entries * bpe; if (bits == 0) bits = 1; }
else {
    double bn2 = logb(entries * bpe);       /* 取整分支：向上取到 2 的幂 */
    n2 = bn2 + 1;
    bits = 1LLU << n2;
    size_t itemDiff = (bits - entries * bpe) / bpe;
    bloom->entries += itemDiff;             /* 多出来的位“认领”成额外容量 */
}
```

两处容易看漏：

- **取整分支会把向上取整多出来的位换算成额外容量**（`entries += itemDiff`）。同样
  `entries=100, error=0.01`，`NOROUND` 得到 960 位、容量仍是 100；取整分支得到 1024 位、
  容量变成 **106**。
- 无论哪条分支，最后都有 `bytes = ((bits/64)+1)*8 或 bits/8`，**字节数一律是 8 的倍数**
  （64 位字对齐），然后 `bits = bytes*8`。所以 `NOROUND` 算出的 958 位最终落地成 960 位。

RedisBloom 调用时传的是 `BLOOM_OPT_FORCE64 | scaling | BLOOM_OPT_NOROUND`，走第二条分支。

### 3. 双哈希：`(a + i·b) % mod`

```c
for (i = 0; i < bloom->hashes; i++) {
    T x = ((hashval.a + i * hashval.b)) % mod;
    ...
}
```

`a` 与 `b` 由同一份数据的两次 MurmurHash 得到（`b` 用 `a` 当种子）。分派规则是
**`n2 > 0` 用 `1<<n2`，否则用 `bloom->bits`** —— `NOROUND` 过滤器走后者。

`bloom_add_h` 返回的是 `!found_unset`，也就是**「元素此前已存在」为真**。这个符号方向在
`SBChain_AddToLink` 里又翻了一次（`if (!bloom_add_h(...)) { size++; return 1; }`），
读代码时极易搞反。

### 4. scalable 链：误差每代收紧一半

```c
#define ERROR_TIGHTENING_RATIO 0.5

// SB_NewChain：首链的 error 就已经乘过 tightening
double tightening = (options & BLOOM_OPT_NO_SCALING) ? 1 : ERROR_TIGHTENING_RATIO;
*err = SBChain_AddLink(sb, initsize, error_rate * tightening);

// SBChain_Add：当前链装满了才开新链
if (cur->size >= cur->inner.entries) {
    if (sb->options & BLOOM_OPT_NO_SCALING) return -2;          /* SB_FULL */
    if (sb->growth == 0 || cur->inner.entries > UINT64_MAX / sb->growth) return -1;
    double error = cur->inner.error * ERROR_TIGHTENING_RATIO;
    SBChain_AddLink(sb, cur->inner.entries * (uint64_t)sb->growth, error);
}
```

所以 `BF.RESERVE key 0.01 100` 建出来的**第一条链误差其实是 0.005**（不是 0.01），
`hashes` 也从 7 变成 8。溢出后再开第二条：误差 0.0025、容量 200。整条链的误差上界约为
`0.005 + 0.0025 + … < 0.01`，正好补回首链“透支”的那一半。

查询时会**遍历所有链**（从新到旧），任一命中即返回「见过」。

### 5. `BF.RESERVE` 的参数命运

- `error_rate` 必须在 `(0, 1)` **开区间**；**大于 0.25 会被静默截断到 0.25**（只打一条 warning
  日志，不是报错）。
- `capacity` 必须在 `[1, 2^30]`。
- `EXPANSION` 与 `NONSCALING` **互斥**：先写 `NONSCALING` 再给 `EXPANSION` → 直接报
  `Nonscaling filters cannot expand`。
- `expansion == 0` 等价于 `NONSCALING`。

### 6. 布谷鸟过滤器：能删，代价是结构更复杂

- **指纹** `fp = hash % 255 + 1`，取值 **1..255**（0 被留作空槽标记 `CUCKOO_NULLFP`）。
- **两个候选桶**：`h1 = hash`，`h2 = index ^ (fp * 0x5bd1e995)`；桶下标是
  `(h % numBuckets) * bucketSize`（**取模**，虽然 `numBuckets` 是 2 的幂）。
- `numBuckets = getNextN2(capacity / bucketSize)`，为 0 时兜底成 1；`expansion` 也被 `getNextN2`
  规整。
- 插入顺序：**先找空位 → 踢出重插入（最多 `maxIterations` 次，失败要按原路回滚）→ 扩容 → 重试**。
  `expansion == 0` 时直接返回 `NoSpace`。
- 删除后 `numDeletes > numItems * 0.10` 且子过滤器多于 1 个 → 触发 `Compact`。
  注意比较发生在**两个计数都已更新之后**。
- `CF.ADD` 走的是 `InsertUnique`（先查重），而直接 `Insert` **不查重**，同一元素可以重复写入，
  `CF.COUNT` 也会大于 1。

## 三、运行

```bash
cd 07-数据存储/03-缓存/布隆过滤器与缓存穿透/python
python selfcheck_bloom.py    # 61 条断言
python selfcheck_cuckoo.py   # 56 条断言
python main.py
cd ../go && go run bloom.go cuckoo.go main.go
```

## 四、断言设计

- **位宽逐项手算核对**：`bpe(0.01) ≈ 9.5850584`、`hashes=7`、`bytes=120`、`bits=960`；
  取整分支 `n2=10 / bits=1024 / bytes=128 / entries=106`。
- **字节对齐单独钉一条**：`bits=958 → bytes=120`（`(958//64+1)*8`），确认是 8 的倍数。
- **`ENTS_IS_BITS` 反推容量**：传 10 得到 1024 位与 106 的容量，与取整分支殊途同归。
- **双哈希读写成对**：`add` 首次返回 1、重复返回 0；`popcount` 恰为 `hashes`；
  未加入的 `check` 为 0。
- **链扩容成对**：加满 100 个后仍是 1 条链；第 101 个触发扩容，新链 `error` 减半、`entries`
  翻倍。哈希用「每个元素占 8 个连续位」的确定性构造，**负控（从未加入的元素）保证不误报**。
- **`NONSCALING` vs `growth=0`**：前者溢出返回 `SB_FULL(-2)`，后者返回 `SB_ERR(-1)`。
- **布谷鸟的确定性假阳性**：`numBuckets=8` 时取 `x=0`、`y=2040`——`2040 % 255 == 0` 使指纹
  相同、`2040 % 8 == 0` 使候选桶相同，于是插入 0 之后查 2040 **必然为真**。
- **压缩阈值成对**：`numItems=100` 删 1 个（1 > 9.9 为假）不压缩；`numItems=10` 删 1 个
  （1 > 0.9 为真）触发压缩。

## 五、注意事项与口径

- **哈希函数未移植**：真实实现用 `MurmurHash64A_Bloom(buf, len, 0xc6a4a7935bd1e995)` 计算
  `a`，再用 `a` 当种子算 `b`。本 demo 把 `(a, b)` 作为**注入参数**，聚焦于位数组与扩容语义；
  位定位公式 `(a + i·b) % mod` 与源码一致。
- `CuckooFilter_Compact` 的真实实现本轮**未实读**，demo 里只用一个计数器记录「触发次数」，
  不模拟搬迁逻辑。
- 布隆过滤器的「删除」是做不到的；需要删除语义时必须换布谷鸟过滤器（或计数布隆）。
- 缓存穿透的完整防护不止过滤器：还要处理**缓存空值**（短 TTL）、**key 合法性校验**、
  **限流**。过滤器只解决「随机不存在的 key」这一类。

## 六、参考资料（实际读过）

- <https://raw.githubusercontent.com/RedisBloom/RedisBloom/master/deps/bloom/bloom.h>
- <https://raw.githubusercontent.com/RedisBloom/RedisBloom/master/deps/bloom/bloom.c>
- <https://raw.githubusercontent.com/RedisBloom/RedisBloom/master/src/sb.h>
- <https://raw.githubusercontent.com/RedisBloom/RedisBloom/master/src/sb.c>
- <https://raw.githubusercontent.com/RedisBloom/RedisBloom/master/src/cuckoo.h>
- <https://raw.githubusercontent.com/RedisBloom/RedisBloom/master/src/cuckoo.c>
- <https://raw.githubusercontent.com/RedisBloom/RedisBloom/master/src/config.c>
- <https://raw.githubusercontent.com/RedisBloom/RedisBloom/master/src/config.h>
- <https://raw.githubusercontent.com/RedisBloom/RedisBloom/master/src/rebloom.c>
