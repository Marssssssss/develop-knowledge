# user namespace 与 UID/GID 映射

## 简介

user namespace 把 UID/GID 空间本身变成可隔离的资源:命名空间内的进程可以是 `uid 0`(拥有完整 capability 集),而它在宿主上的真实身份是某个普通用户。映射关系写在 `/proc/<pid>/uid_map` 与 `/proc/<pid>/gid_map` 里,格式是每行三个数字。

关键概念:

- **uid_map / gid_map**:一张「命名空间内 ID ↔ 父命名空间 ID」的区间对照表,每行 `ID-inside-ns ID-outside-ns size`。
- **只能写一次**:创建新命名空间后,map 文件**只允许成功写入一次**,再次写入 `EPERM`。
- **overflow ID(65534)**:任何未被映射的 ID 在 `getuid` / `stat` 等接口上都表现为 65534 而不是报错。
- **subuid / subgid**:`/etc/subuid`、`/etc/subgid` 给普通用户分配一段从属 ID 区间,由 setuid 的 `newuidmap` / `newgidmap` 代为写 map。
- **嵌套**:user namespace 可以层层嵌套(上限 32 层),每层再映射一次,形成「合成映射」。

历史背景:Linux 3.8 起无特权进程可以创建 user namespace。这是 rootless 容器、`podman`、Chromium/Flatpak 沙箱的共同底座 —— 在这个特性出现之前,"以非 root 身份跑一个看起来是 root 的进程"只能靠 setuid 二进制,无法做到内核级的 ID 隔离。

## 原理详解

1. **创建时映射是空的**。新建 user namespace 里没有任何映射,此时改变 UID/GID 的系统调用(`setuid` 等)一律失败;写入 map 后才能使用**已映射**的值。
2. **行格式**:

   ```
   ID-inside-ns   ID-outside-ns   size
   ```

   第二字段的含义**取决于是谁在读**:读它的进程与 `pid` 在同一命名空间时,它表示「`pid` 的**父**命名空间的起始 ID」;在不同命名空间时,它表示「**读它的进程**所在命名空间的起始 ID」。这解释了为什么同一份 `uid_map` 被不同命名空间的进程读出来可以不同。
3. **初始命名空间是伪映射**:`cat /proc/$$/uid_map` 得到 `0 0 4294967295`。这里 `4294967295 = (uid_t)-1` 被**故意排除**在外,因为 `-1` 在 `setreuid(2)` 等接口里有「无用户 ID」的特殊含义。
4. **写 map 的格式约束**:必须**以换行结尾**、必须**从偏移 0 写**(不能用 `lseek`/`pwrite`)、**总字节数必须小于一页**、**至少要写一行**;违反任一条 → `EINVAL`。
5. **行数上限**:Linux 4.14 之前是 **5 行**,自 **4.16** 起放宽到 **340 行**。注意这个上限与"一页"是**互相挤压**的:340 行只有在每行不超过约 12 字节时才塞得进 4096 字节,ID 位数一多,先撞的其实是页大小。
6. **区间不得重叠** → `EINVAL`。(4.8 之前还额外要求两列都按升序,该限制自 4.9 起取消。)
7. **无特权写入者的三重限制**:若写入者在父命名空间中**没有 `CAP_SETUID`**,则必须同时满足 ——(a) 只有**一行**;(b) 该行的 `size` 必须为 **1**,并把写入者在父 ns 的**有效 UID** 映射进去;(c) 写入者与**创建该命名空间的进程**有效 UID 相同。否则 `EPERM`。
8. **`gid_map` 需要先 `deny`**:无特权时必须先往 `/proc/<pid>/setgroups` 写 `"deny"`(永久禁用该命名空间的 `setgroups(2)`),之后才允许写 `gid_map`。
9. **映射到父 ns 的 UID 0 需要 `CAP_SETFCAP`**(Linux 5.12 引入):这是为了堵一个漏洞 —— 缺乏 `CAP_SETFCAP` 的 UID 0 进程,原本可以靠"自身映射 + 子进程带 `CAP_SETFCAP`"造出带命名空间文件能力的二进制文件。嵌套时这条规则意味着:要把内层 ns 的 root 映射成父 ns 的 root,写入者必须在父 ns 里有 `CAP_SETFCAP`(rootless 场景下用户在自己的 userns 内拥有完整能力集,故成立)。
10. **未被映射的 ID 一律变成 overflow**:`getuid`/`getgid`、`stat`、`waitid`、`/proc/<pid>/status`、UNIX 域套接字凭证、System V IPC 的 `IPC_STAT`、`SIGCHLD` 的 `si_uid` 等都会把未映射 ID 折算成 `overflowuid`/`overflowgid`(默认 65534)。**唯一的例外**是读 `uid_map` 本身:第二字段未映射时显示 `4294967295`。
11. **`CAP_FOWNER` 类能力依赖映射**:在命名空间内绕过文件权限,要求文件的 UID **与** GID 都有效映射(`CAP_FOWNER` 只需 UID);文件的 UID/GID 未映射时,set-user-ID / set-group-ID 位会被**静默忽略**,语义等同以 `MS_NOSUID` 挂载。
12. **嵌套上限 32 层**(Linux 3.11 起),超出时 `unshare(2)`/`clone(2)` 返回 `EUSERS`。
13. **一次调用同时建多种 namespace**:若在**同一次** `clone(2)`/`unshare(2)` 里同时给 `CLONE_NEWUSER` 和其它 `CLONE_NEW*`,内核保证**先建 user namespace**,于是无特权调用者也能拿到其余命名空间的特权。

### 合成映射示意(官方 rootless 数字)

```
/etc/subuid:  testuser:231072:65536        → 可用宿主区间 [231072, 296607]

ns1 的 uid_map:  0 231072 65536
  ns1 内 0     -> 宿主 231072
  ns1 内 65535 -> 宿主 296607
  ns1 内 65536 -> 65534(overflow)

嵌套两层:
ns2 的 uid_map:  0 0 1000                  (ns2 内 -> ns1 内)
  ns2 内 0   -> ns1 内 0   -> 宿主 231072
  ns2 内 999 -> ns1 内 999 -> 宿主 232071
  ns2 内 1000 -> 本层越界 -> 65534
```

## 对比 / 选型

| 方式 | 写入者 | 能映射到的范围 | 典型用途 |
| --- | --- | --- | --- |
| 无特权单行 | 任意用户 | 只映射自身 euid,长度 1 | 简易沙箱、`unshare -U` |
| `newuidmap` 助手 | setuid root 的助手进程 | `/etc/subuid` 中分配给该用户的区间 | rootless 容器(多 UID 需要) |
| 父 ns 中带 `CAP_SETUID` | 特权进程 | 父 ns 中任意 UID/GID | 系统级容器运行时 |
| userns-remap(Docker) | root 守护进程 | 守护进程自带用户,容器内 root ≠ 宿主 root | 加固版 rootful Docker |

`newuidmap` 存在的意义:内核只允许"有权写"的进程写多行 map,而普通用户没有这个权力。于是引入一个 **setuid root 的助手**:它替用户写,但严格校验 (a) 调用者是目标进程的属主,(b) 请求的每个宿主 UID 都落在 `/etc/subuid` 给该用户的区间内。`newuidmap(1)` 原文:"Note that the root user is not exempted from the requirement for a valid `/etc/subuid` entry." 它还支持 `fd:N` 形式(用已打开的 `/proc/<pid>` 目录 fd)来规避 **TOCTTOU** —— pid 被回收后立刻复用会让「先检查属主、后打开 uid_map」的窗口出现风险。

## 环境准备

- 操作系统:任意(纯逻辑模型,不需要内核支持 userns)
- 语言:Python 3.10+、Go 1.21+、C11 编译器

## 运行方式

### Python

```bash
python3 uid_map_model.py
```

### Go

```bash
cd go && go run .
```

### C

```bash
gcc -O2 -Wall -Wextra -pedantic -std=c11 uid_map.c -o uid_map && ./uid_map
```

## 关键代码片段

```python
def write(self, text, *, offset=0, writer_caps_in_parent=False,
          writer_euid_in_parent=1000, creator_euid_in_parent=1000,
          has_cap_setfcap=False):
    if self.written:
        raise MapError("EPERM", "uid_map/gid_map 只能写一次")            # 规则 4
    if offset != 0 or not text.endswith("\n"):
        raise MapError("EINVAL", "必须从偏移 0 写,且以换行结尾")          # 规则 4
    if len(text.encode()) > PAGE_SIZE:
        raise MapError("EINVAL", "写入必须小于一页")                      # 规则 4
    limit = MAX_LINES_MODERN if self.kernel_minor >= 16 else MAX_LINES_LEGACY
    ...
    if not writer_caps_in_parent:                                       # 规则 7
        if len(entries) != 1 or entries[0][2] != 1:
            raise MapError("EPERM", "无 CAP_SETUID 时只允许一行、长度 1")
        if entries[0][1] != writer_euid_in_parent:
            raise MapError("EPERM", "只能映射自身的有效 UID")
        if self.is_gid and not self.setgroups_denied:
            raise MapError("EPERM", "写 gid_map 前必须先 deny setgroups")  # 规则 8
    if any(o == 0 for _i, o, _s in entries) and not has_cap_setfcap:
        raise MapError("EPERM", "映射到父 ns 的 UID 0 需要 CAP_SETFCAP")   # 规则 9
```

## 性能与边界

- **映射查找是线性扫描**:`uid_map` 最多 340 行,内核做区间查找,单次 `stat` 的额外开销是 O(行数) 级别的查表,而非哈希。
- **一页 = 4096 字节的硬墙**:340 行的上限只有在每行 ≤ 约 12 字节时才可达;实测紧凑写法的 340 行是 **3184 字节**,而把 ID 拉长到 6 位数后 340 行直接 **4310 字节 > 4096**,先撞页大小。
- **嵌套上限 32 层**(`EUSERS`)。
- **overflow 是"看起来正常"的边界**:未映射 ID 不会报错,只会静默变成 65534。这在生产上表现为"文件属主显示为 nobody"这类现象,容易误判为权限配置问题。
- **映射不影响已有文件**:`/etc/subuid` 改了不会让镜像里已固化的属主自动跟着变;容器的 rootfs 必须有与映射一致的所有权(这也是 `podman unshare chown` 存在的原因)。

## 注意事项与常见坑

1. **在写 map 之前就 `setuid`**:会失败 —— 手册明确,map 未写入前改变 UID/GID 的系统调用一律失败,写入后也只能用已映射的值。
2. **先 `setuid` 再写 map 的顺序陷阱**:父进程给子进程写 map 时必须**确认子进程已经创建但还没 `execve`**;手册的示例程序用管道做同步,否则 `execve` 会按常规重算能力,子进程在映射完成前就丢掉全部能力。
3. **写 `gid_map` 忘了 `deny`**:表现为 `EPERM`,而不是"权限不足"这种直觉解释。`setgroups("deny")` 是**永久**的,写下去就不能回头。
4. **以为"容器里 uid 0 = 宿主 root"**:只有在没有 user namespace(或做了恒等映射)时成立;rootless / userns-remap 下容器 root 对应的是宿主非特权 UID。
5. **setuid 程序在命名空间里静默失效**:二进制属主 UID 未映射时,set-user-ID 位被忽略,和 `nosuid` 挂载一样。排查"为什么容器里 sudo 不生效"时这是第一嫌疑。
6. **`uid_map` 的第二个字段随读者变化**:写自动化校验脚本时不要假定它总是"父命名空间的 ID"。
7. **pid 复用竞态**:用 pid 去 `/proc/<pid>/uid_map` 时存在 TOCTTOU;`newuidmap` 因此支持 `fd:N` 形式,自己写工具时也应先 `open` `/proc/<pid>` 目录再在 fd 上操作。
8. **`/etc/subuid` 只影响新创建的容器**:改完必须重启容器(或 `podman system migrate` 之类)才会重新生成映射。

## 参考资料(实际阅读过的权威来源)

- [man7.org — user_namespaces(7)](https://man7.org/linux/man-pages/man7/user_namespaces.7.html) — uid_map/gid_map 行格式与第二字段的两种解释、只能写一次、换行/偏移/页大小/行数(5→340)与重叠规则、无特权三重限制、`setgroups` deny、`CAP_SETFCAP`(5.12)、overflowuid 65534 的完整场景清单与 `uid_map` 例外、嵌套 32 层 `EUSERS`、`CLONE_NEWUSER` 与其它 `CLONE_NEW*` 的创建顺序保证、setuid 位静默忽略
- [man7.org — newuidmap(1)](https://man7.org/linux/man-pages/man1/newuidmap.1.html) — 参数三元组语义、调用者必须是目标进程属主、每个宿主 UID 必须落在 `/etc/subuid` 允许区间、root 也不例外、`fd:N` 规避 TOCTTOU、"only once for a given process"
- [Docker Docs — Run the Docker daemon as a non-root user (Rootless mode) 前置条件](https://docs.docker.com/engine/security/rootless/) — `/etc/subuid`、`/etc/subgid` 至少 65,536 个从属 ID 的原文与 `testuser:231072:65536` 示例、`newuidmap`/`newgidmap` 是唯一保留的 setuid 二进制
