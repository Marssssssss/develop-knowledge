# Go map：可扩展散列目录 + Swiss table（1024 之后是「拆」不是「翻倍」）

> Go 1.24 把 `map` 整个换掉了：实现从 `runtime/map.go`（至今还在，但只剩一个薄壳 + 两个
> `loadFactorNum/Den = 7/8` 常量）迁到了 **`internal/runtime/maps/`**。
> 新实现的两条主线是：**一张表最多 1024 槽**（超了拆成两张并按需把目录翻倍，这就是增量扩容），
> 以及**每组 8 槽 + 控制字节 + 三角探测**（swiss table）。
> 本 demo 把这两条主线**逐函数复刻**，重点回答"为什么大 map 的扩容不再是线性翻倍的尖峰"。

代码：`python/go_swissmap.py`（模型）、`python/selfcheck_go_swissmap.py`（1003 条断言，实跑全绿）、
`python/main.py`、`go/`（3 个文件，四项静态检查通过）。

## 一、常量与哈希切分

```go
const maxTableCapacity = 1024          // table.go，注释写着 "Completely made up value"
const maxAvgGroupLoad  = 7             // group.go，7/8，注释说明与 Abseil 一致
const ctrlEmpty   ctrl = 0b10000000    // 0x80
const ctrlDeleted ctrl = 0b11111110    // 0xFE
func h1(h uintptr) uintptr { return h >> 7 }   // 高 57 位，选组
func h2(h uintptr) uintptr { return h & 0x7f } // 低 7 位，当控制字节
```

注意 Go 的哨兵是 **`0x80`/`0xFE`**（与 Abseil 相同），而 Rust hashbrown 现在是
**`EMPTY = 0xFF` / `DELETED = 0x80`**——正好反过来。跨语言移植控制字节时这是第一个坑
（详见本目录的 663）。

`h1` 取**高** 57 位、`h2` 取**低** 7 位：低 7 位已经被拿去当控制字节了，
如果 `h1` 也用低位，组下标与 tag 就会强相关。

## 二、单表容量与 `growthLeft`

```go
func (t *table) maxGrowthLeft() uint16 {
    if t.capacity <= abi.MapGroupSlots { return t.capacity - 1 }   // 单组表：留一个空槽终止探测
    return (t.capacity * maxAvgGroupLoad) / abi.MapGroupSlots      // 大表：7/8
}
```

| capacity | growthLeft | 比例 |
| --- | --- | --- |
| 8 | 7 | 0.875（特例：留空槽） |
| 16 | 14 | 0.875 |
| 1024 | 896 | 0.875 |

`newTable` 的取整：下限 `MapGroupSlots`（8），上限 `maxTableCapacity`（1024），再向上取 2 的幂
（**组数必须是 2 的幂**，否则三角探测不能遍历所有组）。

## 三、`NewMap(hint)` 的目录推导

```go
if hint <= abi.MapGroupSlots { return m }              // 单组小图，连分配都省了
targetCapacity := (hint * abi.MapGroupSlots) / maxAvgGroupLoad   // hint * 8/7
dirSize := ceil(targetCapacity / maxTableCapacity)，再向上取 2 的幂
m.globalDepth = TrailingZeros64(dirSize)
m.globalShift = 64 - globalDepth
```

实测：

```text
hint=8       单组小图（dirLen=0，8 槽全可用）
hint=9       target=10      dirLen=1    globalDepth=0  globalShift=64  每表 16 槽
hint=100     target=114     dirLen=1    globalDepth=0  globalShift=64  每表 128 槽
hint=1000    target=1142    dirLen=2    globalDepth=1  globalShift=63  每表 1024 槽
hint=10000   target=11428   dirLen=16   globalDepth=4  globalShift=60  每表 1024 槽
hint=100000  target=114285  dirLen=128  globalDepth=7  globalShift=57  每表 1024 槽
```

`directoryIndex(hash)` 在 `dirLen == 1` 时直接返回 0（省一次移位），否则 `hash >> globalShift`
——**取的是哈希的最高几位**，所以相邻 key 会落在不同表里（这正好是可扩展散列的定义）。

## 四、三角探测：一次扫一组

```go
func (s probeSeq) next() probeSeq {
    s.index++
    s.offset = (s.offset + s.index) & s.mask
    return s
}
```

步长 1、2、3、4…，`p(i) = h1 + i(i+1)/2 (mod 组数)`。源码注释给了证明链接：
三角数在 `Z/(2^m)` 上是双射，所以**每个组恰好被访问一次**。
从 `h1 = 0` 起、8 组的序列是 `[0, 1, 3, 6, 2, 7, 5, 4]`（demo 对 2/4/8/16/64 组都断言了它是个排列）。

## 五、1024 之后：split 而不是 grow

```go
func (t *table) rehash(typ *abi.MapType, m *Map) {
    newCapacity := 2 * t.capacity
    if newCapacity <= maxTableCapacity { t.grow(typ, m, newCapacity); return }
    t.split(typ, m)
}
```

`split` 把 `localDepth + 1`，新建两张 `maxTableCapacity` 槽的表，按 `localDepthMask(localDepth)`
（`64` 位哈希时是 `1 << (64 - localDepth)`）那一位把元素二分，然后 `installTableSplit`。

`installTableSplit` 有个前提：**如果 `old.localDepth == m.globalDepth`，说明目录不够深，
必须先把目录整体翻倍**（每个目录项复制成两个、`globalDepth++`）再放左右两张表。

demo 实测的完整生命周期（`hint = 0`，用黄金比例常数打散的确定性哈希）：

```text
第    9 个 capacity=8    -> 16    dirLen=1  globalDepth=0   （growToTable 分配 2*8 槽）
第   16 个 capacity=32            dirLen=1  globalDepth=0
第  450 个 capacity=1024          dirLen=1  globalDepth=0
第  898 个 capacity=2048          dirLen=2  globalDepth=1   ← 第一次 split + 目录翻倍
第 1793 个 capacity=3072          dirLen=4  globalDepth=2   表数=3  ← 只拆了一张
第 1796 个 capacity=4096          dirLen=4  globalDepth=2   表数=4
```

关键在 **1793 那一行**：目录从 2 涨到 4，但**表数只从 2 涨到 3**——这一次插入只搬了一张 1024 槽的表。
这就是"增量扩容"的实际含义：**任何一次插入的最坏代价被 1024 槽封顶，与 map 总大小无关**。
对比之下，Java HashMap / CPython dict 在 1000 万元素时的那次 rehash 要搬 1000 万个槽。

## 六、墓碑：只有「整组都满」才产生

```go
// Delete:
// Only a full group can appear in the middle of a probe sequence
// (a group with at least one empty slot terminates probing).
var tombstone bool
if g.ctrls().matchEmpty() != 0 { g.ctrls().set(i, ctrlEmpty); t.growthLeft++ }
else                           { g.ctrls().set(i, ctrlDeleted); tombstone = true }
```

两个直接推论（demo 都断言了）：

- **单组表（`capacity == 8`）永远不会产生墓碑**——它最多装 7 个，组里恒有空槽；
- 墓碑**不归还 `growthLeft`**，但 `PutSlot` 复用墓碑时会先 `growthLeft++` 再 `--`，
  所以"删一个再插一个"在配额上是免费的。

`pruneTombstones` 的门槛是 `t.tombstones()*10 < t.capacity → 直接放弃`：
源码注释说得很直白，清 1 个墓碑要花 O(n)，下一次插入又要花 O(n) 再清 1 个，不如直接 grow。

## 七、选型结论

| 场景 | 该知道的 |
| --- | --- |
| 预知元素量 | `make(map[K]V, hint)` 的 hint 会先乘 8/7 再定目录，`hint ≤ 8` 则完全不分配，第一次写才建 8 槽单组 |
| 超大 map（百万级） | 1024 槽之后进入"拆表"模式，单次插入的搬移量被 1024 封顶；但目录本身是 `2^globalDepth` 个指针，会持续占内存 |
| 频繁增删 | 墓碑不会自动回收，直到一次 grow/split；`growthLeft` 只增不减（除非命中 prune 的 10% 门槛） |
| 迭代 | Go 的迭代器要处理"遍历途中表被 split/替换"的情况（`it.globalDepth != m.globalDepth` 分支），这比 Java/Python 的 fail-fast 复杂得多 |

## 参考资料（实际读过）

- `https://raw.githubusercontent.com/golang/go/master/src/runtime/map.go`
  （9.7 KB，迁走后只剩薄壳；`loadFactorNum = 7 / loadFactorDen = 8` 两个常量在此）
- `https://raw.githubusercontent.com/golang/go/master/src/internal/runtime/maps/table.go`
  （41 KB；`maxTableCapacity`、`table` 四个计数器、`maxGrowthLeft`、`PutSlot`、`Delete`、
  `pruneTombstones`、`rehash`、`split`、`grow`、`localDepthMask`、三角数 `probeSeq` 及其证明链接）
- `https://raw.githubusercontent.com/golang/go/master/src/internal/runtime/maps/map.go`
  （27 KB；`directoryIndex`、`NewMap(hint)` 的容量推导、`installTableSplit`、`replaceTable`、
  `putSlotSmall`、`growToSmall`、`growToTable`）
- `https://raw.githubusercontent.com/golang/go/master/src/internal/runtime/maps/group.go`
  （`maxAvgGroupLoad = 7`、`ctrlEmpty`/`ctrlDeleted`、`bitsetLSB/MSB/L7B` 掩码）
- `https://raw.githubusercontent.com/golang/go/master/src/runtime/slice.go`（`nextslicecap`，见 664）
