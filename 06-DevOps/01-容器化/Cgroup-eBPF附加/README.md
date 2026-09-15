# cgroup v2 与 eBPF 程序附加(BPF token 委派)

## 简介

cgroup v2 与 eBPF 的交集是"**按 cgroup 生效的策略**":把 BPF 程序挂到某个 cgroup 上,该 cgroup 及其子层级里的进程就会受到这段程序的影响 —— 设备访问、套接字创建、`connect`/`sendmsg`、`sysctl` 读写都可以被拦截或改写。这块能力在容器里通常由运行时(containerd/CRI-O)代为安装。

关键概念:

- **`cgroup.subtree_control`**:决定"本 cgroup 把哪些控制器分配给**子**节点"的开关,是 cgroup v2 把资源分配与进程组织解耦的核心机制。
- **域控制器(domain controller)**:`cpu`/`cpuset`/`io`/`memory`/`pids` 等按域(exclusive domain)分配的控制器,受"无内部进程"约束。
- **`BPF_PROG_TYPE_CGROUP_*`**:只能附加到 cgroup 上的程序类型族,`cgroup_skb` / `cgroup_sock` / `cgroup_sock_addr` / `cgroup_device` / `cgroup_sysctl` / `cgroup_sockopt` / `sockops`。
- **attach type**:同一个 cgroup fd 上可以挂多个不同 attach type 的程序;每个 attach type 维护一条**有序程序链**。
- **BPF token**:把"使用 bpf() 系统调用"的能力从 init userns 委派进某个用户命名空间的机制,由特权进程(如容器运行时)通过挂载 BPF FS 时指定委派选项来发放。

历史背景:cgroup v1 有多套层级、控制器可随意组合,导致同一进程在不同层级里的层级结构不一致;v2 收敛为**单一层级**(`mount -t cgroup2 none $MNT`)。eBPF 早期加载程序需要 `CAP_SYS_ADMIN`,粒度过粗,**Linux 5.8** 起拆出 `CAP_BPF`,而 **6.9** 引入 BPF token,使非特权容器在**被委派**的前提下也能用 bpf()。

## 原理详解

### A. cgroup v2 层级的三条硬规则

1. **单一层级**。所有支持 v2 且未绑定到 v1 层级的控制器会自动绑定到 v2 层级并出现在根 cgroup 下。
2. **`cgroup.controllers` 是只读的可用清单,`cgroup.subtree_control` 是可读写的已启用开关**。启用只能针对 `cgroup.controllers` 里列出的名字,写法是空格分隔的 `+name` / `-name`;一次写多个控制器**要么全部成功,要么全部失败**;同一控制器出现多次时**最后一个生效**。
3. **自顶向下约束**:资源自顶向下分配,子节点只能启用**父节点已启用**的控制器;反过来,只要还有子节点在启用某个控制器,父节点就**不能禁用**它。
4. **无内部进程(no internal process)**:非根 cgroup 只有在**自身不含任何进程**时,才能启用域控制器。原因是"进程只应出现在叶子节点",否则父 cgroup 的内部进程会与子 cgroup 竞争同一份域资源。根 cgroup **豁免**(它本来就含进程与无法归属其它 cgroup 的匿名消耗)。实践含义:要给某 cgroup 施加限制,必须**先建子 cgroup、把进程迁下去**,再启用控制器。
5. **接口文件归属父节点**:在 C 上启用 `cpu`,会在 C 的**子**节点里创建 `cpu.` 前缀的接口文件;从 C 禁用,则子节点里的 `cpu.` 文件消失。
6. **委托(delegation)**:把目录连同 `cgroup.procs`、`cgroup.threads`、`cgroup.subtree_control` 的写权限一起授予普通用户,即可委托;`nsdelegate` 挂载选项则把 cgroup namespace 当作委托边界。关键技术细节是**迁移封闭性**:写入 `cgroup.procs` 要求对**源与目标 cgroup 的公共祖先**的 `cgroup.procs` 也有写权限 —— 于是被委托者能在自己的子树内自由搬进程,但**无法把外部进程拉进来或把内部进程推出去**。
7. **`cgroup.procs` 与 `cgroup.threads`**:单次 `write(2)` 只能迁移一个进程;写某个线程的 tid 会迁移**该进程的所有线程**;fork 出来的子进程留在**父进程当时所属**的 cgroup;僵尸进程不出现在 `cgroup.procs` 中。`/proc/<pid>/cgroup` 在 v2 下格式恒为 `0::$PATH`。

### B. BPF 程序的类型与附加

8. **`SEC()` 名决定程序类型与 attach 类型**。官方 program_types 表把 ELF section 名映射到 (`BPF_PROG_TYPE_*`, attach type),例如 `cgroup_skb/ingress` → `BPF_PROG_TYPE_CGROUP_SKB` + `BPF_CGROUP_INET_INGRESS`,`cgroup/connect4` → `BPF_PROG_TYPE_CGROUP_SOCK_ADDR` + `BPF_CGROUP_INET4_CONNECT`,`cgroup/dev` → `BPF_PROG_TYPE_CGROUP_DEVICE` + `BPF_CGROUP_DEVICE`,`cgroup/sysctl` → `BPF_PROG_TYPE_CGROUP_SYSCTL` + `BPF_CGROUP_SYSCTL`。注意 `cgroup/skb` 与 `cgroup_skb/ingress` 指向同一个 attach 类型。
9. **只有 cgroup 类程序能挂到 cgroup 上**:`xdp` / `tc` / `classifier` / `socket` / `kprobe` 之类的 section 名与 cgroup 附加点不兼容。
10. **同一 attach type 上是一条有序链**:多个程序按挂载顺序执行,可以单独 `detach`;基于 link(`BPF_LINK_CREATE`)创建的对象在 fd 关闭时随之销毁,不依赖显式 detach。

### C. BPF token 的委派语义

11. **委派集合有四项**:`delegate_cmds`(允许的 bpf() 子命令)、`delegate_maps`(允许创建的 map 类型)、`delegate_progs`(允许加载的程序类型)、`delegate_attachs`(允许的 attach 类型)。token 从**带委派挂载选项的 BPF FS** 派生。
12. **能力检查的命名空间换了**。官方原文:"When created, BPF token is 'associated' with the owning user namespace of BPF FS instance (super block) that it was derived from, and subsequent BPF operations performed with BPF token would be performing capabilities checks (i.e., `CAP_BPF`, `CAP_PERFMON`, `CAP_NET_ADMIN`, `CAP_SYS_ADMIN`) within that user namespace." 没有 token 时这些能力必须在 **init 用户命名空间**里具备,这基本等于"bpf() 与用户命名空间不兼容"。
13. **token 本身不足以授权**:进程还必须在该用户命名空间内具备**对应的能力组合** —— 加载 TC/XDP 类需要 `CAP_BPF` + `CAP_NET_ADMIN`;加载 tracing 类(kprobe/raw_tracepoint)需要 `CAP_BPF` + `CAP_PERFMON`。内核提交信息明确:"Such setup means that BPF token in itself is not sufficient to grant BPF functionality."
14. **token 与创建它的用户命名空间绑定**:"the BPF Token File Descriptor is bound to the User Namespace that creates it and can't be used on the outside."
15. **`BPF_TOKEN_CREATE` 自身也需要能力**:要求在该 BPF FS 所属用户命名空间内有 `ns_capable(CAP_BPF)`。

### 附:一条 cgroup 程序在内存里的样子

```
struct bpf_insn {          /* 共 8 字节 */
    __u8  code;            /* opcode,LdW/DW/MOV/CALL/EXIT 都在这里 */
    __u8  dst_reg:4;       /* 低半字节 */
    __u8  src_reg:4;       /* 高半字节 —— LD_MAP_FD 时置 BPF_PSEUDO_MAP_FD */
    __s16 off;             /* 跳转偏移 */
    __s32 imm;             /* 立即数;宽指令的第二条承载高 32 位 */
};

LD_MAP_FD r1, map_fd   =>  [0x18][0x11][0x0000][fd]      ← src_reg=1
                           [0x00][0x00][0x0000][0x0000]  ← 宽指令第二条必须全 0
MOV64     r2, r1       =>  [0xb7][0x21][0x0000][0]
CALL      helper       =>  [0x85][0x00][0x0000][helper_id]
EXIT                   =>  [0x95][0x00][0x0000][0]
```

## 对比 / 选型

| 维度 | 挂到 cgroup 的 BPF 程序 | 传统方式的对应物 |
| --- | --- | --- |
| 设备访问控制 | `cgroup/dev` + `BPF_CGROUP_DEVICE` | cgroup v1 的 `devices` 控制器 |
| 套接字创建拦截 | `cgroup/sock_create` + `BPF_CGROUP_INET_SOCK_CREATE` | seccomp 过滤 `socket(2)` |
| `connect`/`sendmsg` 改写 | `cgroup/connect4`、`cgroup/sendmsg4` | iptables + 透明代理 |
| sysctl 读写拦截 | `cgroup/sysctl` + `BPF_CGROUP_SYSCTL` | 无直接等价物 |
| 程序生命周期 | link 对象;fd 关闭即分离 | — |

## 环境准备

- 操作系统:任意(纯逻辑模型,不加载真实 eBPF 程序)
- 语言:Python 3.10+、Go 1.21+、C11 编译器
- 依赖:无第三方库

## 运行方式

### Python

```bash
python3 cgroup_check.py     # 自检(模型本体在 cgroup_bpf.py)
```

### Go

```bash
cd go && go run .
```

### C

```bash
gcc -O2 -Wall -Wextra -pedantic -std=c11 bpf_insn.c -o bpf_insn && ./bpf_insn
```

## 关键代码片段

```python
def write_subtree_control(cg: Cgroup, ops: str, is_root: bool = False) -> list[str]:
    """模拟 `echo '+cpu +memory -io' > cgroup.subtree_control`。"""
    tokens = ops.split()
    final: dict[str, bool] = {}
    for t in tokens:                      # H3:同名控制器只保留最后一次
        final[t[1:]] = t[0] == "+"
    for name, enable in final.items():    # H1 + H5:先全部校验,再统一落地(H2 全有或全无)
        if enable and name not in cg.controllers:
            raise BpfError("EINVAL", f"{name} 不在 cgroup.controllers 中")
        if enable and not is_root and cg.procs and name in domain_ctrls:
            raise BpfError("EBUSY", "含进程时不能启用域控制器")
    ...
    for child in cg.children:             # H6:在父上启用会为子创建接口文件
        child.controllers = sorted(set(child.controllers) | set(result))
    return result
```

```c
/* LD_MAP_FD 必须是"宽指令":src_reg 标记伪 map fd,第二条全 0 */
prog[0] = insn(BPF_LD | BPF_DW | BPF_IMM, BPF_REG_1, BPF_PSEUDO_MAP_FD, 0, map_fd);
prog[1] = insn(0, 0, 0, 0, 0);
```

## 性能与边界

- **cgroup v2 的层级约束是"结构性"的**:控制器接口文件属于父节点而非自身,所以"在同一个 cgroup 上既跑进程又限资源"在 v2 里是不合法的设计;必须多建一层。
- **委托没有深度/数量上限**(官方:"currently no limit on the number of delegated sub-hierarchies"),但父节点的资源限制始终是硬上限 —— 委托子层级里怎么分配都无法突破。
- **`cgroup.procs` 的单次写入只能迁移一个进程**,批量迁移需要循环;迁移还会失败在"公共祖先进程文件无写权限"这一条上。
- **进程迁移代价高且有状态资源不跟随**:官方建议 "Organize Once and Control" —— 启动时按系统结构一次性组织好,之后只调控制器参数,避免为加限制而频繁搬进程。
- **BPF 程序的执行点在内核热路径上**:每次 `connect` / 每次收发包都会过一遍挂在该 cgroup 上的程序链;链越长,单价越高。
- **懒迁移的坑**:v2 与 v1 之间移动控制器时,由于 per-cgroup 控制器状态是**异步销毁**的,迁移不会立即生效;官方明确建议不要在运行时动态迁移,应在启动后、使用前就定好。

## 注意事项与常见坑

1. **忘了"无内部进程"这一条**:在含进程的 cgroup 上 `echo +memory > cgroup.subtree_control` 会失败。正确顺序是"建子节点 → 把进程迁进子节点 → 再启用控制器"。
2. **一次写多个控制器时以为会部分成功**:不会 —— 全有或全无;调试时先单独写一个看错误码。
3. **重复名字取最后一个**:`+cpu -cpu` 的净效果是**不启用**,而不是先启用再禁用后留下副作用。
4. **把 cgroup 程序当成 seccomp 用**:两者层次不同 —— seccomp 在内核边界拒绝系统调用,cgroup BPF 是在 cgroup 语义点上做决策;`cgroup/dev` 管的是设备节点访问,不是系统调用号。
5. **认为有 BPF token 就能为所欲为**:token 只是"把能力检查搬到被委派的 userns",进程**仍须**在该 userns 里具备对应能力组合;而且 token **不能跨 userns 使用**。
6. **忘了委派集合要显式列出程序类型**:即使 `delegate_cmds` 放开了 `BPF_PROG_LOAD`,若 `delegate_progs` 里没有该类型,加载依然被拒。
7. **`cgroup/skb` 与 `cgroup_skb/ingress` 混用**:两者 attach 类型相同,不要以为挂了两次不同效果的钩子。
8. **`/proc/<pid>/cgroup` 在 v2 下格式固定为 `0::$PATH`**:沿用 v1 的 `N:controller:path` 解析会全线失败。
9. **僵尸进程无法迁移**:它不出现在 `cgroup.procs` 里;想删 cgroup 时又需要"无存活进程",容易卡在"看起来空了却删不掉"。

## 参考资料(实际阅读过的权威来源)

- [The Linux Kernel documentation — Control Group v2](https://docs.kernel.org/admin-guide/cgroup-v2.html) — 单一层级、`no internal process` 约束与根 cgroup 豁免、`cgroup.subtree_control` 的全有或全无与"最后一个生效"、自顶向下约束、接口文件归属父节点、委托模型与**迁移封闭性**(公共祖先的 `cgroup.procs` 写权限)、`nsdelegate`、`cgroup.procs`/`cgroup.threads` 语义、`0::$PATH`、threaded 模式与 `cgroup.type`、异步销毁导致的懒迁移、"Organize Once and Control"
- [The Linux Kernel documentation — BPF Program Types and ELF Sections](https://docs.kernel.org/bpf/libbpf/program_types.html) — `cgroup/dev`、`cgroup/skb`、`cgroup_skb/ingress|egress`、`cgroup/connect4|6`、`cgroup/sendmsg4|6`、`cgroup/post_bind4|6`、`cgroup/sock_create`、`cgroup/sysctl`、`cgroup/getsockopt|setsockopt`、`sockops` 等 section 名与其 (程序类型, attach 类型) 对照表
- [eBPF Docs — BPF Token](http://docs.ebpf.io/linux/concepts/token/) — 委派动机(CAP_SYS_ADMIN 过粗 → 5.8 引入 CAP_BPF)、TC/XDP 需 `CAP_BPF`+`CAP_NET_ADMIN`、tracing 需 `CAP_BPF`+`CAP_PERFMON`、token 的 7 步获取流程(BPFFS fd → 特权进程 fsconfig/fsmount → `BPF_TOKEN_CREATE`)、四项委派选项、token 绑定创建它的 userns
- [The Linux Kernel documentation — eBPF Syscall(`BPF_TOKEN_CREATE`)](https://docs.kernel.org/6.18/userspace-api/ebpf/syscall.html) — "BPF token is 'associated' with the owning user namespace of BPF FS instance ... subsequent BPF operations performed with BPF token would be performing capabilities checks within that user namespace"、无 token 时能力须在 init userns 的原文
- [Linux 内核提交 `bpf: introduce BPF token object`(4527358)](https://linux.googlesource.com/linux/kernel/git/will/linux/+/4527358b76861dfd64ee34aba45d81648fbc8a61%5E%21/) — "BPF token in itself is not sufficient to grant BPF functionality"、`bpf_token_capable()` 把 `capable()` 换成 `ns_capable()`、`BPF_TOKEN_CREATE` 需要 `ns_capable(CAP_BPF)`
- [Kubernetes — Container Runtime Interface (CRI)](https://kubernetes.io/docs/concepts/architecture/cri/) — 运行时如何在节点上接住 kubelet 的请求(RuntimeService / ImageService 两类 RPC),与本 demo 的"运行时代为安装 cgroup 策略"上下文相关
