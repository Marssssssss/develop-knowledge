# 中断与软中断剖析 — interrupts / softirqs / IRQ affinity

CPU 时间被谁吃掉了?`top` 里的 `%hi`(硬中断)和 `%si`(软中断)回答了「有多少」,
但回答不了「为什么」。这个 demo 拆开四份口径完全不同的文件,把「%si 高」拆成
三种**动作完全不同**的结论。

三种语言实现同一组自检:**Python / Go / C**,不需要 root、不需要 eBPF。

## 简介

- **硬中断(IRQ)**:设备拉线(或发 MSI 消息)→ 内核切到中断上下文 → 执行 ISR。
  上半部必须极快,否则后续中断会丢。
- **软中断(softirq)**:下半部的延迟处理。内核**编译期静态定义**、不能在模块里动态注册,
  这正是 tasklet / workqueue / threaded IRQ 存在的原因。
- **为什么要分开看**:同样是「CPU 很忙」,硬中断风暴、软中断积压、steal time 的处置
  方式完全不同 —— 前两者要动 IRQ 亲和性或协议栈参数,后者只能找 hypervisor。
- **本 demo 做什么**:不采集真实数据,而是复刻并验证**四份文件的口径**,共 53 项断言,重点在三个最易踩的坑。

## 原理详解

### 1. /proc/interrupts 的行结构

```
           CPU0       CPU1       CPU2       CPU3
  0:         46          0          0          0   IO-APIC    2-edge      timer
 24:      10234       5601       4200       8991   PCI-MSI 524288-edge      eth0
 32:          0    1034521          0          0   PCI-MSI-edge      eth0-TxRx-0
 33:          0          0    1034522          0   PCI-MSI-edge      eth0-TxRx-1
NMI:         12         14         13         12   Non-maskable interrupts
LOC:    1234567    1234568    1234569    1234570   Local timer interrupts
RES:       4321       2109       3210       4102   Rescheduling interrupts
```

解析规则:标签(以 `:` 结尾)+ **N 个每核计数**(N = 表头 CPU 列数)+ 控制器类型 + 驱动名。
最后一个 token 是可以逗号连接的**驱动列表** —— 一个 IRQ 可以共享给多个驱动。

**`NMI:` / `LOC:` / `RES:` 这类行没有 IRQ 号**,它们是架构向量:官方 `/proc` 文档写得很明确,
未编号的中断**只被计入 `/proc/stat` 的 `intr` 总数**。判断依据应该是「标签能不能当整数读」,
**不是白名单** —— 向量种类随内核版本和平台变。

### 2. 多队列网卡的中断分散

`eth0-TxRx-0` / `eth0-TxRx-1` 是同一个网卡的不同**队列**,各自一个 IRQ。如果两个队列的
中断都只落在 CPU0,说明多队列没生效(或被 `irqbalance` 挤到一起),网卡再快也会卡在单核上。
分散成功的标志就是计数落在**不同** CPU 上。

### 3. /proc/softirqs 与 /proc/stat 的口径差别

`/proc/softirqs` 每行一种软中断,顺序是内核预定义且**固定**的:

```
HI  TIMER  NET_TX  NET_RX  BLOCK  IRQ_POLL  TASKLET  SCHED  HRTIMER  RCU
```

名字必须靠外部知识对齐,不能靠行内猜。两个文件都是**累计值**(与 `/proc/stat`、
`/proc/net/dev` 一样),要两次采样求差。

有意思的是 `/proc/stat` 两行的口径**不一样**:

| 行 | 总数 vs 分项之和 |
|---|---|
| `intr` | 总数 **>** 分项之和(未编号的架构向量只计入总数) |
| `softirq` | 总数 **=** 分项之和(所有类型都被列出) |

同一个文件里两句话,不看清楚就会算错占比 —— 本 demo 对两者都写了断言。

### 4. /proc/net/softnet_stat:十六进制 + 没有表头

每 CPU 一行,**全是十六进制**,而且**文件里没有表头**。列序只能靠外部知识:

| 列 | 含义 | 对策 |
|---|---|---|
| 1 | 处理过的包数 | — |
| 2 | 因 backlog 满而**丢掉**的包数 | 抬 `net.core.netdev_max_backlog` |
| 3 | `time_squeeze`:预算用完但还有活 | 抬 `net.core.netdev_budget`(默认 300) |

第 4 列之后(collision / RPS / flow limit …)**随内核版本增加,含义不稳定**,本 demo 不解析。
`00000123` 这种值既是合法十进制(123)又是合法十六进制(291),按十进制读会**静默**给出错数。

### 5. IRQ 亲和性:smp_affinity 位掩码

`/proc/irq/<N>/smp_affinity` 是**十六进制位掩码**:第 b 位为 1 表示允许 CPU b 处理这个 IRQ。
默认 `0xffffffff`(全部非活跃 IRQ 由 `default_smp_affinity` 决定)。内核**不允许**把它设成全 0。

```
echo 8   > /proc/irq/42/smp_affinity     # 只用 CPU3(位 3)
echo f   > /proc/irq/42/smp_affinity     # CPU0-3
```

**超过 32 核时是逗号分组,而且低位组在前**:`ffffffff,ffffffff` 表示 64 个核都能处理,
第一个组管 CPU 0-31,第二个组管 32-63。位序读反就指向完全错的 CPU。

`/proc/irq/<N>/smp_affinity_list` 是给人看的接口,支持 `0-3` / `0,3` / `1024-1031` ——
后者用掩码写要 32 个零。默认情况下内核在允许的 CPU 之间**轮转**分发。

### 6. 把「%si 高」拆成三种动作

```
%si 高 ┬─ dropped 在长 ──→ backlog 太浅,包在入队前就丢了 → 抬 netdev_max_backlog
       ├─ squeeze 在长 ──→ 单次预算用完还有活,被打断      → 抬 netdev_budget
       └─ 两个都不长  ──→ 真的是活多,不是队列问题        → 分散 IRQ / 合并中断 / 减少包量
```

**丢包优先于 squeeze**:丢包是已经发生的损失,squeeze 只是「被打断」,前者更紧急。
第三种结论最重要 —— 如果两个计数器都不长,再调 backlog 和 budget 是白费力气。

## 对比

| 文件 / 工具 | 给什么 | 坑 |
|---|---|---|
| `/proc/interrupts` | 每 IRQ 每核累计次数、控制器类型、驱动列表 | 有无编号向量行 |
| `/proc/softirqs` | 每类型每核累计次数 | 名字要靠预定义表对齐 |
| `/proc/net/softnet_stat` | 每 CPU 处理/丢包/squeeze | **十六进制、无表头** |
| `/proc/stat` `intr`/`softirq` | 总量 + 分项 | 两行口径不一样 |
| `mpstat` 的 `%hi` / `%si` | 换算成时间占比 | 只是比例,不含归因 |
| `/proc/irq/N/smp_affinity` | 谁能处理该 IRQ | >32 核分组顺序 |

`mpstat` 或 `top` 告诉你「软中断占了 30% CPU」,这套文件告诉你「这 30% 该动哪个旋钮」。
## 环境准备

- Python 3.9+ / Go 1.21+ / 任意 C99 编译器。自检**不需要** root、eBPF、也不需要 Linux。
- 若要读真实文件,需 Linux;`/proc/irq/<N>/smp_affinity` 只有 root 可写。

## 运行方式

```bash
# Python(53 项自检;同模块拆分:irq_check.py + irq_parse.py)
cd python && python irq_check.py

# Go(同包三文件,必须用 go run .)
cd go && go run .

# C(实现拆到 irq_impl.h,文本包含;编译入口始终是 irq_check.c)
cd c && cc -O2 -o irq_check irq_check.c && ./irq_check
```

## 关键代码片段

判断「是不是 IRQ」——不用白名单:

```python
def numbered_irqs(rows):
    """只留下有编号的行 —— 判断「是不是 IRQ」的唯一依据是标签能不能当整数读。"""
    return [r for r in rows if r[0].isdigit()]
```

十六进制位掩码,注意分组顺序:

```python
groups = [g.strip() for g in s.split(",")]
mask = 0
for i, g in enumerate(groups):
    mask |= int(g, 16) << (32 * i)   # 第一个组是低位,管 CPU 0..31
```

统计被关掉时的语义保护:

```python
if dropped <= 0 and squeeze <= 0:
    return REAL_WORK   # 不是「没问题」,而是「调参没用,该换方向」
```

## 性能与边界

- 自检是纯计算,毫秒级。
- 真实系统上 `watch -n 1 cat /proc/interrupts` 看**增长量**比看绝对值有意义;
  一次性读到的都是开机以来的累计值。
- 本 demo **不**解析 `/proc/irq/` 下的 `node`、`affinity_hint` 等文件,也不处理 IA64 的
  `r`(redirectable)前缀 —— 那是平台特有行为,官方文档单独成篇。

## 注意事项与常见坑

1. **架构向量不是 IRQ**。`NMI:` / `LOC:` / `RES:` 没有编号,官方只把它们计入 `intr` 总数。
   把它们当成 IRQ 号会让「IRQ 数量」这个统计虚高。
2. **`/proc/net/softnet_stat` 是十六进制且无表头**。`00000123` 按十进制读得到 123,
   按十六进制是 291 —— 不会报错,只会**静默**给出错数。
3. **`/proc/stat` 的 `intr` 与 `softirq` 两行口径不同**:前者总数 > 分项和,后者严格相等。
4. **smp_affinity 的逗号分组低位在前**。`00000000,ffffffff` 是「只用高 32 核」,
   不是「只用低 32 核」。
5. **不能把 affinity 设成全 0** —— 内核会拒绝;写之前先确认目标 CPU 是 `cpuset` 允许的。
6. **别忽略「两个都不长」这个结论**。高 `%si` 且不丢包、不 squeeze,说明是真活多,
   该做的是分散 IRQ、开 GRO/合并中断,而不是继续加 backlog。
7. **`%hi`/`%si` 高不等于瓶颈**:如果软中断处理的是你自己需要的流量,它就是有效工作;
   判断瓶颈要结合 `squeeze`/`dropped` 与实际吞吐。另外 `dropped` 计的是 **backlog** 满了丢的包,
   网卡硬件队列满通常体现为 `rx_dropped`(在 `/proc/net/dev`),两者要分开看。

## 参考资料

实际读取的权威来源:

- 内核文档 *SMP IRQ affinity*:`smp_affinity` 位掩码 / `smp_affinity_list` 语义、
  `default_smp_affinity` 默认 `0xffffffff`、不允许关掉所有 CPU。<https://docs.kernel.org/core-api/irq/irq-affinity.html>
- 内核文档 *The /proc Filesystem*:`/proc/interrupts` 与 `/proc/stat` 的 `intr`/`softirq` 定义 ——
  「第一个数是总数,后面是每个中断的总和;未编号中断不显示,只计入总数」;`/proc/irq` 目录说明。
  <https://www.kernel.org/doc/html/latest/filesystems/proc.html>
- Red Hat *Performance Tuning Guide* §4.3:IRQ 到 ISR 的分发过程、`/proc/interrupts` 各列含义,
  以及 **64 核系统用逗号分隔 32 位组**的写法(`ffffffff,ffffffff` / `0xffffffff,00000000`)。
  <https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/6/html/performance_tuning_guide/s-cpu-irq>
- 内核文档 *IRQ affinity on IA64 platforms*:平台特例,只取**第一个非零位**,`r` 前缀表示可重定向。
  本 demo 明确不处理该情形。<https://docs.kernel.org/6.1/ia64/irq-redir.html>
