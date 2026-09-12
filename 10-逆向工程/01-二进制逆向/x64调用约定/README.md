# x86-64 System V 调用约定(参数寄存器 / 栈帧 / callee-saved)

> 没有调用约定,反汇编就是无意义的指令流;有了它,`mov rdi, 0x1F4` 就是"第一个参数 = 500"。本 demo 双实现:C 侧用内联汇编**实证**寄存器约定确实生效,Python 侧模拟 psABI 的**参数分类算法**。

## 简介

- **实现**:`calling_convention.c`(GCC/Clang,寄存器抓拍)+ `sysv_arg_classifier.py`(分类算法模拟)
- **语言**:C / Python
- **规范**:[System V ABI, AMD64 Architecture Processor Supplement](https://refspecs.linuxfoundation.org/elf/x86_64-abi-0.95.pdf)(Linux/macOS 通用;**Windows x64 是另一套**)

## 原理详解

### 寄存器职责表(psABI §3.2.3 Figure 3.4)

| 寄存器 | 职责 | 跨调用保留 |
| --- | --- | --- |
| rdi rsi rdx rcx r8 r9 | 第 1-6 个 **INTEGER 类**参数(整数/指针) | ❌ caller-saved |
| xmm0-xmm7 | 浮点/SSE 参数(xmm0-1 兼作返回) | ❌ caller-saved |
| rax | 返回值;可变参数时 `%al` = SSE 寄存器用量上界 | ❌ |
| rdx | 第二返回寄存器(128 位返回值高位) | ❌ |
| rbx rbp r12-r15 | callee-saved(被调方负责还原) | ✅ |
| rsp | 栈指针,**call 前 16 字节对齐** | ✅ |
| r10 r11 | 临时寄存器(r10 = 嵌套函数静态链指针) | ❌ |

### 参数传递算法(为什么会有"第 7 个参数在栈上")

1. 每个参数先**分类**:INTEGER(整数、指针、≤16B 的混合结构拆 8B 分片)/ SSE(float、double)/ MEMORY(>16B 结构、`long double`,改走内存)。
2. INTEGER 类依次消耗 `rdi→r9` 共 **6 个**槽;SSE 类并行消耗 `xmm0→xmm7` 共 8 个槽(**两个序列独立计数,互不挤占**)。
3. 寄存器耗尽后的参数**按右到左压栈** —— 这就是 C demo 中 `&g` 与 `&h` 相邻 8 字节、且 `h` 地址更低的原因。
4. MEMORY 类(大结构):调用方在栈上分配空间,把地址作为"隐藏的第一个参数"塞进 rdi,原参数整体右移一位;返回大结构同理(rdi 传入缓冲区地址,rax 返回它)。

### 16 字节对齐与红色区

- psABI 要求 **call 指令执行前 `rsp % 16 == 0`**(SSE 对齐访问需要);call 压入 8 字节返回地址后,被调函数入口 `rsp % 16 == 8` —— C demo 的 `sp_alignment_probe` 打印验证的正是这一点。
- 叶子函数可用 rsp 以下 128 字节**红色区(red zone)**存放临时值而不调整 rsp —— 逆向时看到 `mov -0x8(%rsp),...` 却没有 `sub rsp` 不要误判为越界。

### 逆向应用:从汇编"猜签名"

- 入口 `mov rdi, X; mov rsi, Y` 连续装填 → 前 2 参;`call` 前有 `push` → 参数 ≥ 7 个。
- `sd %xmm0` 附近出现 → 浮点参数/返回值;`%al` 在 call variadic 函数前被置数 → 可变参调用(printf 家族)。
- 函数体中 `push rbx; push r12` 开场 → 用了 callee-saved 寄存器,常意味着有需要在调用间存活的变量(缓存/循环变量)。
- **Windows x64 完全不同**:参数寄存器是 `rcx rdx r8 r9` + 影子空间(shadow space,调用方必须预留 32 字节)且只前 4 个,分析 Windows 二进制时不可套用本文规则。

## 运行方式

```bash
# Python 模拟器(任何平台)
python sysv_arg_classifier.py
# C 实证(需 GCC/Clang,Linux 或 WSL/macOS)
gcc -O1 -fno-inline -o calling_convention calling_convention.c && ./calling_convention
```

## 关键代码说明

- C 版在函数第一条指令处用 `__asm__ volatile("movq %%rdi,%0")` 抓拍入口寄存器 —— 必须在编译器"消费"参数之前,因此加 `noinline` 并保证抓拍位于函数最前(`-O1 -fno-inline` 降低参数被提前搬家的概率;`-O2` 下参数可能已被移入其他寄存器,属正常优化,不影响结论)。
- Python 版 `call()` 里 INTEGER 与 SSE 用两个独立迭代器,正是 psABI"并行计数"语义的直译。

## 性能边界

- C 抓拍法依赖编译器未重排,`-O3` 下不保证寄存器仍是入口原值;工业做法是 gdb 断点在函数地址(`b *0x401000`)再 `info registers`,或 frida `onEnter` hook。
- Python 模拟未实现完整 eightbyte 分类算法(union 位域/`__m256`/MEMORY 类合并规则),仅覆盖最常见路径。

## 注意事项与常见坑

1. **`-O2` 抓拍失效不是约定失效** —— 抓拍必须在真实入口断点处做,这是 demo 用 `-O1` 的原因。
2. **结构体传参是"拆开"的**:`struct {int a,b; double d;}` 会同时占一个 INTEGER 槽(rdi)和一个 SSE 槽(xmm0),逆向时"一个参数占了两个不相邻类型的寄存器"往往就是结构体。
3. **可变参数的 `%al`**:调用 `printf` 前 `%al` 必须 ≤ 8 且是 SSE 用量上界 —— 看到 `mov al, 0/1` 后紧跟 call 即 variadic 调用的指纹。
4. **Go/Rust 不遵守 SysV 的部分细节**:Go 的 ABI(abi-internal)用寄存器传参但布局自定义;Rust 的"重整(shim)"会给非 SysV 签名的函数加包装。遇到无符号/奇怪序言先怀疑非标准 ABI。

## 参考资料(实际读过)

- [System V ABI AMD64 Architecture Processor Supplement(refspecs.linuxfoundation.org PDF)](https://refspecs.linuxfoundation.org/elf/x86_64-abi-0.95.pdf):Figure 3.4 寄存器职责表、§3.2.3 参数分类与传递算法、Figure 3.5/3.6 示例
- [AMD64 ABI Features — Oracle 文档](https://docs.oracle.com/cd/E19253-01/816-5138/fcowb/index.html):rdi..r9 六参数寄存器、16 字节栈对齐、PC 相对寻址等要点确认
- [Registers — x86-64 SysV 教学页(汉诺威大学,含汇编示例)](https://www.sra.uni-hannover.de//////Lehre/WS25/L_BST/doc/x86-abi.html):volatile/callee-saved 划分、对齐动机、call/ret 栈行为
