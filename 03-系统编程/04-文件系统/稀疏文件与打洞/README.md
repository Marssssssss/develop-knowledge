# 稀疏文件：洞在哪、怎么打、打完还算不算文件的一部分

## 简介

`truncate -s 1G bigfile` 一秒钟就造出一个 1 GiB 的文件，而 `du` 说它占 0 字节 ——
这就是**稀疏文件**：文件里有一段"从来没被分配过的零"。

问题随之而来：备份工具怎么知道哪段是洞？虚拟机镜像怎么回收客户机删掉的空间？
数据库怎么预分配空间而不真的写零？答案是同一组接口：

- `lseek(fd, offset, SEEK_DATA / SEEK_HOLE)` —— **扫洞**
- `fallocate(fd, FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE, ...)` —— **打洞**
- `fallocate(fd, 0, ...)` 及另外四种 mode —— **预分配 / 清零 / 折叠 / 插入**
- `ioctl(FS_IOC_FIEMAP)` —— **一次性拿到 extent 布局**

本 demo 用 **Python（40 项断言，可实跑）+ Go + C（真实系统调用）** 复刻这套语义。

## 原理详解

### 1. SEEK_DATA / SEEK_HOLE 的精确边界（lseek(2)）

| 情形 | 行为 |
| --- | --- |
| `offset` 落在洞里，问 `SEEK_HOLE` | **原样返回 offset**（哪怕 offset 在洞的中间） |
| `offset` 落在数据里，问 `SEEK_DATA` | 原样返回 offset |
| 后面还有目标区间 | 返回下一个区间的起始偏移 |
| 后面**没有**洞了 | 返回**文件末尾**（手册：*there is an implicit hole at the end of any file*） |
| 后面**没有**数据了 | `ENXIO`（*offset is within a hole at the end of the file*） |
| `offset` 越过 EOF（两种情况都是） | `ENXIO` |

最后一行是最容易写错的地方：**`offset == st_size` 也还算"在末尾的洞里"**，
所以扫描循环必须以 `ENXIO` 收尾，而不是靠 `offset >= size` 判断结束。

### 2. "洞"的定义比你想的宽松

lseek(2) 原文：洞是"一段（通常）没有被分配过的零"。注意两个例外：

- **文件系统没有义务报告洞**（*a filesystem is not obliged to report holes*），
  所以这两个操作**不是**"文件实际占用空间"的可靠测量手段；
- **真实写过的一串零也不一定被报告成洞**。

手册甚至给出了一个被允许的**退化实现**：`SEEK_HOLE` 恒返回 EOF、`SEEK_DATA` 恒返回 offset。
demo 里把它做成了对照组：备份工具在真实语义下只拷 1 块，在退化实现下拷整个文件 ——
**结果都对，但省下的空间差 16 倍**。

支持 `SEEK_DATA`/`SEEK_HOLE` 的文件系统（手册清单）：Btrfs（3.1）、OCFS（3.2）、
XFS（3.5）、ext4（3.8）、tmpfs（3.8）、NFS（3.18）、FUSE（4.5）、GFS2（4.15）。

### 3. fallocate 的五种模式

| mode | 语义 | 关键约束 |
| --- | --- | --- |
| `0` | 预分配并清零，后续写不会因空间不足失败 | 会**改文件大小**；按块向上取整，可能分得比请求的多 |
| `KEEP_SIZE` | 同上但不改大小 | 适合给追加负载预留空间 |
| `PUNCH_HOLE` | 打洞：整块移除，跨界的部分块清零 | **必须与 `KEEP_SIZE` 一起给**，否则 `EINVAL`；打完读出来是 0 |
| `ZERO_RANGE` | 把区间转成 unwritten extent | 只用元数据 IO，不真的写零 |
| `COLLAPSE_RANGE` | 整段移除且**不留洞**，后面的内容前移 | 粒度必须是 fs 逻辑块倍数；触及/越过 EOF 报错（改用 `ftruncate`）；**不能与其他标志并用** |
| `INSERT_RANGE` | 插入一段洞，后面的内容后移，文件变大 | 同样有粒度要求；`offset >= EOF` 报错 |

`COLLAPSE_RANGE` / `INSERT_RANGE` 互为逆操作：前者"抽掉一段"，后者"塞进一段"，
两者的块集合变化正好相反（demo 有断言）。

### 4. FIEMAP 与 UNWRITTEN

`fallocate` 预分配出来的块在 FIEMAP 里带 `FIEMAP_EXTENT_UNWRITTEN`
（fiemap.h：*Space allocated, but no data (i.e. zero)*）—— **占了空间但没有数据**。
这与"洞"是两回事：洞压根没分配。判断一个区间到底是哪种，看的正是这个标志。

## 对比

| | 洞（hole） | 预分配未写（unwritten） | 写过的零 |
| --- | --- | --- | --- |
| 占物理空间 | 否 | **是** | 是 |
| 读出来 | 0 | 0 | 0 |
| `st_blocks` 计入 | 否 | 是 | 是 |
| FIEMAP 标志 | 不出现在 extent 里 | `UNWRITTEN` | 无 |
| `SEEK_DATA` 会不会停 | 不停 | **会停**（已分配） | 会停 |

## 环境

- Python 3.8+（自检零依赖）
- Go 1.20+ / gcc（C 版需要 Linux，非 Linux 会打印提示后退出）

## 运行方式

```bash
cd python && python check.py                       # 40 项断言
cd go && go run .
gcc -D_GNU_SOURCE -O2 -Wall -Wextra c/sparse_holes.c -o sparse_holes && ./sparse_holes
```

## 关键代码

扫描循环（Python 节选）—— **`ENXIO` 是正常终止信号，不是错误**：

```python
while off < f.size:
    try:
        d = lseek_fn(f, off, SEEK_DATA)
    except OSError as e:
        if "ENXIO" in str(e):
            break          # 扫到文件末尾的洞了
        raise
    h = lseek_fn(f, d, SEEK_HOLE)
    copied += h - d
    off = h
```

`SEEK_HOLE` 落在洞中间要原样返回（Go 版节选）：

```go
for b := start; b < f.NBlocks(); b++ {
    if f.IsHole(b) == wantHole {
        if b == start {
            return offset, nil   // 已经落在目标区间里
        }
        return b * block, nil
    }
}
if wantHole {
    return f.Size, nil           // 末尾之后是隐式洞
}
return 0, fmt.Errorf("ENXIO: offset 落在文件末尾的洞里")
```

## 性能边界

- 扫洞的成本是 O(extent 数)，**不是** O(文件大小) —— 但每趟至少两次 `lseek` 系统调用，
  大文件建议配合 `FIEMAP` 一次拿全。
- 打洞的成本正比于被打的 extent 数；**对齐到块边界**才能整块移除，
  否则只能把跨界的部分块清零（省不到空间）。
- `ZERO_RANGE` 不写数据、只改元数据，是"清零大文件"最快的方式。
- `COLLAPSE_RANGE` / `INSERT_RANGE` 需要整体搬移后面的内容，**成本正比于文件大小**，
  不适合在超大文件头部反复使用。

## 注意事项与常见坑

1. **`PUNCH_HOLE` 忘了 `|KEEP_SIZE` 会直接 `EINVAL`**，而且这个组合 requirement 很容易被忽略。
2. **打洞不改变文件大小**：`st_size` 照旧，只有 `st_blocks` 变小。想缩文件请用 `ftruncate`。
3. **`ENXIO` 是扫描的终止信号**，把它当错误处理会漏拷最后一段或误报失败。
4. **`offset == st_size` 也报 `ENXIO`**（末尾隐式洞）。
5. **别把 `SEEK_DATA`/`SEEK_HOLE` 当"实际占用"的权威口径**：fs 可以不报告洞，
   真写的零也可能被当成洞。要准确就用 `FIEMAP` 或 `st_blocks`。
6. **`fallocate` 按块向上取整**，请求 100 字节可能占掉 4096 甚至跨两块。
7. **`COLLAPSE_RANGE` / `INSERT_RANGE` 不能与其他标志并用**，且粒度必须整块。
8. **不是所有 fs 都支持打洞**（手册只保证 XFS/ext4/Btrfs/tmpfs/gfs2 支持 `PUNCH_HOLE`），
   不支持时 `fallocate` 返回错误。
9. **`UNSHARE_RANGE` 依赖文件系统支持**，不支持会失败。
10. 在 **CoW 文件系统（Btrfs）上打洞**要注意它可能与快照/reflink 交互：打洞会让共享的
    extent 断开（参见同目录的 `CoW与快照`）。

## 参考资料

以下均为本 demo 撰写时**实际读取**的资料：

- `lseek(2)` Linux man-pages 6.19：<https://man7.org/linux/man-pages/man2/lseek.2.html>
  （`SEEK_DATA` / `SEEK_HOLE` 语义、洞中间原样返回、末尾隐式洞、`ENXIO` 两种触发、
  "文件系统没有义务报告洞"、最简退化实现、支持的 fs 与内核版本清单）
- `fallocate(2)` Linux man-pages 6.19：<https://man7.org/linux/man-pages/man2/fallocate.2.html>
  （默认模式与 `KEEP_SIZE`、`PUNCH_HOLE` 必须 OR `KEEP_SIZE`、按块向上取整、
  `COLLAPSE_RANGE` / `ZERO_RANGE` / `INSERT_RANGE` 的语义与 EINVAL 条件、各 fs 支持版本）
- `include/uapi/linux/fiemap.h`（torvalds/linux, master）：
  <https://raw.githubusercontent.com/torvalds/linux/master/include/uapi/linux/fiemap.h>
  （`FIEMAP_EXTENT_UNWRITTEN` / `FIEMAP_EXTENT_LAST` 等标志数值）
