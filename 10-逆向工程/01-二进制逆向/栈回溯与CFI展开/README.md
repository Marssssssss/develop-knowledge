# 栈回溯与 CFI 展开

> 没有额外的元信息，栈只是一串不知所指的字。`CFI`（Call Frame Information）就是那份"告诉你在函数体中间的第 N 个字节上，寄存器被存在哪里"的压缩表。本 demo 把它从头跑一遍：解析 `.eh_frame` → 执行 DW_CFA 程序 → 得到某一 pc 处的规则 → 逐帧回溯。

## 一、简介

x86-64 上常见的两种不用 CFI 的回溯方式都有硬伤：

- **帧指针链**：要求不省略帧指针（`-fno-omit-frame-pointer`），一旦省略就断链；
- **栈扫描**：对每个对齐的字去猜它是不是返回地址，代价高且容易误报。

CFI 的做法是：**由编译器为每个函数生成一段小程序**，描述"从函数入口到某个位置，CFA 怎么算、哪些寄存器被存到栈上的哪里"。运行时把它执行一遍，就能得到精确的恢复规则。

本 demo 覆盖：

1. `.eh_frame` 的 CIE / FDE 结构（LSB Core §10.6）
2. `DW_CFA_*` 指令解码（高两位主操作码 + 低 6 位内嵌操作数）
3. 按目标 pc 求解展开规则
4. 三帧合成栈的完整回溯
5. psABI Figure 3.36 寄存器编号与 Figure 3.37 指针编码

## 二、原理详解

### 2.1 CFA：整个体系的锚点

**CFA（Canonical Frame Address）定义为「调用者执行 call 指令前一刻的 `%rsp`」**。它同时是：

- 被调用者眼中"栈从这个地址开始"；
- **回溯后上一帧的 `%rsp`（这就是 `unwind_step` 里直接把 CFA 赋给 rsp 的原因，而不是去查 `%rsp` 自己的规则——`%rsp` 那列通常标记为 undefined）。

### 2.2 CIE 与 FDE（LSB §10.6.1）

`.eh_frame` 由若干 CFI 记录组成，**每条记录 = 1 个 CIE + 若干共享它的 FDE**，两者都对齐到寻址单位边界。

| CIE 字段 | 说明 |
| --- | --- |
| `Length` | 不含自身。`0xffffffff` 表示真长度在后面的 8 字节 Extended Length；`0` 表示 terminator，处理到此结束 |
| `CIE ID` | 恒为 0（LSB："This value shall always be 0"） |
| `Version` | 恒为 1 |
| `Augmentation String` | NUL 结尾；空串表示无 augmentation |
| `Code Alignment Factor` | ULEB，乘到所有 advance 位置的操作数上 |
| `Data Alignment Factor` | **SLEB**，乘到所有偏移操作数上；x86-64 上恒为 `-8` |
| `Return Address Register` | ULEB，多数架构上是 16（x86-64） |

| FDE 字段 | 说明 |
| --- | --- |
| `CIE Pointer` | **不是绝对地址**：用它自身的偏移减去它，得到关联 CIE 的起始偏移。永不为 0 |
| `PC Begin` / `PC Range` | 覆盖的地址区间，左闭右开 |
| `Call Frame Instructions` | 本函数专属的程序 |

augmentation 串四个字符：`z`（首字符，带来一个 ULEB 的 Augmentation Data Length）、`L`（LSDA 指针编码）、`P`（personality routine）、`R`（FDE 地址指针的非默认编码）。后三者都**只在 `z` 打头时才允许出现**。

> **`Data Alignment Factor` 为什么是 -8**：因为"往低地址增长、单位 8 字节"。于是 `DW_CFA_offset <reg>, 1` 的语义是 "`reg` 存在 `CFA-8`"，而 `2` 就是 `CFA-16`。所有 offset 类操作数都要乘它。

### 2.3 DW_CFA 指令：一字节里塞主操作码 + 操作数

```text
op & 0xC0 == 0x40  ->  DW_CFA_advance_loc,    低 6 位 = 位置增量
op & 0xC0 == 0x80  ->  DW_CFA_offset,         低 6 位 = 寄存器号,后跟 ULEB 偏移×data_align
op & 0xC0 == 0xC0  ->  DW_CFA_restore,        低 6 位 = 寄存器号
op <  0x40         ->  次级操作码（0x01 set_loc … 0x16 val_expression）
```

次要操作码取值：`set_loc=0x01`、`advance_loc1/2/4=0x02/0x03/0x04`、`offset_extended=0x05`、`restore_extended=0x06`、`undefined=0x07`、`same_value=0x08`、`register=0x09`、`remember_state=0x0a`、`restore_state=0x0b`、`def_cfa=0x0c`、`def_cfa_register=0x0d`、`def_cfa_offset=0x0e`、`def_cfa_expression=0x0f`、`expression=0x10`、`offset_extended_sf=0x11`、`def_cfa_sf=0x12`、`def_cfa_offset_sf=0x13`、`val_offset=0x14`、`val_offset_sf=0x15`、`val_expression=0x16`、`GNU_args_size=0x2e`。

两个被啃过的细节：

- `DW_CFA_def_cfa` 的偏移是**未被折算**的绝对值（不走 data_align），而 `def_cfa_sf` / `def_cfa_offset_sf` / `offset_*` 三类都走。写成一致会在 data_align 恰好为 1 的用例上通过、在 -8 上全错。
- `DW_CFA_GNU_args_size` 记录"call 之前为参数预留了多少字节"。psABI §3.7 原文：throwing 时要用它调整栈帧，才能正确跳到异常处理例程。它与回溯本身无关，但常被误当成普通地址偏移。

### 2.4 返回地址列：一个不是寄存器的"寄存器"

psABI Figure 3.36 给 x86-64 的编号里有一条特殊项：

| Register | Number |
| --- | --- |
| `%rax` / `%rdx` / `%rcx` / `%rbx` | 0 / 1 / 2 / 3 |
| `%rsi` / `%rdi` / `%rbp` / `%rsp` | 4 / 5 / 6 / 7 |
| `%r8`–`%r15` | 8–15 |
| **Return Address RA** | **16** |
| `%xmm0`–`%xmm7` | 17–24 |

脚注 29 原文：**"The table defines Return Address to have a register number, even though the address is stored in `0(%rsp)` and not in a physical register."**

这就是 CFI 里"`RA` 存在 `CFA-8`"这条规则能写出来的原因——它借用了寄存器编号空间来表达一个内存槽。

### 2.5 指针编码（Figure 3.37）

`.eh_frame_hdr` 的三个 `_enc` 字段与 FDE 的地址指针都按这张掩码表编码：

| 掩码 | 含义 |
| --- | --- |
| 0x1 | uleb128 / sleb128（视 0x8 而定） |
| 0x2 | udata2 / sdata2 |
| 0x3 | udata4 / sdata4 |
| 0x4 | udata8 / sdata8 |
| 0x8 | 有符号 |
| 0x10 | PC 相对 |
| 0x20 | text 段相对 |
| 0x30 | **data 段相对（0x20 与 0x10 的叠加，不是新编码）** |
| 0x40 | 函数起始相对 |

默认值 0 = "direct 4-byte absolute pointers"。**0x08 是叠加在 0x1–0x4 之上的标志位**，所以 `sdata4` 是 `0x0b` 而不是某个独立取值——拿 `byte & 0x0f` 去分派会在所有有符号编码上失败。

### 2.6 「跑到第 pc 个字节」的边界

CFI 的规则在**advance 之后**的地址生效。因此：

```text
loc = pc_begin
for ins in fde.instructions:
    if ins 是 advance_*:
        if loc + delta > pc: break          # 整条不生效
        loc += delta
    else:
        应用规则
```

写成"先应用再回退 loc"是错的：那一刹那规则已经被改掉了。典型症状是"函数入口刚执行完 `push %rbp` 的位置"被算成已经建立了帧指针。

## 三、对比

| 维度 | `.eh_frame` | `.debug_frame` |
| --- | --- | --- |
| 所属 | LSB Core 规范 | DWARF 标准 |
| 是否可丢弃 | 不可（`strip` 也不能删，否则异常不可用） | 可随调试信息一并剥离 |
| 差异 | LSB 明说 "there are some subtle difference, and care should be taken when comparing the two sections" | —— |
| 典型 extras | `DW_CFA_GNU_args_size`、`augmentation` z/L/P/R | 无 |

**关于 CIE ID 的一处规范冲突**：LSB §10.6.1.1 写 "This value shall always be 0"；而 psABI §3.6.2 在讲位置无关的扩展时写 "frames using this extension of the DWARF standard must set the CIE identifier tag to **1**"。本 demo 采用 LSB 口径（0 判为 CIE、非 0 判为 FDE）；遇到 64 位 DWARF 格式（`0xffffffff` 长度）时的判别口径与之一致。

## 四、环境

- Python 3.8+（标准库 `struct`）
- C99 编译器（`cfi_unwind.c` 只用 `<stdint.h>` / `<stdio.h>` / `<string.h>`）
- 无需真实二进制：全部输入都是手搓的合成 CFI

## 五、运行方式

```bash
python cfi_check.py     # 8 组断言，含三帧回溯
gcc -std=c99 -Wall -o /tmp/cfi cfi_unwind.c && /tmp/cfi
```

自检里的三帧栈是手工算过的：

```text
outer  :  entry_rsp = 0x7fffffffe108  →  push %rbp  →  rbp_O = 0x7fffffffe100
middle :  CFA_M = 0x7fffffffe100 (= rbp_O)  保存的 rbp 在 CFA-16，返回地址在 CFA-8
inner  :  CFA_I = 0x7fffffffe0f0 (= rbp_M)
```

**"被调用者的 CFA 等于调用者的 `%rbp`"** 这条性质是 `push %rbp; mov %rsp,%rbp` 序言的直接后果，也是帧指针链在省略帧指针时失效、而 CFI 不失效的根本差别。

## 六、关键代码

```python
# build_row_to_pc：越过 pc 的那条 advance 必须整条不生效
for ins in decode_instructions(fde.instructions, cie):
    if ins[0] in advance_ops:
        if row.loc + ins[1] * cie.code_align > pc:
            break
        row.loc += ins[1] * cie.code_align
    else:
        row = apply_one(row, ins, cie, initial)
```

```c
/* C 侧同逻辑：先试算到一个副本上，确认没越界再落回去 */
struct row probe = *out;
const uint8_t *q = p;
if (apply_one(&probe, &q, cie, initial) != 0) return -1;
if (probe.loc > pc) break;      /* 这一步连同它的规则一起作废 */
*out = probe; p = q;
```

## 七、性能边界

- **查 FDE 是 O(n) 线性扫描**。真实做法是 `.eh_frame_hdr` 的 binary search table（`initial location` + `address` 二元组，排好序做二分）；当 `table_enc` 为 `DW_EH_PE_omit` 时该表不存在，只能退化到线性扫描。
- **每个 pc 都要重跑一遍 CFI 程序**。glibc / libunwind 的做法是**区间缓存**：以 advance 边界为断点缓存 Row，相邻 pc 直接复用。
- **展开本身是 O(帧数 × 寄存器数)**：每一帧要把所有 callee-saved 寄存器按规则读一遍。异常传播时这是热路径，但对 crash handler / profiler 而言可接受。
- 本 demo 的 `unwind_step` 每次构建完整 Row 而不缓存，用于把规则边界讲清楚，**不是性能示范**。

## 八、注意事项与常见坑

1. **`Data Alignment Factor` 是 SLEB 且通常为负数**。忘了乘它，所有 `offset` 规则会从"`CFA-8`"变成"`CFA+1`"，看起来"能跑但全错"。
2. **`def_cfa` 不折算、`def_cfa_sf` 折算**。二者混用会在 data_align = -8 时立刻暴露——把第二个参数写成 `-8` 而不是 `-1`。
3. **先跑 CIE 的 initial instructions**。跳过它，`DW_CFA_restore` 就没有还原目标。
4. **`%rsp` 的规则通常是 undefined**：新 `%rsp` 必须直接取 CFA，而不是查规则。这是本 demo 里最容易被"顺手写成统一处理"的地方。
5. **`Return Address` 有编号但不是寄存器**（编号 16，存在 `0(%rsp)`）。把它当物理寄存器去索引寄存器表会越界或读到垃圾。
6. **指针编码的 `0x08` 是标志位不是编码**：要用 `format & 0x07` 分派。
7. **`.eh_frame` 不能被 strip 掉**：它不是"调试信息"，而是 ABI 的一部分（`strip` 只删 `.symtab`/`.debug_*`）。剥掉了它，程序仍能跑但异常会直接 `std::terminate`。
8. **LSB 与 psABI 对 CIE ID 的说法不一致**（0 vs 1），实现时先确定采用哪套，并在注释写清。

## 九、参考资料（本轮实测下载并提取正文）

- [Linux Standard Base Core Specification, Generic Part 5.0.0 §10.6 Exception Frames](https://refspecs.linuxbase.org/LSB_5.0.0/LSB-Core-generic/LSB-Core-generic/ehframechpt.html) — CIE/FDE 字段清单、`z`/`L`/`P`/`R` augmentation 含义、`.eh_frame_hdr` 布局、CIE ID 恒为 0 的原文
- [System V AMD64 psABI Draft 0.99.6](https://refspecs.linuxfoundation.org/elf/x86_64-abi-0.99.pdf) — §3.6.2 Figure 3.36 寄存器编号（含脚注 29 关于 RA 的说明）与 Figure 3.37 指针编码掩码；§3.7 Stack Unwind Algorithm（`GNU_ARGS_SIZE` 0x2e 的用途、位置无关扩展下 CIE identifier tag = 1）
- [binutils `include/dwarf2.def`](https://sourceware.org/git/?p=binutils-gdb.git;a=blob_plain;f=include/dwarf2.def) — `DW_CFA_*` 操作码取值（`0x40`/`0x80`/`0xc0` 三个主操作码族与次级操作码）
- [DWARF5 §6.4 Call Frame Information](https://dwarfstd.org/doc/DWARF5.pdf) — CFA 的定义与各类 register rule 的语义
