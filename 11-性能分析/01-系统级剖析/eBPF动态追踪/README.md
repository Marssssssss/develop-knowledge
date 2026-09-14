# eBPF 动态追踪

## 简介

eBPF（extended Berkeley Packet Filter）是**运行在内核里的沙箱化虚拟机**：用户把一段受限字节码通过 `bpf(2)` 加载进内核，内核先用**验证器（verifier）**静态证明它安全，再用 **JIT** 编译成本机指令，挂到内核/用户态代码的 hook 点上执行。它让"重新编程内核行为"不再需要改内核源码、也不必加载内核模块，这条路径被称为"**内核的 JavaScript 时刻**"。

关键概念：

- **eBPF 虚拟机**：11 个 64 位寄存器 `r0`–`r10`，固定 **512 字节栈**，指令**定长 8 字节**
- **验证器**：静态证明程序不会越界访存、不会无限循环、不会读未初始化寄存器
- **JIT**：字节码 → 本机指令，执行开销接近原生内核代码
- **BPF map**：内核态与用户态共享的键值存储（hash / array / LRU / ringbuf / stack trace / LPM trie…）
- **helper function**：稳定 ABI 的内核函数白名单，替代"调用任意内核函数"
- **tail call**：跳去执行另一个 BPF 程序并**替换执行上下文**（类比进程的 `execve()`）

历史：1992 年 Van Jacobson 为 tcpdump 设计 BPF（后称 cBPF）做包过滤；2014 年 Alexei Starovoitov 将其扩展为 eBPF——"eBPF" 今天已是一个独立术语，不再展开为任何全称。

## 原理详解

### 1. 指令集与编码

基本指令 **64 位（8 字节）**，字段布局：

```text
+---------------+---------------+-------------------------------+
|    opcode     |     regs      |            offset             |  (16 bit signed)
+---------------+---------------+-------------------------------+
|                             imm                              |  (32 bit signed)
+--------------------------------------------------------------+
```

- `regs` 字段装 `src_reg`(4 bit) 与 `dst_reg`(4 bit)，取值 **0–10**；小端主机上是 `|src|dst|`
- opcode 的**最低 3 位是 instruction class**：

| class | 值 | 含义 |
| --- | --- | --- |
| `LD` | 0x0 | 非标准加载 |
| `LDX` | 0x1 | 内存 → 寄存器 |
| `ST` | 0x2 | 立即数 → 内存 |
| `STX` | 0x3 | 寄存器 → 内存 |
| `ALU` | 0x4 | **32 位**算术 |
| `JMP` | 0x5 | **64 位**跳转 |
| `JMP32` | 0x6 | **32 位**跳转 |
| `ALU64` | 0x7 | **64 位**算术 |

- ALU/JMP32（32 位）属 **base32** 一致性组；ALU64/JMP（64 位）、`DW` 访存属 **base64** 组
- 移位掩码：64 位操作用 `& 0x3F`，32 位用 `& 0x1F`
- `CALL` 有 3 种 `src_reg` 编码：`0x0` 静态 helper ID、`0x1` 程序内函数（`PC += imm`）、`0x2` BTF ID；`EXIT` = code `0x9`
- 加载/存储的 mode 修饰符含 `IMM`(0) / `ABS`(1) / `IND`(2) / `MEM`(3) / `MEMSX`(4) / **`ATOMIC`(6)**

### 2. 挂载点：kprobe 是怎么"打进去"的

eBPF 只是**执行引擎**，钩子由 kprobe/uprobe/tracepoint 等提供。kprobe 的插入过程（kernel.org `trace/kprobes.rst`）：

1. 注册时 kprobes **拷贝被探测的那条指令**，把它的**首字节替换为断点指令**（x86 上 `int3` = `0xCC`）
2. CPU 执行到该处触发 trap → 保存寄存器 → 经 `notifier_call_chain` 进入 kprobes → 执行 `pre_handler`
3. kprobes **单步执行它在别处保存的那份指令拷贝**——而不是"原地单步"：原地单步必须先临时撤掉断点，那会开一个"另一个 CPU 直接冲过探测点"的时间窗
4. 单步完成 → 执行 `post_handler`（可选）→ 继续执行探测点之后的指令

**kretprobe（返回探针）**：在函数入口下探针，命中时**保存真实返回地址并用 trampoline 地址（通常一条 nop）覆盖它**；函数返回时先跳到 trampoline 触发探针，handler 再把保存的返回地址写回。并发实例由 `kretprobe_instance` 承载，`maxactive` 决定预分配多少个实例（`<= 0` 时默认 `max(10, 2*NR_CPUS)`），实例不够则 `nmissed++`（**只是漏采，不是灾难**）。副作用：`__builtin_return_address()` 与栈回溯会看到 trampoline 地址。

**跳转优化（jump optimization）**：先按断点方式装上探针，再尝试把它替换为"detour buffer"——压寄存器 → `call` trampoline → 恢复寄存器 → 执行被优化区指令 → 跳回原路径；这样 handler 运行时不需关中断。

**黑名单**：kprobes 不能探测自身（会递归 trap / double fault），用 `NOKPROBE_SYMBOL()` 标记；注册被拒。x86-64 的 `__switch_to()` 也直接返回 `-EINVAL`（栈已切换）。另外 CISC 架构**不校验 `addr` 是否落在指令边界**，用 `offset` 要小心。

### 3. 验证器：它证明什么，不证明什么

| 保证 | 含义 |
| --- | --- |
| 必需特权 | 通常需 root / `CAP_BPF`（`unprivileged_bpf_disabled` 开启时可非特权加载**受限**程序） |
| 运行到完成 | 不阻塞、不死循环；**允许有界循环**，但必须能被证明存在必然成立的退出条件 |
| 内存安全 | 不使用未初始化变量、不越界访存、结果不超系统大小要求 |
| 复杂度有限 | 验证器评估**所有可能执行路径**，且必须在配置的复杂度上限内完成分析 |
| 抽象上下文 | 不能直接读任意内核内存，只能通过 helper 访问；不能随意改内核数据结构 |

**务必注意**：验证器是**安全工具（safety tool）**——它保证程序**跑起来安全**，**不是**检查"程序在干什么"的安全审计工具。加载后还有三层加固：程序内存置只读（被篡改则内核 panic 而非执行坏程序）、Spectre 缓解（访存掩码 + 推测路径跟踪 + 必要时发 Retpoline）、**常量致盲**（防 JIT spraying）。

### 4. hook 点选型

| 类型 | 目标 | 稳定性 | 开销 |
| --- | --- | --- | --- |
| `kprobe` / `kretprobe` | 任意内核函数（除黑名单） | **不稳定**（依赖函数名/偏移，跨内核版本会变） | 有 trap 开销 |
| `uprobe` / `uretprobe` | 任意用户态函数 | 同 kprobe，且随二进制变化 | 有 trap 开销 |
| `tracepoint` | 内核预置静态探针 | **稳定 ABI**，首选 | 低 |
| `fentry` / `fexit` | 函数入口/出口（BPF trampoline） | 较稳定 | **最低**（无 trap） |
| XDP / tc | 网络包路径 | 稳定 | 极低（原生路径） |

## 环境准备

- 操作系统：Linux（kprobe/ftrace 机制自 2.6.15 起多处理器并发安全）；本 demo 在任意平台可跑模拟部分
- 语言：Python 3.8+ / Go 1.21+ / C（GCC 或 Clang）；**无需 root 即可运行模拟与编码演示**
- 真实挂载需：`CAP_BPF`（或 root）+ 可写的 tracefs（`/sys/kernel/tracing`）

## 运行方式

### C

```bash
gcc -O2 -Wall -Wextra -pedantic bpf_kprobe.c -o bpf_kprobe
./bpf_kprobe          # 打印 int3 打补丁流程 + 编码一条真实 eBPF 指令；写入 tracefs 需 root
```

### Python

```bash
python3 mini_bpf.py   # 编码/解码 + 验证器 4 组用例 + 跑一个计数程序
```

### Go

```bash
go run .              # bpf_insn.go(编码)+ bpf_verify.go(类型格验证器,5 组用例)
```

## 关键代码片段

Python 侧的核心是**把"验证器"这件抽象事写成可读的数据流分析**：

```python
def verify(prog):
    """worklist 抽象解释：状态 = (pc, 已初始化寄存器位图)"""
    worklist, seen = [(0, 0)], set()
    while worklist:
        pc, init = worklist.pop()
        if (pc, init) in seen:      # 不动点：该路径已分析过
            continue
        seen.add((pc, init))
        ins = prog[pc]
        # ... 检查读的寄存器是否已初始化 / 栈偏移是否落在 [-512, 0)
        # 反向跳转：必须存在有界循环计数器，否则判 "unbounded loop"
```

以及把"有界循环"落成可机械检查的模式——反向跳转前必须有一条对被计数寄存器的 `SUB imm`：

```python
def bounded_loop_ok(prog, pc):
    """反向跳转 (JA/JNE 且 offset < 0) 必须伴随 MOV reg,imm + SUB reg,imm"""
    target = pc + 1 + prog[pc].offset
    counter = find_sub_counter(prog, target, pc)   # 在循环体内找 SUB reg, imm
    if counter is None:
        return False, "unbounded loop: no decrementing counter"
    return True, f"bounded by imm={counter.imm}"
```

## 性能与边界

- **JIT 后开销接近原生**：eBPF 官方表述为"运行效率等同于原生编译的内核代码或内核模块"
- kprobe 走 trap 路径，比 tracepoint / fentry 贵；高频路径优先 `fentry` / `tracepoint`
- 指令数上限（内核实现）：特权程序 100 万条、非特权 4096 条（本 demo 的模拟机用步数预算近似表达"复杂度上限"）
- 栈固定 **512 字节**，不能动态增长——大缓冲必须放 map 或 percpu 数组
- 验证器复杂度随**分支数**增长，巨型 switch 常导致验证超时（`BPF_COMPLEXITY_LIMIT_INSNS`）

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| 加载返回 `EACCES` | 非特权且 `unprivileged_bpf_disabled=1` | 提权或调 `kernel.unprivileged_bpf_disabled` |
| 循环被拒 | 反向跳转缺可证明的退出条件 | 用显式递减计数器 + 常量上界 |
| `__builtin_return_address()` 拿到错地址 | kretprobe 用 trampoline 覆盖了返回地址 | 改用 `kretprobe_instance.ret_addr` |
| 探针静默漏采 | `maxactive` 太小，`nmissed` 递增 | 按并发上限设 `maxactive`（默认 `max(10,2*NR_CPUS)`） |
| 函数偏移探不动 | CISC 不校验指令边界 | 用 `symbol_name` 而非裸 `addr` |
| 内核 panic（被篡改） | 程序内存只读保护被触发 | 这是**设计行为**，不是 bug |

## 参考资料（实际阅读过的权威来源）

- [What is eBPF?（eBPF 官方站点）](https://ebpf.io/what-is-ebpf/) — 验证器/JIT/maps/helper/tail call/三层安全加固的官方定义
- [Kernel Probes (Kprobes) — Linux Kernel Documentation](https://docs.kernel.org/trace/kprobes.html) — int3 打补丁四步、detour buffer、kretprobe trampoline 与 `maxactive`/`nmissed`、黑名单
- [BPF Instruction Set Specification, v1.0 — Linux Kernel Documentation](https://docs.kernel.org/bpf/standardization/instruction-set.html) — 8 字节编码布局、8 个 instruction class、ALU/JMP32 base32 vs ALU64/JMP base64、`CALL`/`EXIT`/`ATOMIC`
- 配套书：Liz Rice《Learning eBPF》(O'Reilly, 2023)、Brendan Gregg《BPF Performance Tools》(2019)（见 eBPF 官方推荐书单）
