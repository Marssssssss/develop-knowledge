# libmalloc 的 Nano V2 分配器

`malloc(16)` 走的是哪条路?在 Apple 平台上,≤256 字节的分配由 **nano 分配器**接管,它是一套"用空间换 CPU 与可扩展性"的方案:不做合并、不做跨块整理、靠 per-CPU 当前块把锁竞争降到最低。nano 现在有两代实现,`NANO_V1`(旧)与 `NANO_V2`(当前默认),由 `_malloc_engaged_nano` 选择。

V2 相对 V1 的关键改动是**地址即自描述**:一个 nano 指针可以被直接拆成"哪个 arena / 哪个块 / 块内第几槽",不需要任何查表或哈希;同时块元数据被抽出来集中放进 arena 的第 0 个块(元数据块),不再占据被分配的槽位。

## 一、尺寸类

```c
#define NANO_MAX_SIZE          256
#define SHIFT_NANO_QUANTUM       4
#define NANO_REGIME_QUANTA_SIZE (1 << 4)   // 16
#define NANO_SIZE_CLASSES       (256/16)   // 16
```

桶就是 `{16, 32, 48, ..., 256}`,尺寸类 = `howmany(size, 16) - 1`,字节数 = `(class + 1) * 16`。`_nano_common_good_size` 先把请求向上取整到 16 的倍数(且至少 16),`nanov2_malloc` 判的是**取整后**的 `rounded_size <= NANO_MAX_SIZE`,而 `nanov2_calloc` 判的是**取整前**的 `total_bytes <= NANO_MAX_SIZE`——两处判据不同,写在同一份文件里。

每个块 16KB,块内槽位数 = `块大小 / 分配大小`(整除)。除不尽的余量就是纯浪费,源码在 `slots_by_size_class[]` 的注释里逐类标了出来:48 字节浪费 16、80 与 96 各浪费 64、144 浪费 112、208 浪费 160……只有 16/32/64/128/256 这几类正好整除。

## 二、地址位域

`nanov2_addr_s` 是一个覆盖整个指针的位域结构(**低位先声明**,与 C 位域从 LSB 起分配一致):

```text
iOS(默认)     : offset:14 | block:12 | arena:3 | signature:35
macOS/模拟器  : offset:14 | block:12 | arena:3 | region:15 | signature:20
```

于是 `block_size = 1 << 14 = 16KB`、`blocks_per_arena = 2^12 = 4096`、`arenas_per_region = 2^3 = 8`,倒推 `arena = 64MB`、`region = 512MB`。iOS 上 `NANOV2_REGION_BITS = 0`,即**只有一个 region**(`NANOV2_MAX_REGION_NUMBER = 0`),省下的位给了签名。

签名本身在 iOS 上是 `(SHARED_REGION_BASE + SHARED_REGION_SIZE) >> 29`(nano 区跟在共享区后面),macOS 上是常量 `0x6 << 44`。判断"这个指针是不是 nano 指针"只需一次右移比较:`ptr >> SHIFT_NANO_SIGNATURE == NANOZONE_SIGNATURE`。

## 三、Arena 与块布局

一个 arena = 4096 个块,其中**块 0 是元数据块**,里面排 4096 个 `nanov2_block_meta_t`(每个正好 4 字节,合计 16KB)。

块号与元数据下标之间不是恒等映射,而是**高低 6 位互换**:

```c
meta_index = ((block_index >> 6) | (block_index << 6)) & 0xFFF;
```

这是一个**对合**(做两次回到原值),把"块号"搅乱成"元数据下标",使得连续块的元数据不连续。

各尺寸类在 arena 里占多少块,由 `block_units_by_size_class[]` 决定(单位是"64 个块",16 个类的数字加起来正好 64)。这张表**不是平均分**:16 字节类占 2 个单元,32 字节类占 10 个,48 字节类占 11 个,240 字节类只占 1 个。由它推导出两张表:

- `first_block_offset_by_size_class[]` / `last_block_offset_by_size_class[]`:第 0 类的起始是 **1** 而不是 0(块 0 让给元数据),所以第 0 类实际只有 127 个块而不是 128;
- `ptr_offset_to_size_class[]`:64 项,把"逻辑块号 >> 6"映射到尺寸类。

因为各类块数不是 2 的幂,尺寸类反查**不能靠掩码**,只能查表——源码注释里明确写了 "This would be a simple mask operation if all size classes were of equal size, but sadly they are not"。

## 四、块元数据

```c
typedef struct {
    uint32_t next_slot  : 11;  // 空闲链表头,1-based
    uint32_t free_count : 10;  // 空闲槽位数 - 1
    uint32_t gen_count  : 10;  // A-B-A 计数
    uint32_t in_use     :  1;
} nanov2_block_meta_t;
```

`next_slot` 除了"下一个空闲槽 + 1"之外,还有一组**特殊值**(都 ≥ 0x7fa,与真实槽位数拉开了距离):

| 值 | 名字 | 含义 |
| --- | --- | --- |
| `0x000` | `SLOT_NULL` | 这个槽从未用过 |
| `0x7fa` | `SLOT_GUARD` | 保护块(置 `PROT_READ`) |
| `0x7fb` | `SLOT_BUMP` | 空闲链表空,请 bump 分配 |
| `0x7fc` | `SLOT_FULL` | 块已满 |
| `0x7fd` | `SLOT_CAN_MADVISE` | 块空且已停用,可 madvise |
| `0x7fe` | `SLOT_MADVISING` | 正在 madvise,别碰 |
| `0x7ff` | `SLOT_MADVISED` | 已 madvise |

`free_count` 的口径是**"空闲槽位数 − 1"**,这是最容易读错的一处。新块初始化为 `slots - 1`;满块时再减 1 会变成 `-1`,被 10 位无符号字段回绕成 `0x3FF`。由此:

- `slot_full = (free_count == 0)` 的真实含义是"**拿完这一格就一个都不剩**",而不是"当前没有空闲槽";
- bump 分配的槽位是 `slots - free_count - 1`,即 `free_count` 直接编码了 bump 前沿的位置;
- 从满块释放第一格时 `free_count` 从 `0x3FF` 加 1 回绕成 `0`,状态自洽。

## 五、分配与释放

分配(`nanov2_allocate_from_block_inline`)只有两条路:

```text
if next_slot in {SLOT_BUMP, SLOT_CAN_MADVISE}:   # 空闲链表空 -> bump
    slot = slots - free_count - 1
    new_next_slot = slot_full ? SLOT_FULL : SLOT_BUMP
else:                                            # 从空闲链表取头
    slot = next_slot - 1                         # next_slot 是 1-based
    new_next_slot = slot_full ? SLOT_FULL : slot.next_slot
free_count -= 1; gen_count += 1
```

释放(`nanov2_free_to_block_inline`)把槽位压回链表头,并写 `double_free_guard = slot_freelist_cookie ^ ptr`;分配路径在 **CAS 成功之后**才校验这个哨兵(注释解释得很清楚:CAS 之前校验的话,另一线程可能已经抢走这个槽并往里写了)。

一个反直觉的分支:只有当 `!was_full && new_free_count == slots - 1` 时才走"放空"分支,把 `next_slot` 置成 `SLOT_BUMP`(块仍 in_use)或 `SLOT_CAN_MADVISE`(已停用,可 madvise)。所以**从满块释放第一格不会走这条路**——`was_full` 把它排除了,结果是 `next_slot` 直接指向那个槽,而不是回到 `SLOT_BUMP`。

## 六、ASLR cookie

arena 里的"逻辑块号"与"真实块号"差一个异或:`real = logical ^ aslr_cookie`。所有涉及块号的地方都要先解扰再比较:`nanov2_next_block_for_size_class` 是"解扰 → 和 `last` 比 → 加一 → 再扰回去";`nanov2_size_class_for_ptr` 是"解扰 → `>> 6` → 查 `ptr_offset_to_size_class`"。元数据块自身的下标则是 `block_index_to_meta_index(aslr_cookie)`。

## 七、并发与 per-CPU 当前块

`nanozonev2_t.current_block[size_class][index]` 是一个 `16 × 64` 的二维数组,每个尺寸类最多 64 个"当前块",默认按 CPU 号取下标:`cpu & (MAX_CURRENT_BLOCKS - 1)`。分配先试当前块,失败才去 arena 里找块(`nanov2_find_block_in_arena`),找不到就开新 arena / 新 region。

## 八、边界与坑(自检里都有对应用例)

- `free_count` 是"空闲数 − 1",满块回绕成 `0x3FF`;把它当"剩余槽位数"读会整体差 1。
- `next_slot` 是 **1-based**,"槽 0"写作 `next_slot == 1`;`SLOT_NULL == 0` 才有意义。
- 满块的第一格释放不触发"放空"分支,块会停在"指向那个槽"的状态而不是 `SLOT_BUMP`。
- 第 0 尺寸类只有 127 个块(块 0 是元数据块),`last - first + 1 ≠ units * 64`。
- 块号↔元数据下标是高低 6 位互换,是对合但不是恒等;块 1 的元数据在下标 64。
- 尺寸类反查必须查表,不能按掩码——各类的块单元数不是 2 的幂。
- `nanov2_malloc` 与 `nanov2_calloc` 的"是否交给 helper zone"判据不同(取整后 vs 取整前)。
- iOS 变体没有 region 位,只有一个 region;`NANOV2_MULTIPLE_REGIONS` 在 iOS 上是 0。

## 九、文件与运行

```text
python/nanov2_const.py       常量、尺寸类、arena 映射表构建
python/nanov2_model.py       地址布局、块状态机、arena 反查
python/selfcheck_nanov2.py   107 条断言(实跑全绿)
python/main.py               一个 16 字节类块的分配/释放演示
go/nanov2.go                 Go 侧同题实现
go/main.go                   演示入口
```

```bash
cd python && python selfcheck_nanov2.py && python main.py
```

Go 侧本机无工具链(`which go` 为空),走人工审查 + `bracket_check` / `go_sanity` / `go_crossref` / `syntax_sanity` 四项静态检查,均通过。

## 十、参考资料(实际读过)

- `apple-oss-distributions/libmalloc@main` · `src/nano_zone_common.h`(2229 B,`NANO_MAX_SIZE` / 量子 / iOS 与非 iOS 签名位宽)— https://github.com/apple-oss-distributions/libmalloc/blob/main/src/nano_zone_common.h
- `apple-oss-distributions/libmalloc@main` · `src/nanov2_zone.h`(10983 B,地址位域、`nanov2_block_meta_t`、`next_slot` 特殊值、`MAX_CURRENT_BLOCKS`)— https://github.com/apple-oss-distributions/libmalloc/blob/main/src/nanov2_zone.h
- `apple-oss-distributions/libmalloc@main` · `src/nanov2_malloc.c`(129396 B,`block_units_by_size_class`、`slots_by_size_class`、两张表的构建、`nanov2_allocate_from_block_inline`、`nanov2_free_to_block_inline`)— https://github.com/apple-oss-distributions/libmalloc/blob/main/src/nanov2_malloc.c
- `apple-oss-distributions/libmalloc@main` · `src/nano_malloc_common.h`(3019 B,`nano_version_t` 的 `NANO_NONE` / `NANO_V2`)— https://github.com/apple-oss-distributions/libmalloc/blob/main/src/nano_malloc_common.h

> 口径说明:本 demo 只建模**单块内的分配状态机、地址编解码与 arena 映射表**。真实的 CAS 重试、块扫描策略(`nanov2_scan_policy`)、madvise 时机策略、region 链表与枚举器都不在建模范围内;`aslr_cookie` 与 `slot_freelist_cookie` 只当作给定的混淆值使用,不模拟其生成。
