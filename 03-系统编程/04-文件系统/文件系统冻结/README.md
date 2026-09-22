# 文件系统冻结与一致性快照（`FSFREEZE` / LVM snapshot）

想给一个正在运行的文件系统拍一份"干净"的块设备快照，正确姿势是 **先冻结、再拍、再解冻**：

```bash
fsfreeze --freeze   /mnt/data     # 或 xfs_freeze -f
lvcreate --snapshot --size 10G --name snap /dev/vg/data
fsfreeze --unfreeze /mnt/data     # xfs_freeze -u
```

直接 `lvcreate` 拍出来的镜像等价于**掉电后的磁盘**——数据块是某一时刻的、元数据是另一时刻的，挂载时必须走日志重放（ext4 的 recovery / XFS 的 log recovery），而且重放只能保证元数据一致，应用层的多文件原子性照样丢失。

## 一、`freeze_super` 是**分级**的，不是一步到位

`sb->s_writers.frozen` 会依次经过（`fs/super.c` 注释块原文）：

| 级别 | 挡什么 | 备注 |
| --- | --- | --- |
| `SB_UNFROZEN`(0) | — | 正常 |
| `SB_FREEZE_WRITE`(1) | **新写** | 页错误**仍允许**；等所有写完成后升到下一级 |
| `SB_FREEZE_PAGEFAULT`(2) | + 页错误 | 内部 fs 线程仍可改（但不应再弄脏）；等页错误完成后 **sync 一次** |
| `SB_FREEZE_FS`(3) | + 内部写入源 | 挡新事务；调 `->freeze_fs()` |
| `SB_FREEZE_COMPLETE`(4) | 全挡 | 主要给文件系统自检"我没在冻结后改东西" |

只有前三级各有一把 percpu rwsem（`SB_FREEZE_LEVELS == 3`），名字依次是：

```c
static char *sb_writers_name[SB_FREEZE_LEVELS] = {
        "sb_writers", "sb_pagefaults", "sb_internal",
};
```

`SB_FREEZE_COMPLETE` 没有对应的 rwsem——它只是个辅助态。**为什么要分级**：一次性把所有写都挡住会让正在进行的写长时间持锁；分级让它能"边退边挡"，而且 sync 恰好安排在页错误被挡之后，保证 sync 期间不会再产生新的脏页。

两条容易写反的方向性断言：

- `SB_FREEZE_WRITE` 时**页错误仍然允许**（第一级只挡 `write(2)` 那类）
- `SB_FREEZE_PAGEFAULT` 时**内部 fs 线程仍可动**，到 `SB_FREEZE_FS` 才挡

## 二、谁能冻：`may_freeze` 与持有者计数

`freeze_super(sb, who, freeze_owner)` 的 `who` 是 `enum freeze_holder` 的位组合：

```
FREEZE_HOLDER_KERNEL = 1   FREEZE_HOLDER_USERSPACE = 2
FREEZE_MAY_NEST      = 4   FREEZE_EXCL             = 8
```

- `KERNEL` 与 `USERSPACE` **是两套独立计数**（`freeze_kcount` / `freeze_ucount`）。内核冻一次、用户冻一次，`thaw` 也要各来一次，文件系统才真正解冻。
- 不带 `MAY_NEST` 时，同一类持有者**只能冻一次**；再冻返回 `EBUSY`。块设备经由多个设备冻同一个 fs 时才用 `MAY_NEST`，此时"文件系统保持冻结，直到所有设备都解冻"。
- `FREEZE_EXCL` 的额外约束（最容易漏）：
  - 只能配 `KERNEL`，**不能**配 `USERSPACE`，也不能配 `MAY_NEST`
  - 必须给 `freeze_owner`
  - 已经有 owner 就 `EBUSY`；**只有"已经被冻过多次"时才把 owner 记成调用者**

## 三、谁能解冻：`may_unfreeze` 是**另一套**规则

别把 `may_freeze` 的逻辑套过来：

- `KERNEL` 分支里 `kcount == 1 && freeze_owner != NULL` → **拒绝**，防止别人偷走属于 EXCL 持有者的那一份引用
- `USERSPACE` 只看 `ucount > 0`
- `EXCL` 分支：owner 必须匹配；但**总计数 > 1 时会先把 `freeze_owner` 清成 NULL 再返回成功**——也就是放弃独占声明，只减自己那份

`thaw_super_locked` 里 `if (freeze_dec(sb, who))` 非零就**提前返回**，所以：

- 还有别的持有者时，`->unfreeze_fs()` **不会**被调用，`freeze_owner` 也**不会**被清空
- 只有最后一次 thaw 才真正 `frozen = SB_UNFROZEN` + `freeze_owner = NULL`

## 四、两个边界情况

**只读文件系统**：`freeze_super` 直接标 `SB_FREEZE_COMPLETE`，**不分级、不 sync、不调 `->freeze_fs()`**（本来就没脏数据）。

```c
if (sb_rdonly(sb)) {
        /* Nothing to do really... */
        WARN_ON_ONCE(freeze_inc(sb, who) > 1);
        sb->s_writers.freeze_owner = freeze_owner;
        sb->s_writers.frozen = SB_FREEZE_COMPLETE;
        ...
}
```

**半冻结态**：如果另一个 freezer 正在推进，`frozen` 停在 `SB_FREEZE_*` 中间值，此时新的 `freeze_super` 会走 `wait_for_partially_frozen()`——睡在 `s_writers.frozen` 上等它落到 `SB_UNFROZEN` 或 `SB_FREEZE_COMPLETE`，然后 `goto retry` 重来。

## 五、快照一致性

本 demo 用一个极简 `Snapshot` 把结论钉住：

| 拍快照的时机 | consistent | 挂载需 replay |
| --- | --- | --- |
| `SB_UNFROZEN` | 否 | **是** |
| `SB_FREEZE_COMPLETE` | 是 | 否 |
| 只读文件系统 | 是 | 否 |

## 文件说明

- `python/freeze.py` —— 状态常量、`may_freeze` / `may_unfreeze`、`freeze_inc/dec`、`freeze_super` 分级推进、`thaw_super`、`wait_for_partially_frozen`、`Snapshot`
- `python/selfcheck_freeze.py` —— 76 条断言（实跑全绿）
- `python/main.py` —— 演示入口
- `go/freeze.go` —— Go 侧镜像

## 参考资料（实际读过）

- `fs/super.c`（`freeze_super` 的分级注释块、`may_freeze` / `may_unfreeze` / `freeze_inc` / `freeze_dec`、`thaw_super_locked`、`wait_for_partially_frozen`、`sb_writers_name[]`）
  <https://github.com/torvalds/linux/blob/master/fs/super.c>
- `fsfreeze(8)`（util-linux 工具的 `--freeze` / `--unfreeze` 语义）
  <https://man7.org/linux/man-pages/man8/fsfreeze.8.html>

> 口径说明：LVM/设备级快照本身的 COW 机制（`dm-snapshot` 的 exception store）本轮未建模，只表达"冻结与否决定快照是否一致"这一层。`xfs_freeze` 与 `fsfreeze` 的差异（前者是 XFS 专用 ioctl 包装，后者走 `FIFREEZE`/`FITHAW`）只在 README 里提及，未取源码核对。
