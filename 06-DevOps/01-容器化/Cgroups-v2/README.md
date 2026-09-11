# Cgroups v2 资源限制

> 容器三大基石之二:namespaces 负责"看不见",**cgroups 负责"用不多"**(CPU/内存/IO 等资源配额),联合文件系统负责"叠着放"。

## 简介

- cgroup(control group)以**分层树结构**组织进程,并沿层级**受控且可配置地**分配系统资源(内核文档原文:"organize processes hierarchically and distribute system resources along the hierarchy in a controlled and configurable manner")
- 两部分:**core**(组织进程)+ **controllers**(分配具体资源:cpu / memory / io / pids …)
- 关键概念:
  - **单一层级(unified hierarchy)**:v2 只有一棵树,挂载 `mount -t cgroup2 none /sys/fs/cgroup`(v1 每个控制器一棵树,资源管理互相割裂)
  - **纯文本接口**:cgroupfs 上的一个目录就是一个 cgroup,接口文件即属性
  - **进程迁移**:把 PID 写入目标组的 `cgroup.procs` 即完成迁移;`/proc/pid/cgroup` 显示成员关系(格式 `0::$PATH`)
- 历史:v1 2007 年进入主线;v2 由 Tejun Heo 主导,2016 年(4.5)正式可用,现代 systemd 发行版默认引导为 v2

## 原理详解

### 核心 API(接口文件)

| 文件 | 作用 | 关键细节 |
| --- | --- | --- |
| `cgroup.controllers` | 该组**可用**的控制器 | 只读;由父组 `subtree_control` 决定 |
| `cgroup.subtree_control` | 为**子代**启用哪些控制器 | 写 `"+cpu +memory -io"`;单次写全成功或全失败 |
| `cgroup.procs` | 成员进程 PID 列表 | 写一个 PID = 迁移该进程(所有线程);fork 出生在父进程当前组 |
| `cgroup.events` | `populated` 事件 | 组内无活进程时翻 0,可用于清理通知 |
| `cgroup.type` | domain / threaded | 线程模式切换(单向) |

### CPU 控制器(带宽配额模型)

```text
cpu.max   = "$MAX $PERIOD"     # 每 $PERIOD 微秒最多消耗 $MAX 微秒 CPU
            "50000 100000"     # = 每 100ms 最多 50ms = 50% 单核
            "max 100000"       # max = 不限
cpu.weight = 1..10000(默认 100) # 相对权重,200 的进程拿到的份额是 100 的两倍
cpu.stat: usage_usec / nr_periods / nr_throttled / throttled_usec
```

内核实现:`cfs_bandwidth` 结构按周期(hrtimer)发放 quota;配额耗尽的运行队列进入 `throttled_cfs_rq` 队列被摘下运行队列,等周期定时器补充 —— 这就是 `nr_throttled` 计数的来源。

### Memory 控制器

| 文件 | 语义 |
| --- | --- |
| `memory.current` | 当前总用量(字节);含匿名内存、page cache、内核 dentry/inode、TCP 缓冲 |
| `memory.min` / `memory.low` | 硬/软保护:用量在此线下时回收器不(尽量不)动它 |
| `memory.high` | 软上限:超限触发回收 + 按超额比例节流(`schedule_timeout_killable`),不杀进程 |
| `memory.max` | 硬上限:超限先强制回收,回收失败 → **OOM kill** |
| `memory.events` | 计数器:`high` / `max` / `oom` / `oom_kill` |

### 组织规则(v2 特有的两个约束)

1. **内部进程约束**:非根 cgroup 若有成员进程,就不能在其 `subtree_control` 启用 domain 控制器(root 豁免)—— 逼迫"进程放叶子组,中间组只管资源"
2. **层级收紧**:子组启用控制器只会**进一步限制**;靠近根的限制不能被叶子覆盖("restrictions set closer to the root can not be overridden")

### demo 流程

```text
mkdir /sys/fs/cgroup/cgroup-demo        # 建组 = 建目录
echo "+memory +cpu" > .../subtree_control(root,缺才补)
echo $$ > cgroup-demo/cgroup.procs      # 自迁移
echo 16777216 > cgroup-demo/memory.max  # 16MiB 硬上限
(子进程触碰 512MB) ──> 回收失败 ──> OOM killer SIGKILL
cat cgroup-demo/memory.events           # oom_kill >= 1
echo $$ > /sys/fs/cgroup/cgroup.procs   # 迁回 root
rmdir /sys/fs/cgroup/cgroup-demo        # 有进程的组不可删
```

## 对比 / 选型(cgroup v1 vs v2)

| 维度 | v1 | v2 |
| --- | --- | --- |
| 层级 | 每控制器一棵独立树 | 单一统一层级 |
| 进程归属 | 可同时属多个层级 | 恰属一个 cgroup(所有线程同组) |
| 内部进程约束 | 无(层级易被滥用) | 有(进程只在叶子,结构清晰) |
| 委托 | 难以安全下放 | `nsdelegate` 挂载选项以 user namespace 为边界 |

## 环境准备

- OS:Linux,cgroup v2 已挂载(`stat /sys/fs/cgroup/cgroup.controllers` 存在;systemd ≥ 240 的发行版默认)
- root 权限(写 cgroupfs)
- C:gcc;Python:3.8+;Go:1.21+

## 运行方式

### C
```bash
gcc -O2 -Wall -Wextra cgroup_demo.c -o cgroup_demo
sudo ./cgroup_demo org && sudo ./cgroup_demo mem && sudo ./cgroup_demo cpu
```

### Python / Go
```bash
sudo python3 cgroup_demo.py <org|mem|cpu>
sudo go run cgroup_demo.go <org|mem|cpu>   # mem-child/cpu-child 为内部子命令
```

## 关键代码片段(Python 版 demo_mem 核心)

```python
write_file(f"{CGDEMO}/cgroup.procs", str(os.getpid()))  # 自迁移入组
write_file(f"{CGDEMO}/memory.max", "16777216")          # 16MiB 硬上限
pid = os.fork()
if pid == 0:                                            # 子进程触碰 512MB
    buf = bytearray(512 << 20)
    for i in range(0, len(buf), 4096):                  # 逐页触碰才真正分配
        buf[i] = 1
    os._exit(0)                                         # 通常到不了这里
_, status = os.waitpid(pid, 0)                          # SIGKILL(9) = OOM kill
# memory.events 的 oom_kill 计数 >= 1
```

## 性能与边界

- cpu 配额粒度:周期通常 100ms,配额低于单次调度开销时(如 `1000 100000` = 1%)进程呈现明显"卡 100ms 跑 1ms"的锯齿
- memory.max 不是精确预留:page cache 计入 current,可被回收,所以"用量看起来超限"很常见
- cgroup v2 文档明示:**RT 实时进程不受 cpu 控制器管理**,且启用 cpu 控制器要求所有 RT 进程在 root 组
- v1 与 v2 可混合挂载(过渡期),但生产环境强烈不建议动态搬移控制器

## 注意事项与常见坑

1. **有成员进程的目录 `rmdir` 报 `EBUSY`** —— 删组前必须先把进程迁走(写 root 的 `cgroup.procs`);僵尸进程不出现在 `cgroup.procs` 但同样阻止删除
2. **在非根组上启用控制器报 `EBUSY`**(内部进程约束)—— 控制器要写在**父组**的 `subtree_control` 里,为**子代**启用;demo 里统一在 root 上补(缺才补,不回收,避免破坏 systemd 配置)
3. **`malloc` 成功 ≠ 内存已记账** —— 匿名页在**首次触碰**时才分配并计入 `memory.current`;OOM demo 必须逐页写
4. **memory.max 触发的是先回收后 kill**,不是立即 kill —— 若 page cache 占大头,进程可能先经历长尾延迟;`memory.high` 只节流不杀,适合"软限制"场景
5. **`cpu.max` 限的是时间片不是频率** —— 50% 配额的进程在多核上可能单周期内跑满半数核的时间,需要配合 `cpuset` 才能限制核数
6. Go 版用 `exec.Command` 自我复制代替 fork(子进程继承 cgroup 成员关系),与 C/Python 的 fork 语义等价

## 参考资料(实际阅读过的权威来源)

- [Control Group v2 — Linux Kernel Documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html) — 单一层级、组织规则、内部进程约束、cpu/memory 控制器全部接口文件(搜索快照全文阅读)
- [Control Group v2 (中文镜像)](https://linuxkernel.org.cn/doc/html/latest/admin-guide/cgroup-v2.html) — 交叉验证中文表述(全文阅读)
- [Cgroup v2 Resource Controllers — kernel-internals.org](https://kernel-internals.org/cgroups/cgroup-controllers) — `cfs_bandwidth` 限流内核实现、`memory.high` 节流函数 `mem_cgroup_handle_over_high`(快照阅读)
