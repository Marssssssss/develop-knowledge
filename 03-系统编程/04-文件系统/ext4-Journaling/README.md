# ext4 Journaling (JBD2)

## 简介

**JBD2(Journaling Block Device, version 2)** 是 ext3/ext4 文件系统的预写日志(Write-Ahead Log, WAL)模块,保证系统崩溃后**文件系统元数据**修改的原子性。

- **关键概念清单**
  - **事务(transaction)**:一组对磁盘块的修改,要么全部生效、要么全部回滚;以 **commit block** 为"全部落盘"标记
  - **预写日志(WAL)**:先把修改写到日志区,再写到最终位置;崩溃后 replay 重做已 commit 的事务,丢弃未 commit 的事务
  - **块类型**:JBD2 块首 12 字节是 `journal_header_t` (`h_magic`/`h_blocktype`/`h_sequence`);**有 5 类**:descriptor(1)/commit(2)/journal superblock v1(3)/v2(4)/revocation(5)
  - **descriptor block**:含 `journal_block_tag_s` 数组,每个 tag 描述后续 data block 的最终落盘位置(blocknr)+ flag(8/12/24/38 字节 4 种格式)
  - **block revocation**:某次事务**覆盖**了已写过 journal 的更旧块 → 记录该块号,让 replay 跳过旧副本,加速 recovery
  - **字节序**:JBD2 内部所有字段是 **big-endian**;但 ext4 其他元数据是 little-endian — 注意 swap 字节
  - **3 种 mount data 模式**:`data=ordered`(默认,只 journal 元数据,先 data 后 metadata)/`data=journal`(data 也走 journal,最慢最安全)/`data=writeback`(data 不强制刷,仅 metadata 原子)
- **历史背景**:ext2 无日志,系统崩溃需 `e2fsck` 全盘扫描;ext3(Stephen Tweedie 1999)首次引入 JBD 实现日志;ext4 沿用并演进为 JBD2,加入 64-bit blocknr、checksum、async commit 等

## 原理详解

### 1. 工作机制:写一个事务的 5 步

1. **事务开始** → 内核分配一个 `transaction_t`;客户端(journal_start)持有 handle 引用
2. **写 dirty buffer** → 通过 `JBD2_journal_dirty_metadata()` 把要修改的 buffer 登记进 handle 的 list,**`journal_file_buffer()` 把 buffer 内容 copy 到 shadow 区域**(原始内容以便回滚;JBD2 同时写目标块和日志)
3. **事务关闭** → `journal_stop()` → 当 handle 引用归零、事务达到阈值或 `journal_start_commit()` 被调用,事务进入"提交"阶段
4. **写日志** → 写 `descriptor_block`(含 blocknr + flags + 序号 h_sequence)→ 紧接着写各 data block 的副本
5. **写 commit block** → 当所有 data block 已 flush 到磁盘,**再写 commit block**(32 字节,但占满 1 个磁盘块)→ 至此事务持久化;此后 checkpoint(checkpoint=把 data block 写到最终位置)可异步进行

崩溃 recovery:从 journal 头顺序扫描;遇到 commit block 前的事务 = 完整事务,**replay**;遇到 commit block 但中间缺块(校验和不匹配) = 丢弃该未完成事务。

### 2. ASCII 时序图

```
sys_write(新建文件)
  → VFS journal_start(handle=T1)          T1.active++
  → inode 标记 dirty                      handle->t_buffers += inode_buf
  → journal_stop(handle=T1)               T1.active-- == 0 → 进入 log commit 阶段
  → 写 [Descriptor blk]  h_magic=0xC03B3998 / h_blocktype=1 / h_sequence=42
                         tag[0].blocknr=256, tag[0].flags(ESCAPED? / SAME_UUID)
  → 写 [Data block #0]   inode 内容        ┐ 
  → 写 [Data block #1]   dir entry 块      ┘ 日志文件(/dev/sdaN,通常 inode 8)
  → 写 [Commit blk]      h_blocktype=2 / h_sequence=42 → T1 commit 完成
  → checkpoint 后台写 → inode_buf 落到最终位置 .inode_table[block 256]
```

### 3. JBD2 块结构(`struct journal_header_s`,前 12 字节)

| Offset | Type | Name | 描述 |
|--------|------|------|------|
| 0x00 | `__be32` | `h_magic` | JBD2 magic = `0xC03B3998` |
| 0x04 | `__be32` | `h_blocktype` | 1=descriptor / 2=commit / 3=jsb v1 / 4=jsb v2 / 5=revocation |
| 0x08 | `__be32` | `h_sequence` | 事务 ID,同一事务的所有块共享 |

### 4. 关键 API / 常量

JBD2 是内核子系统,用户态通过 mount 选项 + `tune2fs` 配置:

| 常量 | 值 | 含义 |
|------|------|------|
| `JBD2_MAGIC_NUMBER` | `0xC03B3998` | 块首 magic |
| `JBD2_DESCRIPTOR_BLOCK` | 1 | descriptor 块类型 |
| `JBD2_COMMIT_BLOCK` | 2 | commit 块类型 |
| `JBD2_REVOKE_BLOCK` | 5 | revocation 块类型 |
| `JBD2_FLAG_ESCAPE` | 1 | data block 前 4 字节与 magic 相同 → 被替换为 0 + set escape flag |
| `JBD2_FLAG_SAME_UUID` | 2 | tag 内 uuid 与 jsb 相同 → 省略 16B uuid |
| `JBD2_FLAG_DELETED` | 4 | 块已被删除(不写最终位置) |
| `JBD2_FLAG_LAST_TAG` | 8 | 块 tag 数组结束标记 |

mount 选项(`/etc/fstab` 的 `data=` 或 `mount -o`):
- `data=ordered`(默认):只 journal metadata,data flush 先于 metadata commit
- `data=writeback`:data 不强制顺序;可能文件内 0KB 块
- `data=journal`:data + metadata 都 journal;最安全,最慢(~3x 写延迟)

### 5. 字节序陷阱

- **JBD2 大端**(big-endian,网络字节序)
- **ext4 小端**(little-endian, x86 默认)
- 解析 journal 内容必须用 `be32_to_cpu()`;内核源码 `fs/jbd2/recovery.c` 一开始就 `sb = journal_get_superblock()` 然后对每个 block header `cpu_to_be32()`/逆转换

## 对比/选型

| 模式 | data | metadata 落盘顺序 | 适用场景 |
|------|------|----------------|---------|
| `data=ordered`(ext4 默认) | 不 journal | data flush 先于 metadata commit | 通用服务器、桌面 |
| `data=writeback` | 不 journal,顺序不保证 | metadata journal 即可 | 大文件顺序写吞吐优先 |
| `data=journal` | journal + metadata 都 journal | 全部经 journal | 数据库、邮件服务器(强一致) |
| CoW 文件系统(Btrfs/ZFS) | N/A | 不需要 journal,Copy-on-write 自带原子性 | 但需自行管理 CoW 带来的碎片与空洞(见 [CoW与快照/](../CoW与快照/)) |

**性能代价**:
- 写吞吐:ordered 模式比无日志快 0-10% 损耗;journal 模式损耗 30-50%(data 写两遍)
- 写延迟:journal 模式尾部多一次 journal flush
- recovery 时间:与 journal 大小线性相关;默认 128MB journal 下最坏情况 ~几秒

## 环境准备

- **操作系统**:Linux 内核 2.6+ 内置 JBD2;**仅 ext3/ext4/ocfs2** 使用;其他 fs(xfs/btrfs)走自己的事务机制
- **语言版本**:GCC 9+ / Python 3.8+ / Go 1.18+
- **依赖**:本 demo 是 **JBD2 块格式模拟器**(in-memory simulator),不依赖真实 ext4 文件系统;用户态下无法直接构造/解析真实 journal
- **可选**(生产):`tune2fs -l /dev/sdaN` 看 journal_inum(通常是 inode 8);`debugfs -R "dump_extents <8>" /dev/sdaN` 看 journal 块号

## 运行方式

```bash
cd c      && gcc -O2 -Wall -Wextra -pedantic jbd2_sim.c -o jbd2_sim && ./jbd2_sim
cd python && python3 jbd2_sim.py
cd go     && go run jbd2_sim.go
```

## 关键代码片段

### Python(写 descriptor + data + commit 三件套)

```python
import struct

JBD2_MAGIC = 0xC03B3998
BLCKSIZE = 4096

def be32(n): return struct.pack(">I", n & 0xFFFFFFFF)

def make_descriptor_block(sequence, tags):
    # journal_header_t: magic / blocktype / sequence (big-endian)
    hdr = be32(JBD2_MAGIC) + be32(1) + be32(sequence)
    # tags: list of (blocknr, flags, uuid)
    body = b""
    for i, (bn, flags, uuid) in enumerate(tags):
        last = 0x8 if i == len(tags)-1 else 0
        body += be32(bn) + be32(flags | last) + uuid      # JBD2_FLAG_SAME_UUID
    # pad to blocksize
    return (hdr + body).ljust(BLCKSIZE, b'\x00')

def make_commit_block(sequence):
    return (be32(JBD2_MAGIC) + be32(2) + be32(sequence)).ljust(BLCKSIZE, b'\x00')

def write_transaction(dev, txn_id, data_blocks):
    tags = [(d["blocknr"], 0x2, b'\x00'*16) for d in data_blocks]  # SAME_UUID
    dev.append(make_descriptor_block(txn_id, tags))
    for d in data_blocks:
        dev.append(d["payload"].ljust(BLCKSIZE, b'\x00'))
    dev.append(make_commit_block(txn_id))
```

### Python(recovery:从 journal 重放 committed 事务)

```python
def replay(dev):
    committed, pending = [], []
    i = 0
    while i < len(dev):
        magic, btype, seq = parse_header(dev[i])
        if magic != JBD2_MAGIC: i += 1; continue
        if btype == 1:    # descriptor
            pending = [parse_tag(t) for t in parse_tags(dev[i])]
            pending_data = []
            i += 1
            # 收集所有 data blocks
            while i < len(dev) and parse_header(dev[i])[1] not in (1, 2):
                pending_data.append(dev[i])
                i += 1
            # 期望下一个是 commit block
            magic2, btype2, seq2 = parse_header(dev[i])
            if btype2 == 2 and seq2 == seq:
                committed.append((seq, pending, pending_data))
            else:
                pass   # 无 commit,丢弃
        elif btype == 2:    # orphan commit (no descriptor): 跳过
            i += 1
        else:
            i += 1
    return committed
```

## 性能与边界

- **JBD2 块大小**:`blocksize`(默认 1024-4096 字节;现代内核 4096)
- **Journal 大小**:默认 128 MB(`mke2fs -J size=128`);最大 2^32 块(约 16 TB @ 4KB 块)
- **单事务最大块数**:`s_max_transaction`(默认不限,实际受 journal 容量限制)
- **Commit block 大小**:`struct commit_header` = 32 字节,占用整块
- **Checkpoint 时间**:`/proc/sys/fs/jbd2/commit_interval`(默认 5 秒;写到 checkpoint 间隔,非强制)
- **性能**:JBD2 比 LFS 之类日志文件系统简单;recovery 仅线性扫描 journal

## 注意事项与常见坑

1. **`h_magic` 与数据块碰撞**:JBD2 检查 data block 前 4 字节是否等于 `0xC03B3998`;若等于,set `JBD2_FLAG_ESCAPE` 并把前 4 字节替换为 0;replay 时还原。**含义**:`/dev/sda` 任何含 magic 字节的块不会被误识别
2. **`blocknr` 大小**:`JBD2_FEATURE_INCOMPAT_64BIT` 启用时 `blocknr` 是 8B;否则 4B;tag 长度 8/12/24/38B 视 flag 位而定
3. **descriptor 与 data 顺序颠倒就 recovery 失败**:写入必须严格 [descriptor][data*][commit],顺序错则 journal 无效
4. **commit block 不写最终位置**:commit 只标记"事务已持久化",真正的 checkpoint(把 data block 写到目标 inode/位图等)异步发生;若 checkpoint 未完成就再次 crash,**replay 会重写相同 data block**(已 commit 的事务幂等,因为 blocknr 与内容都一致)
5. **`data=ordered` 不保护数据一致性**:崩溃后**元数据一致但文件内容可能含 0 字节空洞**(没刷掉的 data);`data=journal` 才会保护 data,但写吞吐下降
6. **`/proc/sys/fs/jbd2/commit_interval` 仅是 hint**:内核可能在 journal 满之前就 commit;不应依赖定时 commit 保证
7. **`tune2fs -O ^has_journal` 关闭 journal** → 文件系统变 ext2;现有 ext3/ext4 卸载后变 ext2 可挂载,但**不能 ext2→ext4 在线升级**(需 mkfs)
8. **journal 损坏的 fs 需 `fsck.ext4 -p`(自动修复)**:fsck 会读 journal,replay committed、丢弃 incomplete;journal 头损坏可能需 `e2fsck -b <backup_superblock>` 用备份 superblock
9. **OCFS2 也用 JBD2**:OCFS2(Oracle Cluster FS)是另一用户;OCFS2 与 ext4 共用 jbd2.ko,但参数不同
10. **不要把 journal 设备与主设备分离**:外部 journal(`mke2fs -J device=/dev/sdb1`)可减少 fs 主设备的随机写,但 jbd2 commit 与主设备写仍是串行依赖

## 参考资料(实际阅读过的权威来源)

- [ext4.wiki.kernel.org — Ext4_Disk_Layout(Journal/JBD2 段)](https://ext4.wiki.kernel.org/index.php?title=Ext4_Disk_Layout) — ext4 官方维护的磁盘布局文档,含 journal 完整字节布局、block 头、descriptor/data/commit/revocation 块格式、JBD2 特性 flag、checkpoint 流程
- [ext4.wiki.kernel.org — Ext4_Disk_Layout(历史 revision)](https://ext4.wiki.kernel.org/index.php?title=Ext4_Disk_Layout&diff=next&oldid=7831&printable=yes) — 同一文档 2013-11-13 版本,内容基本一致;含 `data=ordered/writeback/journal` 模式详解
- Linux kernel source `fs/jbd2/recovery.c`(未抓全,但概念与 wiki 一致):recovery 流程是顺序扫 journal,找 descriptor,收集后续 data,看 commit block 是否匹配;不匹配的丢弃
- Michael Kerrisk *man pages* (man7.org) — `mount(8)` / `ext4(5)` 提及 `data=` 选项的语义(本 README 第四节"模式"基于此)