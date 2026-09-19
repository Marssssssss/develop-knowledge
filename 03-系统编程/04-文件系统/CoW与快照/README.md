# CoW 文件系统：为什么快照免费、碎片整理却要命

## 简介

日志文件系统（ext4/XFS）的思路是**原地改写 + 用日志保证元数据不崩**；
写时复制文件系统（Btrfs/ZFS）换个思路：**永远不覆盖已写过的块**，改一块就分配新块、
把指针挪过去。这个差别带来两个直接后果：

- **快照与 `cp --reflink` 几乎免费**：只复制元数据，物理块的引用计数 +1。
- **随机写变成"读旧块 + 写新块"**，而且写得越碎，后续读越慢 —— 于是有了
  `autodefrag`，而 defrag 又会**打断 reflink**，把省下的空间一次性还回去。

本 demo 用 **Python（48 项断言，可实跑）+ Go** 把"物理块引用计数"这一层做成可执行模型，
并把 `FIEMAP_EXTENT_SHARED` 与 btrfs send stream 的 CRC32C 一起串起来。

## 原理详解

### 1. extent 与引用计数

Btrfs 的空间单位是 extent（一段连续的物理块）。一个文件持有若干
`(logical, physical, length)`。两个文件指向同一个 `physical` 时，该 extent 的引用计数 > 1，
`FIEMAP` 就会给它打上 `FIEMAP_EXTENT_SHARED`（`0x2000`，fiemap.h 原文：
*Space shared with other files*）。

### 2. 往中间写会把 extent 切成三段

这是最反直觉的一点：改 1 个块**不是**把整个 extent 复制一遍，而是切成
`头（仍指向原块）/ 中（新分配）/ 尾（仍指向原块）`。

```
原 extent:   [0 1 2 3 4 5 6 7 8 9]  phys=100  refs=2
改第 3 块后: [0 1 2](phys=100) [3](phys=NEW, refs=1) [4..9](phys=104)
```

所以 CoW 的代价**正比于被改的块数，与文件总大小无关** —— 这正是"快照便宜、
数据库随机写昂贵"的同一枚硬币。

### 3. defrag 会打断 reflink

btrfs-man5 原文：defrag 会 *break up the reflinks of COW data*（`cp --reflink` 复制的、
快照里的、去重过的数据），并警告 *may cause considerable increase of space usage*。
demo 里三个 8 块的文件本来共享同一份物理块（8 块），defrag 其中一个之后物理块变成 16 块。

### 4. 挂载选项的三角互斥

`datacow` / `datasum` / `compress` 三者互相牵制（btrfs-man5）：

| 规则 | 手册原文要点 |
| --- | --- |
| `nodatacow` 隐含 `nodatasum`，并禁用压缩 | *Nodatacow implies nodatasum, and disables compression* |
| `nodatacow` 或 `nodatasum` 任一启用 | 压缩被禁用 |
| 压缩被启用 | `nodatacow` 与 `nodatasum` 被禁用 |
| `datasum` | 隐含 `datacow`（*Datasum implies datacow*） |

而且挂载选项**按顺序处理、只有最后出现的那个生效**，所以
`compress,nodatacow` 与 `nodatacow,compress` 的最终状态**完全不同**（demo 有断言）。

手册还提醒：挂载选项作用于整个文件系统，**只有第一个被挂载的 subvolume 的选项生效**，
不能按 subvolume 单独设 `nodatacow` / `nodatasum` / `compress`。

### 5. swapfile 的两条硬约束

swapfile 必须 **NODATACOW**，且必须**预分配、不能有洞**（btrfs-man5 SWAPFILE SUPPORT）。
demo 用 `swapfile_ok()` 把它做成布尔判定。

### 6. send / receive 与它的 CRC32C

`btrfs send` 遍历一个**只读** subvolume，full 模式产出完整表示；给了参考 subvolume 就产出增量。
流是一串编码命令（改元数据、创建/克隆/截断 extent、重命名/删除），**每条命令带 CRC32C 校验和，
初值为 0 且不取反** —— 这与常见 CRC-32C 形态（初值 `0xFFFFFFFF`、结果取反）不同，
照抄网上的 CRC32C 实现会全流校验失败。

`receive` 重建出来的是**等价而非 1:1**：inode 号、subvolume UUID 都可能不同。

## 对比

| | 日志 fs（ext4/XFS） | CoW fs（Btrfs） |
| --- | --- | --- |
| 改写一块 | 原地覆盖，先写日志 | 分配新块、改指针 |
| 快照 | 靠 LVM/外部机制 | 内建，`btrfs subvolume snapshot` |
| 大文件复制 | 读一遍写一遍 | `cp --reflink=always` 零拷贝 |
| 随机小写的代价 | 低（原地） | 高（读改写 + 碎片） |
| 崩溃后 | 日志重放 | 指向旧根即回滚，但**原地写的文件可能半截**（`nodatacow`） |
| 校验和 | 元数据有，数据通常没有 | 数据与元数据都有（关掉 `datasum` 才没有） |
| 碎片整理的副作用 | 基本无害 | **打断 reflink，空间放大** |

## 环境

- Python 3.8+（自检零依赖）
- Go 1.20+（本机无工具链时走代码审查）

## 运行方式

```bash
cd python && python check.py     # 48 项断言
cd go && go run .
```

## 关键代码

切三段是整套模型的核心（Python 节选，Go 版同构）：

```python
os_, oe = max(lo, e.logical), min(hi, e.end())
if os_ > e.logical:                       # 头：仍指向原物理块
    out.append(Extent(e.logical, e.phys, os_ - e.logical, e.unwritten))
delta = os_ - e.logical                   # 中：释放旧的、分配新的
alloc.drop(e.phys + delta, oe - os_)
out.append(Extent(os_, alloc.alloc(oe - os_), oe - os_))
if oe < e.end():                          # 尾：物理块号要跟着偏移
    out.append(Extent(oe, e.phys + (oe - e.logical), e.end() - oe, e.unwritten))
```

CRC 那一段把 init/xorout 参数化，两种形态共用一个表驱动实现：

```python
def crc32c(data, init=0xFFFFFFFF, xorout=True):
    crc = init
    for byte in data:
        crc = TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return (crc ^ 0xFFFFFFFF) if xorout else crc

def btrfs_csum(data):
    return crc32c(data, init=0, xorout=False)
```

自检里有一条有意思的断言：CRC 对 init 是**仿射**的，所以**等长**数据时两种初值的差是常数；
换成不等长就不成立（`M^len` 随长度变）。这是"看起来的巧合"最容易骗人的地方。

## 性能边界

- 快照的**创建**是 O(元数据)，与数据量无关；**删除**要遍历被引用的 extent 做减引用，
  大快照删除会有一段 IO 抖动。
- CoW 的写放大 = 被改块数 / 逻辑写块数，**在 4K 随机写 + 大块 extent 的场景下最差**。
- `autodefrag` 只对"几十 KB 范围的小随机写"生效（手册写明当前是 64 KiB），
  **不适合大型数据库负载**（手册原文：*May not be well suited for large database workloads*）。
- defrag 的空间放大倍数 ≈ 该文件的 reflink 份数。

## 注意事项与常见坑

1. **别在跑数据库的目录上开 `autodefrag`**，手册直接点名不合适。
2. **`nodatacow` 会连带关掉 `datasum`**，数据校验和也没了，别以为是"只关 CoW"。
3. **`nodatacow` 让写变成原地，中断会产生半个块**（手册：*potential partial writes*），
   对数据库是灾难 —— InnoDB 之类要么开 CoW 要么自己有双写缓冲。
4. **defrag 会扯断 reflink**，在大量快照/去重的环境里可能把磁盘撑爆。
5. **swapfile 必须 NODATACOW 且预分配**，用 `truncate` 造的空洞文件不行。
6. **`FIEMAP_EXTENT_SHARED` 是唯一的"共享"证据**，不要靠 `st_blocks` 反推。
7. `FIEMAP_EXTENT_UNWRITTEN`（已分配但无数据，读出来是 0）与 `SHARED` 是完全不同的位，
   预分配场景要分开判断。
8. **send stream 的 CRC32C 初值为 0 且不取反**，别套用标准 CRC-32C 的参数。
9. `receive` 出来的不是 1:1，别拿 inode 号做跨机比对。
10. `subvol=` 与 `subvolid=` 同时给必须指向同一个 subvolume，否则挂载失败。

## 参考资料

以下均为本 demo 撰写时**实际读取**的资料：

- `btrfs(5)` 手册（btrfs.readthedocs.io，btrfs-man5）：
  <https://btrfs.readthedocs.io/en/latest/btrfs-man5.html>
  （`datacow`/`datasum`/`compress` 互斥、挂载选项顺序、`autodefrag` 64 KiB、
  defrag 打断 reflink、swapfile 约束、`subvol`/`subvolid`、`barrier`）
- Btrfs 文档「Send/receive」：<https://btrfs.readthedocs.io/en/latest/Send-receive.html>
  （full/incremental、非 1:1 重建、命令流、CRC32C 初值 0 不取反）
- Btrfs 文档「Compression」：<https://btrfs.readthedocs.io/en/latest/Compression.html>
  （频率采样与香农熵检测、压缩与 nodatacow/nodatasum 的互斥）
- `include/uapi/linux/fiemap.h`（torvalds/linux, master）：
  <https://raw.githubusercontent.com/torvalds/linux/master/include/uapi/linux/fiemap.h>
  （`FIEMAP_EXTENT_*` / `FIEMAP_FLAG_*` 数值与语义）
