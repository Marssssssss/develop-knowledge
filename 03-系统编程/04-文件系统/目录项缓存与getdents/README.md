# 目录项缓存（dcache）与 `getdents(2)`

路径解析是 Linux 上最热的路径之一：`open("/a/b/c")` 要逐级查目录。dcache 把"名字 → inode"的映射缓存起来，`getdents` 则是遍历大目录时唯一能绕开 `readdir(3)` 缓冲成本的接口。

## 一、dentry 的状态与类型

### `d_flags` 里的类型是一个 **3 位字段**，不是独立标志位

```c
/* include/linux/dcache.h */
DCACHE_ENTRY_TYPE       = (7 << 19),   /* bits 19..21 are for storing type: */
DCACHE_MISS_TYPE        = (0 << 19),   /* Negative dentry */
DCACHE_WHITEOUT_TYPE    = (1 << 19),   /* Whiteout dentry (stop pathwalk) */
DCACHE_DIRECTORY_TYPE   = (2 << 19),   /* Normal directory */
DCACHE_AUTODIR_TYPE     = (3 << 19),   /* Lookupless directory */
DCACHE_REGULAR_TYPE     = (4 << 19),   /* Regular file type */
DCACHE_SPECIAL_TYPE     = (5 << 19),   /* Other file type */
DCACHE_SYMLINK_TYPE     = (6 << 19)    /* Symlink */
```

这带来三条容易写错的性质：

1. **MISS 是 0** —— 所以"清掉类型位"就等于变成 negative dentry；判断 negative 不能只看某个 bit。
2. **DIRECTORY ≠ AUTODIR** —— `d_can_lookup()` 同时接受两者，但 `d_is_directory()` 只认 DIRECTORY。用错会把自动挂载点误判成普通目录。
3. **类型位与标志位互不干扰** —— 同一个 `d_flags` 上可以同时有 `DCACHE_MOUNTED`(bit15)、`DCACHE_LRU_LIST`(bit18) 和类型值，读写类型必须走 `flags & DCACHE_ENTRY_TYPE` 而不是判等。

`DCACHE_MANAGED_DENTRY` 是三个挂载相关位的掩码（`MOUNTED | NEED_AUTOMOUNT | MANAGE_TRANSIT`），pathwalk 遇到它就可能要走 `d_manage()`；它**不含** `DCACHE_LRU_LIST` 或 `DCACHE_CANT_MOUNT`。

### 四种状态

| 状态 | 条件 | 位置 |
| --- | --- | --- |
| in-use | `d_lockref.count > 0` | 挂在父目录 `d_children`，**不在 LRU** |
| unused | `count == 0` | 仍在哈希表 + **在 LRU 上** |
| negative | `d_inode == NULL`（类型 MISS） | 同 unused |
| dying/killed | 已从哈希摘除 | 等 RCU 释放 |

关键：`d_alloc` 出来的 dentry **初始 refcount 就是 1**（in-use），只有 `dput` 归零后才进 LRU。所以"新建即 unused"是错的。

`shrink_dentry_list` 给带 `DCACHE_REFERENCED` 的 dentry **第二次机会**：先清标志再放回队尾，下一轮才真的回收。本 demo 用两个 dentry 的对照把"被 touch 的那个活过第一轮"钉住。

**negative dentry 缓存的是"这个文件不存在"**——一次失败的 lookup 也会留下 dentry，后续同样的查询不必再下探文件系统。这对反复探测文件存在的程序（比如动态库搜索）影响很大。

## 二、`getdents64` 的布局

```c
struct linux_dirent64 {
    ino64_t        d_ino;    /* 64-bit inode number */
    loff_t         d_off;    /* Not an offset; see getdents() */
    unsigned short d_reclen; /* Size of this dirent */
    unsigned char  d_type;   /* File type */
    char           d_name[]; /* null-terminated */
};
```

头部 **19 字节**（8+8+2+1），但 `d_reclen` 还要把下一条对齐到 8 字节（`fs/readdir.c`）：

```c
int reclen = ALIGN(dirent_size(dirent, namlen + 1), sizeof(u64));
```

（`namlen + 1` 是那个 NUL。老接口 `getdents` 用的是 `namlen + 2`，因为 `d_type` 被塞在 `d_reclen - 1` 那个原本是填充的字节上。）

实测记录大小：

| 名字 | 原始 | `d_reclen` |
| --- | --- | --- |
| `a` | 21 | 24 |
| `abc` | 23 | 24 |
| `abcdef` | 26 | 32 |
| `n`×255 | 275 | 280 |

`d_type` 的取值来自 `<dirent.h>`，注意**不连续**（它们就是 `stat` 的模式位右移 12）：

```
DT_UNKNOWN=0  DT_FIFO=1  DT_CHR=2  DT_DIR=4  DT_BLK=6
DT_REG=8      DT_LNK=10  DT_SOCK=12  DT_WHT=14
```

dcache 内部类型到 `d_type` 的映射不是一一对应的：

- `MISS`(negative) → `DT_UNKNOWN`
- `SPECIAL` → **也是 `DT_UNKNOWN`**
- `DIRECTORY` 和 `AUTODIR` **都是** `DT_DIR`
- `WHITEOUT` → `DT_WHT`（overlayfs 用）

man page 明确要求：*"All applications must properly handle a return of DT_UNKNOWN."* 只有部分文件系统（Btrfs、ext2/3/4 等）真正填充 `d_type`。

## 三、EINVAL 的真实时机（最容易踩）

man page 只写了 "EINVAL: Result buffer is too small"，但源码 (`fs/readdir.c`) 的行为更微妙：

```c
buf->error = -EINVAL;   /* only used if we fail.. */
if (reclen > ctx->count)
        return false;
...
/* SYSCALL_DEFINE3(getdents64) 尾部 */
if (error >= 0)
        error = buf.error;
if (buf.prev_reclen) {
        ...
        if (put_user(d_off, &lastdirent->d_off))
                error = -EFAULT;
        else
                error = count - buf.ctx.count;   /* ← 覆盖掉 -EINVAL */
}
```

所以：

1. 装不下就**停在那里，不截断**当前记录
2. 只要**发出过至少一条**，`error` 就被改写成"写入字节数"，预设的 `-EINVAL` 被丢掉
3. `-EINVAL` 只在**一条都装不下**时才对用户可见（缓冲小于第一条的 `d_reclen`）

另外 **最后一条记录的 `d_off` 会被覆写成 `buf.ctx.pos`**（下一次的续读位置），而不是填表时给的值。这正是 man page 说 `d_off` "对用户空间没有明确含义"的原因——别把它当偏移用，`lseek` 回去续读也不可靠。

## 文件说明

- `python/dcache.py` —— `d_flags` 位与类型字段、`d_is_*` 判定、`DCache`（引用计数 / LRU / negative / 两轮收缩）、`linux_dirent64` 打包与 `emit_getdents`
- `python/selfcheck_dcache.py` —— 92 条断言（实跑全绿）
- `python/main.py` —— 演示入口
- `go/dcache.go` + `go/dcache_lru.go` —— Go 侧镜像

## 参考资料（实际读过）

- `include/linux/dcache.h`（`struct dentry`、`struct qstr`、`enum dentry_flags`、`DCACHE_MANAGED_DENTRY`）
  <https://github.com/torvalds/linux/blob/master/include/linux/dcache.h>
- `fs/readdir.c`（`filldir` / `filldir64` 的 `ALIGN(...)` 与 `reclen > ctx->count` 判断、`SYSCALL_DEFINE3(getdents64)` 尾部的 error 覆盖）
  <https://github.com/torvalds/linux/blob/master/fs/readdir.c>
- `getdents(2)`（`struct linux_dirent64`、`DT_*` 清单、"These are not the interfaces you are interested in"）
  <https://man7.org/linux/man-pages/man2/getdents.2.html>
- glibc `dirent/dirent.h`（`DT_UNKNOWN=0 … DT_WHT=14` 的枚举值）
  <https://sourceware.org/git/?p=glibc.git;a=blob;f=dirent/dirent.h>
- `Documentation/filesystems/vfs.html`（dentry 状态与 dcache 语义）
  <https://www.kernel.org/doc/html/latest/filesystems/vfs.html>

> 口径说明：老接口 `struct linux_dirent` 的 `d_type` 位于 `d_reclen - 1` 是 man page 里的说法（该字段从 Linux 2.6.4 起复用原填充字节），本 demo 只在常量上标了 `DIRENT_HEADER`，未建模其打包。dcache 的哈希键在模型里简化成"名字"，内核实际是 (parent dentry, name) 的二元组。
