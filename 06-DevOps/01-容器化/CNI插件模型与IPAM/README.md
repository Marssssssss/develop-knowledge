# CNI 插件模型与 host-local IPAM

> CNI 把「容器网络」切成两件事：**运行时**（读配置列表、按序拉起插件、串联 `prevResult`）与**插件**（真正建网卡、配路由、发地址）。本文把这两层都做成可执行的模型，并把 `host-local` IPAM 的轮询分配器逐行转写出来——它是「为什么容器重启后 IP 会变」这个问题的答案所在。

权威来源（本 demo 实际读过）：

- `containernetworking/cni` **SPEC.md**（55356 B，main 分支原文）：§1 配置格式、§2 执行协议、§3 配置列表的生命周期与顺序、§5 结果类型
- `containernetworking/plugins` `plugins/ipam/host-local/backend/allocator/`：`range.go`（`Canonicalize`/`Contains`/`Overlaps`/`lastIP`）、`range_set.go`（`RangeFor`/`Canonicalize`）、`allocator.go`（`Get`/`GetIter`/`Next`）、`main.go`

```bash
python python/main.py              # 演示入口
python python/selfcheck_cni_ipam.py   # 65 项断言
go run go/cni_range.go go/cni_ipam.go go/main.go
```

## 1. 执行协议：环境变量进、JSON 出

插件是一个**可执行文件**，运行时把参数放进环境变量、把配置写进 stdin：

| 变量 | 含义 | 备注 |
| --- | --- | --- |
| `CNI_COMMAND` | `ADD` / `DEL` / `CHECK` / `GC` / `VERSION` | 五个操作 |
| `CNI_CONTAINERID` | 容器 ID | 不能以数字以外的字符开头，必须非空 |
| `CNI_NETNS` | 隔离域引用，通常是 `/run/netns/<name>` | 附件参数 |
| `CNI_IFNAME` | 容器内网卡名 | 插件用不了这个名字就必须报错 |
| `CNI_ARGS` / `CNI_PATH` | 额外 KV（分号分隔）/ 插件搜索路径 | 可选 |

成功 → stdout 输出 result JSON、退出码 0；失败 → stderr 输出 error 结构、退出码非 0。**附件（attachment）的唯一标识是 `(CNI_CONTAINERID, CNI_IFNAME)` 二元组**——同一个容器要加入同一网络两次，必须换网卡名。

关键约束（SPEC §3 Lifecycle）：

- 运行时**不得**对同一容器并行操作，但**可以**对**不同**容器并行（所以 IPAM 插件必须自己加锁）；
- `GC` 与 `ADD`/`DEL` **互斥**：跑 `GC` 前必须等所有 add/delete 结束，跑完才能发新的；
- `ADD` 失败也要补一次 `DEL`；`DEL` 可以重复调用；
- 配置在 `ADD` 与 `DEL` 之间不应改变。

## 2. 链式插件：`prevResult` 怎么传

配置是一个 `plugins` 列表（`bridge` → `tuning` → `portmap`）。三类操作的 `prevResult` 语义**完全不同**，这是最容易搞错的地方：

| 操作 | 执行顺序 | `prevResult` 传什么 | 出错怎么办 |
| --- | --- | --- | --- |
| `ADD` | **正序** | 上一个插件的返回值；首个插件**不带**该字段 | 立即中止 |
| `DEL` | **逆序** | 恒为 `ADD` 的**最终**结果（不是上一个插件的） | 立即中止 |
| `CHECK` | 正序 | 恒为 `ADD` 的**最终**结果 | 立即中止（`disableCheck` 时直接返回成功） |
| `GC` | 正序 | 不带附件参数，改带 `cni.dev/valid-attachments` | **继续跑完，收集全部错误** |

`DEL` 之所以要逆序且统一喂最终结果：逆序是拆栈顺序（先装的 portmap 规则先撤），而「喂最终结果」保证每个插件都能看到完整拓扑——否则逆序到第 2 个插件时，它想知道的 IP 可能已经被后面的插件"还没撤"的信息遮蔽。

`GC` 是唯一「出错也继续」的操作：它本来就是清理陈旧资源，一个失败不该挡住其他插件的清理。

请求配置的派生（SPEC §3 末节）：运行时注入 `cniVersion`（选中的协议版本，当前 1.1.0）与 `name`，**剥离** `capabilities`，其余未知字段原样透传。

## 3. Range 的规范化：默认值从哪来

`host-local` 的配置只给一个 `subnet`，其余字段是 `Canonicalize()` 补的：

```
/24 10.1.0.0  →  Gateway = .1     RangeStart = .1     RangeEnd = .254
```

- `Gateway` 为空 → 取 `NextIP(subnet.IP)`，即 `.1`；
- `RangeStart` 为空 → **同样取 `.1`**（源码注释明说这会和网关冲突，靠迭代器跳过）；
- `RangeEnd` 为空 → `lastIP(subnet)` = `ip | ^mask`，**v4 再末字节减 1**，所以 `/24` 是 `.254` 而不是 `.255`（广播地址）。这一字节的差别正是"能分 253 个还是 254 个"的分界。

三条硬校验：

1. `ones > masklen-2` → `Network ... too small to allocate from`。所以 **`/31` 和 `/32` 直接拒绝**，`/30` 是最小可用（`.1` 作网关，只剩 `.2`）；
2. 子网地址带主机位 → `Network has host bits set`；
3. `RangeStart`/`RangeEnd` 必须落在子网内（注意此时另一端可能还是 nil，`Contains` 会忽略它）。

`Contains` 用三段判定：地址族一致 → 落在子网内 → 在 `[RangeStart, RangeEnd]` 内（nil 的边界忽略）。因此**网络地址 `.0` 会被判 False**（不是因为不在子网，而是小于 `.1`）。

## 4. 轮询迭代器：为什么 crash-loop 的容器不会立刻拿回旧 IP

`GetIter()` 的策略写在源码注释里：*a crash-looping container will not see the same IP until the entire range has been run through*。实现是：

1. 读 `store.LastReservedIP(rangeID)`；若存在且仍在 RangeSet 内 → 迭代器定位到该 range、`cur = lastReservedIP`；
2. 否则 → `rangeIdx = 0`，`startIP = rangeset[0].RangeStart`（`cur` 留空）。

`Next()` 的状态机：

- `cur == nil`（首次）→ `cur = RangeStart`、`startIP = cur`、若等于网关则递归跳过；
- `cur == RangeEnd` → `rangeIdx = (rangeIdx+1) % len`、**切换到下一个 range 的 `RangeStart`**；
- 否则 `cur = NextIP(cur)`；
- `startIP` 为空就设为 `cur`，否则若 `cur == startIP` → 返回空（穷尽）；
- 等于网关 → 递归跳过。

三个可直接观测的后果（均在自检里断言）：

- 单个 `/24` 首个可用地址是 **`.2`**（`.1` 是网关被跳），可分配 **253** 个；
- 把网关挪到 `.5`，`.1` 就变成可分配，总数仍是 253；
- 两个 `/24` 共 506 个，跨 range 后第一个是 `10.1.1.2`（`10.1.1.1` 是第二个 range 的网关）。

于是 `/29` 场景：首次 `.2` → 释放 → 再分配得 `.3`（不是 `.2`）；只有把 5 个地址全遍历一遍才会回到 `.2`。

## 5. 分配器的三条拒绝路径

`Get(id, ifname, requestedIP)`：

- **指定 IP**：不在集合内 → `... not in range set`；等于网关 → `requested ip ... is subnet's gateway`；已被别人占用 → `... is not available in range set`；
- **不指定**：先把 `store.GetByID(id, ifname)` 里落在当前 RangeSet 的地址翻出来，非空就报 `duplicate allocation is not allowed`（SPEC 不允许同一容器重复分配）；
- **耗尽**：`no IP addresses available in range set`。

## 6. 与既有 demo 的分工

- `veth-pair-网络设备`：网卡怎么跨 netns 移动、bridge 怎么转发（**数据面**）；
- 本 demo：谁决定调这些插件、按什么顺序、IP 从哪个格子拿（**控制面**）；
- `OCI-Runtime-Spec`：容器进程本身的生命周期，与网络插件链是两条独立的链。

## 7. 自检覆盖

65 项断言，分五组：Range 规范化（含 `/30`/`/31`/`/32` 边界、host bits、越界、地址族）、RangeSet（空集/混合族/重叠/`RangeFor`）、迭代器（首个地址、总数、网关跳过、跨 range、lastReservedIP 轮转）、分配器（重复分配/三种拒绝/轮询不回退/耗尽）、运行时链式执行（ADD 正序与 prevResult 串联、出错中止、DEL 逆序且统一结果、`disableCheck` 短路、GC 收集全部错误、请求派生规则）。
