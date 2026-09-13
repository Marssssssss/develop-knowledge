# Map 底层实现 — hmap + bmap + 渐进式扩容

> Go 1.23 及之前的 map 由 `hmap`(总控) + `bmap`(桶,每桶 8 对 KV)构成。哈希值低 B 位找桶,高 8 位(tophash)快速过滤;装载因子超过 6.5 或溢出桶过多触发**渐进式扩容**。Go 1.24+ 切换 Swiss Table,但核心行为兼容。

## 简介

**Go map = 哈希表 + 拉链法(以桶为节点)**——把"每个元素独立链节点"优化为"每 8 个元素一个桶",降低指针开销,通过 `tophash` 减少昂贵 key 比较。**渐进式扩容**(incremental evacuation)把搬迁成本分摊到每次写操作,**避免一次插入卡顿数毫秒**。

**关键概念**:
- **`hmap`**:map 的运行时表示,含 count/B/seed/buckets/oldbuckets/nevacuate 等字段
- **`bmap`**:桶,编译期展开为 `tophash[8] + keys[8] + values[8] + overflow`
- **tophash**:key 哈希值的**高 8 位**,用来快速过滤不匹配槽;**< minTopHash 标记桶状态**
- **装载因子 6.5**:`count > 6.5 * 2^B` 触发翻倍扩容
- **渐进式搬迁**:扩容期间保留 `oldbuckets` 与 `buckets`,每次写操作搬迁 1-2 个旧桶,直到追上
- **Go 1.24+ Swiss Table**:把 bmap 替换为目录化 Swiss Table,open addressing + SIMD probe

**历史**:Go 1.0 map 由 Robert Griesemer 实现;**Go 1.24 (2025) 改用 Swiss Table**(借鉴 C++ abseil)。

## 原理详解

### 1. hmap 结构(Go 1.23 及之前)

```go
type hmap struct {
    count     int           // 元素个数,len() 直接读
    flags     uint8         // iterator / hashWriting / sameSizeGrow
    B         uint8         // log_2 of bucket count, len(buckets) = 2^B
    noverflow uint16        // 溢出桶数量
    hash0     uint32        // 哈希种子(makemap 时 fastrand 生成)
    buckets   unsafe.Pointer
    oldbuckets unsafe.Pointer // 扩容期间的旧桶数组
    nevacuate  uintptr        // 已搬迁进度
    extra      *mapextra      // 溢出桶管理
}
```

### 2. bmap 结构(编译期展开)

```
┌─────────────────────────────────────┐
│  tophash[0] │ ... │ tophash[7]     │  ← key 哈希值高 8 位 / 桶状态
├─────────────────────────────────────┤
│  keys[0]    │ ... │ keys[7]        │  ← 8 个 key(连续, 内存对齐优化)
├─────────────────────────────────────┤
│  values[0]  │ ... │ values[7]      │  ← 8 个 value
├─────────────────────────────────────┤
│  overflow → *bmap                   │  ← 溢出桶指针
└─────────────────────────────────────┘
```

源码注释解释 key/value 连续存储的理由:让代码复杂一点点,但消除 padding,例如 `map[int64]int8` 的对齐开销。

### 3. 查找流程(mapaccess)

```
hash = alg.hash(key, hash0)
bucket_idx = hash & (2^B - 1)   // 低 B 位找桶
tophash_target = hash >> (64 - 8) // 高 8 位
遍历 bucket 的 8 个槽: tophash 不同跳过, tophash 相同比 key, key 相等返回值; 否则遍历 overflow 链
```

**关键优化**:先比 tophash(1 byte),命中后再比完整 key;**1 字节比较比 key 比较快 5-10x**。

### 4. 装载因子与扩容

```go
// overLoadFactor: count > bucketCnt && count > 6.5 * 2^B
// 触发翻倍扩容:  B → B+1
```

**两种扩容触发**:
- **翻倍扩容**:装载因子 > 6.5 → `B = B + 1`,旧桶搬到两个新桶
- **等量扩容**:`noverflow > 1<<(min(B,15))` → `B` 不变,只整理稀疏数据(常见于"写-删-写"循环)

### 5. 渐进式搬迁

```
hashGrow:  oldbuckets ← buckets;  buckets ← newBuckets;  nevacuate ← 0
   │ (后续每次写操作)
evacuate(oldBucket):
   1. 遍历 oldBucket[i] 的 KV
   2. 翻倍扩容: 每 KV 按 hash & newbit 分到 x 桶或 y 桶
   3. 等量扩容: 1:1 搬到新桶
```

**读取时的双桶查找**:先查 oldbuckets,找不到再查 buckets。

### 6. mapextra 与溢出桶

```go
type mapextra struct {
    overflow    *[]*bmap  // 当前桶的溢出桶列表
    oldoverflow *[]*bmap  // 扩容期间旧溢出桶列表
    nextOverflow *bmap    // 预分配的下一个可用溢出桶
}
```

`B >= 4` 时 `makemap` 预分配 `2^(B-4)` 个溢出桶,**避免运行时频繁 malloc**。overflow 用 uintptr 而非 `*bmap` 是为了让 bmap 不含指针 → 整段内存可批量 GC 扫描。

### 7. tophash 桶状态枚举

```go
const (
    emptyRest = 0
    emptyOne  = 1
    evacuatedX = 2    // 旧桶搬迁到新桶前半段
    evacuatedY = 3
    minTopHash = 5    // tophash < 5 是状态, >= 5 是真实哈希
)
```

**为什么这么设计**:让 tophash 单值既能存哈希高 8 位(>= minTopHash),又能存桶状态(< minTopHash)——一字节双用。

### 8. 不可并发读写

`fatal error: concurrent map read and map write`——并发读写未定义行为;`-race` 检测器能捕获。**生产环境必须 `sync.Mutex` 包裹**或用 `sync.Map`。

## 对比 / 选型

| 维度 | Go map(经典) | Swiss Table(1.24+) | Python dict | Java HashMap |
| --- | --- | --- | --- | --- |
| 冲突解决 | 拉链法(8 元素/桶) | open addressing(SIMD) | open addressing | 拉链 + 红黑树退化 |
| 扩容策略 | 渐进式翻倍 | 渐进式 + 目录化 | 增量翻倍 | 一次性翻倍 |
| 装载因子 | 6.5/8 = 81% | ~75% | ~66% | 0.75 |

## 环境准备

- 操作系统:任何支持 Go 的平台
- Go 版本:Go 1.21+(本 demo 行为在 1.23 经典 / 1.24+ Swiss Table 都成立)
- 依赖:无第三方依赖

## 运行方式

```bash
cd 09-语言学习/Golang/map底层实现/go
go run map_mechanism.go
```

## 关键代码片段

```go
// 1) 迭代顺序随机化演示
for k := range m { fmt.Println(k) } // 每次顺序不同
// 2) 装载因子 6.5 演示: insert 触发扩容
m := make(map[int]int)
for i := 0; i < 100; i++ {
    m[i] = i
    // 用 reflect / runtime 探测 B 变化 (略)
// 3) 并发读写触发 fatal error
// go func() { m[1] = 2 }()
// go func() { _ = m[1] }()
// fatal error: concurrent map read and map write
// 4) sync.RWMutex 包裹 map
var mu sync.RWMutex
mu.Lock(); m[1] = 2; mu.Unlock()
mu.RLock(); _ = m[1]; mu.RUnlock()
// 5) sync.Map: 读多写少场景
var sm sync.Map
sm.Store("k", 1)
v, ok := sm.Load("k")
// 6) nil map 可读不可写
var nilMap map[string]int
v, ok = nilMap["k"] // ok=false, v=0, 不 panic
// nilMap["k"] = 1   // panic: assignment to entry in nil map
```

## 性能与边界

- **查找**:O(1) 摊销;冲突少时 ~30 ns;大量冲突退化为 O(n)
- **装载因子 6.5** 选定原因:泊松分布下 ~3.87% 访问需遍历溢出桶
- **渐进式扩容**:**单次写操作只搬迁 1-2 个旧桶**,最坏情况 n 次写操作保证 n 大小的 map 完整搬迁
- **桶大小 8**:CPU 缓存行友好(int64 key 8B × 8 = 64B)
- **迭代随机化**:每次 range 起点 + 偏移随机,防止开发者依赖遍历顺序
- **删除 vs 清空**:`delete(m, k)` 标记空槽(不缩容);`m = nil` 让 GC 回收整个 map

## 注意事项与常见坑

1. **并发读写未定义行为**:必须用 `sync.Mutex` / `sync.RWMutex` / `sync.Map`
2. **迭代顺序随机**:`for k, v := range m` 每次顺序不同;需要有序遍历改用 slice 排序
3. **map 是引用类型**:`m2 := m` 后 `m2[k] = v` 也改 `m`
4. **delete 不缩容**:`m = nil` 才能让 GC 回收
5. **nil map 与空 map**:`var m map[K]V` 可读不可写;`m := map[K]V{}` 可读可写
6. **元素不可寻址**:`&m["k"]` 编译错误;需 `v := m["k"]; v.X = ...; m["k"] = v`

## 参考资料

- [runtime/map.go 源码(Go 1.23) — hmap / mapassign / mapaccess 等](https://go.dev/src/runtime/map.go)
- [Go Under The Hood — 5.4 Swiss Table and the Go 1.24 Implementation (golang.design)](https://www.golang.design/under-the-hood/en/part2lang/ch05data/swisstable)
- [runtime/map_noswiss.go 等早期源码分析(go.cyub.vip)](https://go.cyub.vip/type/map/)
- [Go FAQ — Why are map operations not atomic?](https://go.dev/doc/faq#atomic_maps)

---

> 文件清单:`map_mechanism.go`(一文件涵盖 hmap/bmap/迭代随机化/装载因子扩容/渐进式搬迁演示/并发读写 fatal/sync.Mutex/sync.Map/nil map/元素不可寻址 10 个示例),`go.mod`,`README.md`。