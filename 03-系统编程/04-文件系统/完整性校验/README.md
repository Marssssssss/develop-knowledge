# fs-verity 与 dm-integrity：只读完整性校验

两个"完整性"方案，粒度与威胁模型完全不同：

| | fs-verity | dm-integrity |
| --- | --- | --- |
| 层级 | 文件系统（per-file） | 块设备（per-sector） |
| 可写性 | **只读文件**（开启后内容不可改） | 可读写 |
| 结构 | Merkle 树 | 交织的 tag 区 + 日志/位图 |
| 校验时机 | 读数据页时逐级验 hash | 每次 IO 验 tag |
| 主要威胁 | 内容被离线篡改后仍被信任 | 静默数据损坏（bit rot） |
| 典型用法 | Android APK、容器镜像、只读系统分区 | LUKS2 认证加密、RAID 之上的额外校验 |

两者可以叠加：dm-crypt 生成 integrity payload 交给 dm-integrity，就是"认证加密"。

## fs-verity

### 1. 分块与扇出

> The file contents is divided into blocks, where the block size is configurable but is usually 4096 bytes. … Each block is then hashed, producing the first level of hashes. Then, the hashes in this first level are grouped into 'blocksize'-byte blocks (zero-padding the ends as needed) and these blocks are hashed, producing the second level of hashes.

扇出 = `block_size / digest_size`：

- SHA-256 + 4 KiB 块 → **128**（文档给的推荐配置）
- SHA-512 + 4 KiB 块 → **64**

因此大文件的树大小收敛到原文的 **1/127**——`1 + 1/128 + 1/128² + …` 减去自身那一项的倒数关系。实测 8 GiB 文件（2097152 块）树占 67637248 字节 = 0.0079 ≈ 1/127。

但**小文件的填充代价很重**：2 块（8 KiB）文件也要一整块（4 KiB）树，开销 **50%**。而 1 块文件**完全没有树**——root 就是那块数据的 hash（文档原文：*"If the file fits in one block and is nonempty, then the Merkle tree root hash is simply the hash of the single data block"*）。空文件的 root 是**全 0**（不是空串的 hash！）。

### 2. salt 的零填充口径

> If a salt was specified, then it's zero-padded to the closest multiple of the input size of the hash algorithm's compression function, e.g. 64 bytes for SHA-256 or 128 bytes for SHA-512. The padded salt is prepended to every data or Merkle tree block that is hashed.

注意填充到的是**压缩函数输入块**（64/128 字节），不是摘要长度，也不是文件系统块大小。所以 13 字节 salt 在 SHA-256 下变成 64 字节，65 字节 salt 变成 128 字节。

两处填充各有目的：*block padding* 让每个 hash 输入等长（便于硬件加速），*salt padding* 让"预算好 salted 状态再 import"成为可能，salting 因而是"免费"的。

### 3. 为什么要 descriptor（文件摘要 ≠ root）

> By itself, the Merkle tree root hash is ambiguous. For example, it can't distinguish a large file from a small second file whose data is exactly the top-level hash block of the first file.

所以"fs-verity 文件摘要"是 **hash(descriptor)**，descriptor 里带上 `version / hash_algorithm / log_blocksize / salt_size / data_size / root_hash[64] / salt[32]`，定长 **256 字节**小端。

字段偏移（`include/uapi/linux/fsverity.h`）：

```
0  version(1)  1  hash_algorithm(1)  2  log_blocksize(1)  3  salt_size(1)
4  __reserved_0x04(4)
8  data_size(8, le64)
16 root_hash[64]      ← 装得下 SHA-512，装 SHA-256 时后 32 字节为 0
80 salt[32]
112 __reserved[144]
```

`root_hash` 是 64 字节字段而不是 32——用 SHA-256 时后半段恒为 0，这是最容易在实现里写错的地方。

## dm-integrity

盘上布局（`Documentation/admin-guide/device-mapper/dm-integrity.html`）：

```
[保留区][superblock 4KiB][journal 区][交织的 (tag 区 + 数据区) × N]
```

superblock 里存 `log2(interleave sectors)`、`integrity tag size`、journal section 数、`provided_data_sectors`、`log2(sectors per block)` 等。

关键参数与默认值：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `interleave_sectors` | 32768 | **向下取到 2 的幂** |
| `buffer_sectors` | 128 | 向下取到 2 的幂 |
| `block_size` | 512 | 只能取 512/1024/2048/4096 |
| `journal_watermark` | 50 | 百分比 |
| `commit_time` | 10000 | 毫秒 |

文档给的实例可以直接算：

> on a device using the default interleave_sectors of 32768, a block_size of 512, and an internal_hash of crc32c with a tag size of 4 bytes, it will take 128 KiB of tags to track a full data area, requiring **256 sectors of metadata** per data area. With the default buffer_sectors of 128, that means there will be **2 buffers** per metadata area, or 2 buffers per 16 MiB of data.

`32768 × 4 = 131072 B = 128 KiB = 256 扇区` ✓；`256 / 128 = 2` buffers ✓。

四种模式：`D` 直写（数据与 tag 分开写，崩溃后可能不匹配）、`J` 日志写（原子，写吞吐减半）、`B` 位图（快，但崩溃时的损坏可能查不出）、`R` 恢复模式（只许读）、`I` 内联（tag 存进底层设备的 integrity profile）。

一个容易踩的点：格式化流程要求**先把 superblock 清零**，内核只在 superblock 全 0 时才格式化；既非全 0 又非法就直接拒绝加载。格式化后用 1 扇区大小挂载、读出 `provided_data_sectors`、再按该大小重新加载。

## 文件说明

- `python/verity.py` —— 常量、salt 填充、层级/扇出/开销、Merkle 建树与块级验证、descriptor 序列化、dm-integrity 几何
- `python/selfcheck_verity.py` —— 70 条断言（实跑全绿）
- `python/main.py` —— 演示入口
- `go/verity.go` —— Go 侧镜像

## 参考资料（实际读过）

- `Documentation/filesystems/fsverity.html`（全文 68 KB，Merkle tree / descriptor / builtin signature 三节）
  <https://www.kernel.org/doc/html/latest/filesystems/fsverity.html>
- `include/uapi/linux/fsverity.h`（`FS_VERITY_HASH_ALG_*`、`struct fsverity_descriptor`、`FS_VERITY_METADATA_TYPE_*`）
  <https://github.com/torvalds/linux/blob/master/include/uapi/linux/fsverity.h>
- `Documentation/admin-guide/device-mapper/dm-integrity.html`（参数表、盘上布局、status 行）
  <https://www.kernel.org/doc/html/latest/admin-guide/device-mapper/dm-integrity.html>

> 口径说明：`FS_VERITY_MAX_SALT_SIZE` 的常量名在内核内部头里，本次未取到原文，demo 取文档明文写的 32 字节并与 `salt[32]` 字段互证。`dm-integrity` 的 `data_sectors` 需为 2 的幂（superblock 只存 `log2`）这一约束来自文档 "The number of data sectors in one run must be a power of two"，模型按此实现；真实驱动在该约束下如何调整 interleave 未进一步核对源码。
