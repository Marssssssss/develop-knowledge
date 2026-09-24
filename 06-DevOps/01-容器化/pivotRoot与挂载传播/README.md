# pivot_root 与挂载传播：容器是怎么"换根"的

> 容器启动到最后一脚是把自己关进新的根文件系统。`pivot_root(2)` 只有一页 man，却有**六条限制**和一堆反直觉的传播语义；runc 为了满足它们、同时不弄脏宿主，写出了一段值得逐行读的代码。本文把 man 页的限制表与 runc 的实现都做成可执行模型。

权威来源（本 demo 实际读过）：

- **man7 `pivot_root(2)`**（20967 B）：DESCRIPTION 的六条限制、ERRORS 的 `EBUSY`/`EINVAL`/`ENOTDIR`/`EPERM` 分派、NOTES 里的 `pivot_root(".", ".")` 免临时目录写法
- **`opencontainers/runc` `libcontainer/rootfs_linux.go`**（52735 B）：`prepareRoot` / `rootfsParentMountPropagation` / `pivotRoot` / `msMoveRoot` / `chroot` / `setReadonly` 及其注释

```bash
python python/main.py              # 演示入口
python python/selfcheck_pivot.py   # 43 项断言
go run go/pivot_model.go go/rootfs_move.go go/main.go
```

## 1. pivot_root 到底做了什么

> *pivot_root() moves the root mount to the directory put_old and makes new_root the new root mount.*

三个关键副作用（man 页原文）：

- 同 mount namespace 里**所有** root/cwd 指向旧根的进程/线程，会被一起改到 new_root——这是为了让内核线程不再占着旧根，从而能 umount 它；
- **调用者自己的 cwd 不会变**（除非它就在旧根上），所以后面必须自己 `chdir("/")`；
- 需要 `CAP_SYS_ADMIN`（在拥有该 mount namespace 的 user namespace 里）。

## 2. 六条限制与 errno 分派

| 限制 | 违反时的 errno |
| --- | --- |
| `new_root` 与 `put_old` 必须是目录 | `ENOTDIR` |
| 二者不能和当前根在同一个挂载上（含 `new_root == "/"` 这种病态情况） | `EBUSY` |
| `put_old` 必须在 `new_root` 处或其下（给 `put_old` 加若干 `/..` 后缀能得到 `new_root`） | `EINVAL` |
| `new_root` 必须是挂载点，且不能是 `/` | `EINVAL` |
| `new_root` 的**父挂载**、当前根的**父挂载**都不能是 `MS_SHARED`；`put_old` 若是挂载点也不能是 `MS_SHARED` | `EINVAL` |
| 当前根目录必须是个挂载点（否则说明之前 chroot 过） | `EINVAL` |

`EPERM` 留给缺 `CAP_SYS_ADMIN` 的情况。

第三条有个例外：**`new_root` 和 `put_old` 可以是同一个目录**。man 页专门给了 `pivot_root(".", ".")` 的写法——runc 正是靠它免掉了「在 rootfs 里建临时目录再删掉」这一步。

## 3. shared subtree：四种传播类型

| 类型 | 行为 |
| --- | --- |
| `MS_SHARED` | 同一**对等组（peer group）**内的挂载互相传播；一处的 mount/umount 会在所有对等体上重现 |
| `MS_SLAVE` | 只**单向接收**来自 master 对等组的事件，自己的事件不回传 |
| `MS_PRIVATE` | 完全不传播（默认） |
| `MS_UNBINDABLE` | 不传播，**且不能作为 bind 的源**（`EINVAL`） |

`MS_REC` 不是一种类型，而是"递归"修饰符：`mount --make-rslave /` 会把整棵子树一起改掉。对一个本来是 private 的挂载做 `rslave`，Linux 给的是**无主 slave**——行为等同 private，但类型确实是 slave。

## 4. runc 走了哪几步

`prepareRoot()`（切根之前）：

1. `mount("", "/", "", MS_SLAVE|MS_REC, "")` —— 把容器 mount namespace 里的 `/` 变成 **rslave**。这一步是必须的：宿主 `/` 常常是 shared，容器内的 mount/unmount 会顺着传播回宿主（注释里写得很直白：*we don't want that*）。
2. `rootfsParentMountPropagation(rootfs)` —— 确保 rootfs 的**父挂载**不是 shared。源码注释给了两个理由：`pivot_root()` 在父挂载是 shared 时会失败；bind rootfs 时如果父挂载是 (r)shared，新挂载会**泄漏**到父 namespace。
   - 用的 flag 由 `rootfsParentMountPropagationFlags()` 决定：配置带 `MS_SLAVE` 就用 `MS_SLAVE`，否则 `MS_PRIVATE`；
   - 实现是**沿父目录上溯**：`EINVAL` 意味着"这层还不是挂载点"，就往上一级，直到 `/`。
3. `mount(rootfs, rootfs, "bind", MS_BIND|MS_REC, "")` —— 把 rootfs **bind 到自己身上**。这正是 man 页说的"把非挂载点变成挂载点"的标准手法，同时满足第 4 条限制。

`pivotRoot(root *os.File)`：

```
oldroot = open("/", O_PATH)          // 先抓住旧根的 fd
fchdir(rootfd)                        // 切到新根
pivot_root(".", ".")                  // 旧根被堆到 / 上
fchdir(oldroot)                       // 不依赖 /proc/self/cwd 的内核行为
mount("", ".", "", MS_SLAVE|MS_REC)   // 旧根改 rslave，避免卸载传播出去
umount2(".", MNT_DETACH)              // MNT_DETACH 才能卸掉 /proc/self/cwd
chdir("/")
```

两个注释里的细节很值钱：

- 为什么用 `rslave` 而不是 `rprivate`：rprivate 会引发竞态——我们还持有某个挂载的引用时，宿主 namespace 的进程以为那里没有挂载并开始操作（点名 devicemapper）。
- 为什么要 `fchdir(oldroot)` 而不是信 `/proc/self/cwd`：内核没保证 pivot_root 之后 cwd 是什么。

## 5. `--no-pivot` 的退路：msMoveRoot

用 `chroot` 代替 `pivot_root` 时，**宿主的 procfs/sysfs 挂载在 mount namespace 里仍然可达**。源码注释说得很清楚：内核只在"存在一个**完整**挂载（文件系统根被挂载）且没有其他锁定挂载覆盖其子树"时才允许再挂 procfs——所以 runc 会先把宿主的完整 procfs/sysfs 全部处理掉：

- 筛选条件（`mountinfo` 过滤）：`info.Root == "/"`（完整挂载）**且** fstype 是 `proc` 或 `sysfs` **且** 挂载点不在 rootfs 之下；
- 先 `MS_SLAVE|MS_REC` 切断传播，再 `MNT_DETACH` 卸载；
- 卸载失败且是 `EINVAL`/`EPERM`（典型是 rootless）→ **用 tmpfs 盖住**，而不是放弃；
- 最后 `mount(rootfs, "/", "", MS_MOVE)` + `chroot(".")` + `chdir("/")`。

## 6. 与既有 demo 的分工

- `Namespace隔离`：mount namespace 的建立（**隔离**）；
- `OverlayFS-联合挂载`：rootfs 内容从哪来（**内容**）；
- `runc创建流程与同步协议`：切根发生在 `prepareRootfs` 的哪一步、前后有哪些同步（**流程**）；
- 本 demo：切根这一脚本身（**机制**）。

## 7. 自检覆盖

43 项断言，分四组：六条限制的 errno 分派（非挂载点、`new_root == /`、put_old 越界、非目录、shared 父挂载、shared 的 put_old）与成功后的树变换；四种传播类型（shared 扩散到对等组与 slave、slave 单向、private/unbindable 不扩散、unbindable 不能 bind、`MS_REC` 递归、无主 slave）；runc 路径（prepareRoot 的 rslave、父挂载上溯、bind 自身成挂载点、flag 选择、`pivot_root(".", ".")` 的堆叠与后续 rslave→MNT_DETACH→chdir、msMoveRoot 的三条筛选条件与 rootless 用 tmpfs 盖）；兜底（上溯到 `/`、对非挂载点设传播报错、bind 自身转换、shared 根上只读 remount）。
