# Bump (Arena) Allocator — 最小 bump 指针分配器

## 简介

Bump allocator(又名 **arena / linear / region allocator**)是最简单的内存分配策略:
预分配一大段连续内存,用一个 `offset` 指针(或偏移)标识"下一个可分配位置",
每次分配只需把 `offset` 推进(对齐 + size)即可返回指针。

- 适用场景:解析器一帧内分配大量小对象、编译器 IR 节点、测试用例的临时数据、游戏引擎一帧内存池。
- 不适用:长时间运行的服务(因为单次累积的 offset 永不回收,会撑爆整段 buffer)。
- 关键概念:**bump pointer**(单调递增的分配游标)、**arena**(整段缓冲)、**aligned bump**(`(x + a - 1) & ~(a - 1)` 二进制向上取整)。
- 历史:非常古老(和 `brk`/`sbrk` 等价),LLVM 称为 `BumpPtrAllocator`、Rust 早期 arena、Go 早期 `goarena` 实验都用了类似模式。

## 原理详解

### 工作机制(单块版,以 C `mmap` 为例)

1. **创建**:`arena_create(capacity)` 一次性 `mmap` 一段 `sizeof(Arena) + capacity` 字节;
   头部放控制结构,数据段放实际可用 buffer。
2. **分配**:`arena_alloc(a, size, alignment)`
   - 算当前 offset 对应的"绝对地址",对其向上对齐到 alignment(必须是 2 的幂)
   - 加上 padding + size 检查是否超出 capacity
   - 推进 offset;返回对齐后的指针
3. **重置**:`arena_reset(a)` 把 `offset = 0`(整段缓冲可复用,**不能 free 单对象**)
4. **销毁**:`arena_destroy(a)` 调用 `munmap`,把内存还给 OS

### 数据结构(单块版)

```text
  +---------------------------------------------+------+
  | sizeof(Arena) 控制头                          | data |  mmap 区域
  +---------------------------------------------+------+
  ^ base (data 起点)                             ^ base + capacity
              ^
              offset = 0 (初始)

  alloc(16, 8) 之后:
  +--------+--------------------------------------+
  | header | xxxxxxxx (16 字节,8 字节对齐)         |
  +--------+--------------------------------------+
                    ^ offset = 16

  alloc(32, 8) 之后:
  +--------+--------------------------------+------+
  | header | xxxxxxxx | xxxxxxxxxxxxxxxxxxxxxxxx    |
  +--------+--------------------------------+------+
                                  ^ offset = 48
```

### 对齐的二进制数学

```text
    +-+-+-+-+-+-+-+-+-+-+-+
    |1|0|0|0|0|1|0|1|0|1|1| = offset          = 1076
    +-+-+-+-+-+-+-+-+-+-+-+
AND |1|1|1|1|1|1|1|1|0|0|0| = ~(alignment - 1)= ~7
    +-+-+-+-+-+-+-+-+-+-+-+
    |1|0|0|0|0|1|0|1|0|0|0| = aligned address = 1064
    +-+-+-+-+-+-+-+-+-+-+-+
```

公式 `aligned = (x + a - 1) & ~(a - 1)` 把 x 向上取整到 a 的倍数:
- `+ a - 1` 把 x 推到下一对齐行(若已经在行上则下推到行末,随后 & ~ 把它拉回)
- `& ~(a-1)` 清掉低 `log2(a)` 位

这个 trick 成立的前提是 **`a` 必须是 2 的幂**,否则按位与结果会出错(例如 a=6 时 `~5`=`...11111010`,不是 8 的倍数屏蔽)。生产代码必须校验 `alignment & (alignment - 1) == 0`。

### 底层发生了什么

| 角度 | bump allocator | malloc (glibc ptmalloc2) |
| --- | --- | --- |
| 分配 | 单次 `&` + 单次指针 `+` | 走 bin / unsorted bin / 大块走 mmap |
| free | 只支持整段重置(`offset=0`) | 单对象 free → 回 bin,可能合并相邻 free chunk |
| 时间复杂度 | **O(1)** (3 条指令) | 平均 O(1),worst O(n) |
| 内部碎片 | 仅来自对齐 padding(对象尺寸固定时几乎为 0) | 取决于 free list 状态 |
| 适用 | 短生命周期、高批量 | 通用 |
| 操作系统视角 | 整段 mmap → munmap,无需回收 | 复杂,需要 brk/mmap/thread cache |

Go 版本用 `make([]byte, capacity)` 直接得到堆 slice,无须 mmap;但逻辑上等价。
Python 版本用 `bytearray` + `memoryview`,`offset` 是相对 buffer 起点的偏移。

## 对比 / 选型

| 维度 | bump | free list (malloc) | pool (slab) | GC (mark-sweep) |
| --- | --- | --- | --- | --- |
| 分配速度 | **最快** | 中(查 bin) | 中-快 | 慢 |
| 单对象 free | ❌ | ✅ | ✅(cache) | ✅(自动) |
| 适合生命周期 | 全局/帧级 | 长短期混合 | 固定尺寸高频 | 长生命周期 |
| 内部碎片 | 低(对齐损失) | 中 | 极低 | 取决于 compact |
| 实现复杂度 | **极低** | 高 | 中 | 很高 |
| 典型用户 | 解析器 / 帧分配器 | glibc malloc / jemalloc | Linux kmem_cache | JVM / Go runtime |

## 环境准备

- 操作系统:Linux / macOS / WSL(Windows 需 WSL 或 MSYS2 使用 mmap)
- C:任意 `gcc` / `clang`(无需特殊头文件)
- Python:3.10+(用到 `memoryview`、类型下界与 `^`-取整)
- Go:1.21+(用了 `unsafe.Alignof`、`reflect.Type.Align()` 等)— 1.20 也可(把 `AlinedSize` 改为自行 `unsafe.Sizeof/Alighof`)

## 运行方式

```bash
# C
gcc -O2 -Wall -Wextra bump_allocator.c -o bump_demo
./bump_demo

# Python
python3 bump_allocator.py

# Go
go run bump_allocator.go
```

## 关键代码片段

### C:对齐函数(2 的幂校验 + 向上取整)

```c
static inline uintptr_t align_up(uintptr_t x, size_t alignment)
{
    return (x + alignment - 1) & ~(alignment - 1);
}

/* arena_alloc:对齐 + 边界检查 + 推进 offset */
void *arena_alloc(Arena *a, size_t size, size_t alignment)
{
    /* alignment 必须为 2 的幂 — 否则 ~(alignment-1) 不是位掩码      */
    if ((alignment & (alignment - 1)) != 0) return NULL;

    uintptr_t current = (uintptr_t)(a->base + a->offset);
    uintptr_t aligned = align_up(current, alignment);
    size_t    pad     = aligned - current;

    if (a->offset + pad + size > a->capacity) return NULL;  /* OOM  */
    a->offset += pad + size;
    return (void *)aligned;
}
```

### Python:把对齐 / 边界 / 返回切片整合

```python
def alloc(self, size: int, alignment: int = 8) -> memoryview:
    current = self.offset
    aligned = self._align_up(current, alignment)
    pad     = aligned - current
    if self.offset + pad + size > self.capacity:
        raise MemoryError(...)
    self.offset += pad + size
    return memoryview(self._buf)[aligned: aligned + size]
```

### Go:用 `unsafe.Pointer` + `uintptr` 做"指针到整数"位运算

```go
func (a *Arena) Alloc(size, alignment int) ([]byte, error) {
    current := uintptr(unsafe.Pointer(&a.buf[a.offset]))   // "指针转整数"
    aligned := alignUp(current, uintptr(alignment))
    pad := int(aligned - current)
    if a.offset+pad+size > a.capacity {
        return nil, fmt.Errorf("OOM")
    }
    a.offset += pad + size
    return a.buf[aligned : aligned+uintptr(size)], nil
}
```

## 性能与边界

- **分配复杂度**:`O(1)`(常数级:1 个对齐运算 + 1 个比较 + 1 次加法)
- **重置**:`O(1)`(`offset = 0`)
- **释放**:**只能整段 reset**,单个对象"释放"必须等到下一次 reset
- **容量上限**:依 mmap 与平台指针空间;64 KiB 单块在 demo 里够用
- **多线程**:本 demo 是**单线程**;多线程共享同一 arena 必须外部加锁,否则 offset 竞争;典型做法是**每线程一个 arena**(Go 的 `sync.Pool` 就利用了这一点)
- **碎片**:对齐 padding 引入"内部碎片";对象大小变化大时浪费会显著

## 注意事项与常见坑

1. **alignment 必须是 2 的幂**:`(x + a - 1) & ~(a-1)` 这个位运算只在 2 的幂下成立。
   生产函数应拒绝非 2 幂的 alignment,否则地址会错。
2. **单块满必须显式处理**:本 demo 是单块,超容量返回 `NULL` / `MemoryError`;多块(linked arena)需要 `next` 指针链表,记得结构体必须**命名**(`typedef struct Arena { struct Arena *next; ... } Arena;`),匿名 struct 不能自引用。
3. **绝对地址 vs 相对 offset**:offset 是相对 base 的偏移;对齐计算前必须转为绝对地址(`(uintptr_t)(base + offset)`)、对齐后转回相对偏移再写回 `offset`,C 与 Go 都必须如此。
4. **不能 free 单对象**:用 bump allocator 写入的指针,在 reset 之后**地址依然有效但语义被回收**;若指针逃出 arena 生命周期,会出现"野引用"问题。
5. **不能与其他分配器混用**:sbrk-based bump allocator 会"借用"brk 区域,如果程序里同时用 malloc,reset 会破坏 malloc 的堆;最稳妥是先用 `malloc` 拿一大块、再在 arena 内分配。
6. **大对象 + 小对齐**会浪费 padding(例如对一个 1 字节对象要求 4096 字节对齐,浪费 4095 字节);生产里把对象按大小粗粒度分类用不同对齐。
7. **分配失败的处理不可忽略**:返回 NULL / panic / MemoryError 都是契约,调用方必须检查;`CHECK_ALLOC` 宏(`if (!ptr) { cleanup; return -1; }`)是常见做法。
8. **OS mmap 粒度**:`MAP_ANONYMOUS` 在 Linux 上总是按页(4 KiB)粒度分配,内部分配比页小不会"真浪费";但建议 capacity 仍是 2 的幂,方便算 padding。

## 参考资料(实际阅读过的权威来源)

- [Arena allocation in C — Leo Mavr](https://leonmavr.github.io/2026/03/22/Arena_allocation.html) — 完整 multi-block 实现 + 对齐二进制数学 + ASCII 偏移示意图 + Capacity 扩展时的 `SIZE_MAX/2` 溢出保护(本次 demo 主要参考)。
- [Arena Allocators — William Fedele](https://williamfedele.com/blog/arena-allocators) — 用 `mmap` 的简化版本,包含 `arena_init` / `arena_free` / `arena_destroy` 三段生命周期演示。
- [C - Fast & simple bump allocator — Code Review StackExchange](https://codereview.stackexchange.com/questions/243228/c-fast-simple-bump-allocator/243236) — 一个用户的实现与社区评审:alignment 必须为 2 的幂、`stderr` 而非 `stdout` 输出错误、`KB`/`MB` 宏应 `((size_t) size * 1024)` 防缩小。
- [Is this implementation of malloc a bump allocator? — StackOverflow](https://stackoverflow.com/questions/62076569/is-this-implementation-of-malloc-a-bump-allocator) — 高赞回答澄清 bump allocator 只能整段 free、不能单独 free 单对象、`sbrk` 方法会与 libc malloc 冲突。
- [llvm BumpPtrAllocator — llvm-project bolt/runtime/instr.cpp](https://llvm.org/reports/scan-build/report-instr.cpp-computeEdgeFrequencies-131-68056c.html) — LLVM 的 `BumpPtrAllocator` 用 `mmap` + `EntryMetadata` magic/AllocSize 调试 + 16 字节对齐,生产级示例。
