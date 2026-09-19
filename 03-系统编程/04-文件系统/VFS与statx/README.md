# VFS 四大对象与 statx(2)：请求掩码不等于返回掩码

## 简介

Linux VFS 用四个内核对象把"一个文件"拆开：`super_block`（一个已挂载的文件系统）、
`inode`（文件本身，与名字无关）、`dentry`（名字到 inode 的映射与缓存）、`file`（一次打开的上下文）。
用户态能直接摸到的只有它们的**投影**——`stat()` 家族返回的那一堆字段。

`stat()` 的字段几十年没加过，塞不进 btime、mount id、O_DIRECT 对齐这些信息。
所以 Linux 4.11 引入了 `statx(2)`，它多带一个 `mask` 参数：**你告诉内核要哪些字段，
内核用它填回来的 `stx_mask` 告诉你实际给了哪些**。

本 demo 的关键点是那句容易被忽略的话（statx(2) 原文）：

> In either case, `stx_mask` will not be equal `mask`.

也就是 **"我请求了"和"我拿到了"是两件事**。本 demo 用 **Python（36 项断言，可实跑）+ Go + C**
把这套语义做成可执行模型。

## 原理详解

### 1. 四种组合（`include/uapi/linux/stat.h` 头注释原文归纳）

对每个 mask 位：

| 情况 | 内核行为 | `stx_mask` |
| --- | --- | --- |
| 该字段**不被支持** | 清位，编造一个兼容值（CIFS 的挂载 uid/gid 就是这么来的），否则清零 | 清 |
| **显式请求**且支持 | 填充并置位 | 置 |
| **未请求**但"顺手可得"（头注释：*available in approximate form without any effort*） | 照样填充并置位 | 置（**超出请求范围**） |
| 其余 | 字段与位都清掉 | 清 |

所以正确的代码长这样：

```c
if (buf.stx_mask & STATX_BTIME)   /* 只有置位才可以读 stx_btime */
        use(buf.stx_btime);
```

### 2. 请求位清单

`STATX_BASIC_STATS`（`0x7ff`）是 `TYPE|MODE|NLINK|UID|GID|ATIME|MTIME|CTIME|INO|SIZE|BLOCKS`
11 位的或，正好对应老 `stat` 结构能表达的字段。
`STATX_ALL`（`0xfff`）= `BASIC_STATS|BTIME` 已被**废弃** —— 头文件注释明说
"to avoid confusion please use the equivalent"，直接写 `STATX_BASIC_STATS | STATX_BTIME`。

`STATX__RESERVED`（`0x80000000`）是**唯一**会让 `statx()` 返回 `EINVAL` 的掩码位。
手册专门警告：不要把 mask 设成 `UINT_MAX`，因为将来可能有位被拿去做"结构体扩展"的开关。

### 3. 属性位必须再掩一次

`stx_attributes` 与 `stx_attributes_mask` **逐位对应**；不在 mask 里的属性位"没有可用值"
（手册原文：*any attribute that is not indicated as supported by stx_attributes_mask has no usable value*）。
demo 里的 ext4 报了 `DAX` 但 `stx_attributes_mask` 不含 DAX，读出来必须当它不存在。

### 4. 字段之间不保证同一时刻

statx(2) 明说：出于性能与实现简单的考虑，**同一个 struct statx 里的不同字段可能来自不同时刻**。
另一进程并发 `chmod` / `chown` 时，你可能拿到旧 `stx_mode` 配新 `stx_uid`。
demo 用 `on_field` 钩子在"填完 mode 之后"改 uid 复现了这个组合。

### 5. 几个字段的单位与边界

- `stx_blocks` 是 **512 字节**单位（不是 `stx_blksize`，也不是 1024）。POSIX 甚至没规定单位，
  只是 Linux 上普遍是 512（inode(7)）。有洞的文件会 `stx_blocks*512 < stx_size`。
- 符号链接的 `stx_size` 是**目标路径长度，不含结尾 NUL**。
- `stx_dio_mem_align` / `stx_dio_offset_align` 成对出现：一个非 0 另一个必非 0，
  两个都为 0 表示这文件不支持 O_DIRECT（见 `STATX_DIOALIGN`，Linux 6.1 起）。

### 6. `flags` 里的三态同步

`AT_STATX_SYNC_TYPE`（`0x6000`）掩出同步策略：`AS_STAT`(0) / `FORCE_SYNC`(0x2000) / `DONT_SYNC`(0x4000)。
**两个位同时置位（0x6000）是非法组合**。这是给网络文件系统用的：`DONT_SYNC` 可能不产生一次服务器往返，
但拿到的可能是缓存里的近似值。

## 对比

| | `stat()` / `fstatat()` | `statx()` |
| --- | --- | --- |
| 出生时间 | 4.3BSD / POSIX.1-1988 | Linux 4.11，glibc 2.28 |
| 请求字段 | 无，全给 | `mask` 请求 + `stx_mask` 回执 |
| btime（创建时间） | 拿不到 | `STATX_BTIME`（fs 支持才有） |
| mount id | 拿不到 | `STATX_MNT_ID` / 6.8 起 `STATX_MNT_ID_UNIQUE` |
| O_DIRECT 对齐 | 拿不到，只能猜 | 6.1 起 `STATX_DIOALIGN` |
| 时间戳精度 | `struct timespec` | `struct statx_timestamp`（`__s64 tv_sec` + `__u32 tv_nsec`） |
| 不支持的字段 | 编造值，你无从分辨 | **位被清掉，可分辨** |
| 符号链接 | 要换 `lstat()` | `AT_SYMLINK_NOFOLLOW` 一个 flag |

## 环境

- Python 3.8+（自检脚本零依赖）
- Go 1.20+ / gcc（本机无工具链时走代码审查）

## 运行方式

```bash
python check.py      # 36 项断言，全部实跑
go run vfs_statx.go
gcc -O2 -Wall -Wextra vfs_statx.c -o vfs_statx && ./vfs_statx
```

## 关键代码

Python 侧把"四种组合"直接写成循环（节选）：

```python
for bit in [b for b, _ in MASK_NAMES]:
    if not (mask & bit):
        continue
    if not (fs.supported & bit):
        _fill_dummy(buf, inode, fs, bit)   # 位保持清除
        continue
    _fill(buf, inode, fs, bit)
    got |= bit
    if on_field is not None:
        on_field(bit)                      # 埋点：模拟并发修改

for bit in [b for b, _ in MASK_NAMES]:     # 没请求但顺手可得
    if mask & bit:
        continue
    if (fs.free_bits & bit) and (fs.supported & bit):
        _fill(buf, inode, fs, bit)
        got |= bit
```

Go 侧是同一个 `Resolve()`，C 侧是同一个 `resolve_mask()` —— 三份实现刻意保持同题，
方便对照三种语言在"位运算 + 错误返回"上的写法差异（Go 返回 `(uint32, uint64, error)`，
C 用返回码 `-1` 表示 `EINVAL` 并靠出参回传）。

## 性能边界

- `statx()` 相比 `stat()` 的**收益**不是"更快"，而是"少问几次"：一次调用拿到 btime + mount id +
  DIO 对齐，省掉 `stat()` + `name_to_handle_at()` + ioctl 三趟。
- `AT_STATX_DONT_SYNC` 在网络文件系统上可能**避免一次服务器往返**，代价是值可能过期。
- `stx_mask` 检查本身几乎零成本（一次与运算），但**省掉它就是正确性 bug**——
  读到的是编造值还是真值，只有 `stx_mask` 说得清。
- 别为了"省一次调用"把 mask 拉满：手册与头文件都提醒将来会有位被用作扩展开关。

## 注意事项与常见坑

1. **`stx_mask != mask` 是常态，不是异常**。写 `assert(mask == buf.stx_mask)` 一定是错的。
2. **不要设 `mask = UINT_MAX`**：可能踩到 `STATX__RESERVED`（`EINVAL`）或未来的扩展位。
3. **属性位要相与两次**：`stx_attributes & stx_attributes_mask` 之后才判断具体位。
4. **`stx_btime` 不被多数文件系统支持**（inode(7)："not currently supported by most Linux filesystems"），
   请求了也白请求，必须看位。
5. **`stx_blocks` 单位是 512 字节**，拿它乘 `stx_blksize` 会算出天文数字。
6. **字段可能来自不同时刻**，别假设 struct 内部一致；要一致性自己去串行化。
7. `STATX_ALL` 已废弃，写了会被同行认出是抄老代码。
8. `AT_EMPTY_PATH` 是"按 fd 查"的开关（配空路径或 `O_PATH` fd）；`AT_SYMLINK_NOFOLLOW`
   才是拿链接本身的，别混。
9. `STATX_MNT_ID_UNIQUE` 保证**运行期不复用** mount id；老的 `STATX_MNT_ID` 会复用，
   拿它做缓存键在卸载重装后会串。

## 参考资料

以下均为本 demo 撰写时**实际读取**的资料：

- `statx(2)` Linux man-pages 6.19：<https://man7.org/linux/man-pages/man2/statx.2.html>
  （mask/stx_mask 语义、字段逐条说明、属性位、`AT_STATX_*` 三态、错误码）
- `inode(7)` Linux man-pages：<https://man7.org/linux/man-pages/man7/inode.7.html>
  （`st_mode` 与 `S_IFMT` 类型宏、时间戳语义、512 字节 blocks、btime 支持面）
- `include/uapi/linux/stat.h`（torvalds/linux, master）：
  <https://raw.githubusercontent.com/torvalds/linux/master/include/uapi/linux/stat.h>
  （`STATX_*` / `STATX_ATTR_*` 数值，以及"四种组合"的权威头注释）
- `include/uapi/linux/fcntl.h`（torvalds/linux, master）：
  <https://raw.githubusercontent.com/torvalds/linux/master/include/uapi/linux/fcntl.h>
  （`AT_*` 数值与 `AT_STATX_SYNC_TYPE`）
