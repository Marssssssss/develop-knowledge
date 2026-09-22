# 592 SquashFS 固件文件系统解析

路由器固件的 rootfs 绝大多数是 squashfs：只读、压缩率高、元数据也压缩。
认出 superblock 后拿到四张表的偏移，就能按 `<block, offset>` 还原整棵目录树。

## 1. superblock：96 字节、全小端

`fs/squashfs/squashfs_fs.h`：

```c
struct squashfs_super_block {
    __le32 s_magic;              /* 0x73717368 == "hsqs" */
    __le32 inodes, mkfs_time, block_size, fragments;
    __le16 compression, block_log, flags, no_ids, s_major, s_minor;
    __le64 root_inode, bytes_used, id_table_start, xattr_id_table_start;
    __le64 inode_table_start, directory_table_start, fragment_table_start;
    __le64 lookup_table_start;
};
```

字段偏移（对着 hexdump 找表时用）：magic 0、compression 20、block_log 22、flags 24、
s_major 28、root_inode 32、bytes_used 40、id_table_start 48、xattr 56、
inode_table_start 64、directory_table_start 72、fragment_table_start 80、lookup_table_start 88。

`block_log` 与 `block_size` 必须满足 `1 << block_log == block_size`，这是自检镜像是否合法的第一道判据。
flags 用 `SQUASHFS_BIT(flag, bit) = (flag >> bit) & 1` 取位：NOI=0、NOD=1、NOF=3、
NO_FRAG=4、ALWAYS_FRAG=5、DUPLICATE=6、EXPORT=7、COMP_OPT=10。
压缩 id：zlib=1、lzma=2、lzo=3、xz=4、lz4=5、zstd=6。

## 2. 元数据块：2 字节头，bit15 = 未压缩

`Documentation/filesystems/squashfs.rst` §3.2：元数据（inode 与目录）以 **8K** 为单位压缩，
每个压缩块前有一个 2 字节长度，**最高位（bit15）置位表示该块未压缩**（压缩反而变大，或用 `-noI` 时）。

同表内的块是**首尾相接**的：`hdr(2) + data(n)` 之后紧跟下一个 `hdr(2)`，块之间不补对齐。

`SQUASHFS_COMPRESSED_SIZE(B)` 有个容易被读漏的分支：

```c
#define SQUASHFS_COMPRESSED_SIZE(B) (((B) & ~BIT) ? (B) & ~BIT : BIT)
```

低 15 位全为 0 时，返回的是 **`BIT` 本身即 32768**，而不是 0。所以 `0x8000` 表示
「未压缩且长度 32768」，而 `0x0000` 也算未压缩、长度同样取 32768。

数据块的长度头用的是另一套位：`SQUASHFS_COMPRESSED_BIT_BLOCK = 1 << 24`。

## 3. inode 号是 <block, offset> 的 48 位编码

```c
#define SQUASHFS_INODE_BLK(A)     ((unsigned int) ((A) >> 16))
#define SQUASHFS_INODE_OFFSET(A)  ((unsigned int) ((A) & 0xffff))
#define SQUASHFS_MKINODE(A, B)    (((long long)(A) << 16) + (B))
```

高 32 位是「所在元数据块的块号」，低 16 位是「解压后块内偏移」。
inode **不跨块、也不按块边界对齐**，所以同一个块里会挤着很多 inode —— 这正是 squashfs 体积小的来源。

## 4. fragment：尾部打包

小文件的尾巴会被塞进一个共享的 fragment 块（`SQUASHFS_INVALID_FRAG = 0xffffffff` 表示没有）。
fragment 查找表本身也被压成元数据块，每项 16 字节（`start_block` u64 + `size` u32 + `unused` u32）：
512 项正好占满一个 8K 块，第 513 项就要跨块，索引项则是每项 8 字节的 u64。

lookup 表（inode 号 → 磁盘位置）每项 8 字节；uid/gid 表每项 4 字节 —— 两者都套了「第二级索引表」，
因为索引表很小，挂载时可以直接读进内存缓存。

## 5. 目录：两级结构 + count 存的是 n-1

目录不是简单的名字列表，而是「一个共享 start_block 的目录头 + 一串目录项」重复若干次。
目录头里的 `count` 字段**存的是条目数减一**，`fs/squashfs/dir.c` 里明确：

```c
dir_count = le32_to_cpu(dirh.count) + 1;
if (dir_count > SQUASHFS_DIR_COUNT)   /* 256 */
```

所以 `count` 的合法范围是 0..255，读到 256 会让 `dir_count` 变成 257 而越界。

## 6. 普通文件的数据块数

块长表项数 = `ceil(file_size / block_size)`，但**尾部进了 fragment 就不再单列一个整块**：

```python
full, rem = divmod(file_size, bs)
if rem and not has_fragment:
    full += 1
```

`SQUASHFS_FILE_MAX_SIZE = 1048576`（1 MiB）是单个数据块的上限，超过要切块。

## 运行

```bash
python python/main.py
python python/selfcheck_squashfs.py     # 88 条断言
cd go && go run .
```

## 参考资料

- [Linux `fs/squashfs/squashfs_fs.h`](https://github.com/torvalds/linux/blob/master/fs/squashfs/squashfs_fs.h) — superblock / flags / 压缩 id / 48 位 inode 编码 / `COMPRESSED_SIZE`
- [Linux `Documentation/filesystems/squashfs.rst`](https://github.com/torvalds/linux/blob/master/Documentation/filesystems/squashfs.rst) — §3.2 inodes、§3.3 directories、§3.4 file data、§3.5 fragment 表
- [Linux `fs/squashfs/dir.c`](https://github.com/torvalds/linux/blob/master/fs/squashfs/dir.c) — `dir_count = count + 1`
