# x86-64 指令编码与解码（ModRM / SIB / REX）

## 简介

x86-64 是**变长指令集**：一条指令最长 15 字节，最短 1 字节。反汇编器（objdump / Capstone / Ghidra）的核心工作就是"从字节流里正确切出指令边界并还原操作数"——这一步错了，后面所有分析全崩。本 demo 手写一个最小解码器，把 `ModRM`/`SIB`/`REX` 的位字段拆开，输出人类可读的操作数。

关键概念：

- **Legacy prefix**：指令最前面的 1-4 字节修饰前缀（LOCK / REP / 段覆盖 / 66 / 67）
- **REX prefix**：0x40-0x4F，`0100WRXB`，64 位模式下扩展寄存器编号与操作数宽度
- **ModRM**：1 字节，`mod(2) reg(3) rm(3)`，决定"第二操作数"是寄存器还是内存
- **SIB**：1 字节，`scale(2) index(3) base(3)`，编码 `[base + index*scale]` 寻址
- **RIP 相对寻址**：`mod=00, rm=101` 时操作数是 `RIP + disp32`（64 位模式特有）

历史背景：x86 从 16 位（8086）到 32 位再到 64 位，编码格式是**层层加前缀扩展**而非重新设计（AMD 在 2000 年设计 AMD64 时为了兼容性只能"借用"0x40-0x4F 这个在 32 位下无意义的字节段作 REX），这就是为什么今天解码器要实现得这么啰嗦。

## 原理详解

### 1. 指令总格式

```
+--------------+-----------+--------+-------+------+-------+-----+
| legacy prefix| REX       | opcode | ModRM | SIB  | disp  | imm |
| 1-4 bytes    | 0-1 byte  | 1-3 B  | 0-1 B | 0-1 B|0/1/2/4|0/1/2|
| (optional)   | (opt)     |(req.)  | (opt) |(opt) | /8 B  | /4/8|
+--------------+-----------+--------+-------+------+-------+-----+
 低地址  ──────────────────────────────────────────────►  高地址
```

解码顺序即内存顺序，任何一段缺失都不留空洞——**变长的根源是只有 opcode 必填，其余六段都是"看情况"**。

### 2. Legacy prefix 分四组

| 组 | 前缀 | 含义 |
| --- | --- | --- |
| 1 | `F0` LOCK / `F2` REPNE / `F3` REP(E) | 原子性 / 串操作重复 |
| 2 | `2E`CS `36`SS `3E`DS `26`ES `64`FS `65`GS | 段覆盖；`2E`/`3E` 在 Jcc 上兼作分支提示 |
| 3 | `66` | 操作数尺寸覆盖（64 位模式下：32↔16 位） |
| 4 | `67` | 地址尺寸覆盖（64 位模式下：64→32 位寻址） |

同一组内**最后一个生效**（如 `F2 F3` 视为 `F3`），不同组可任意叠加。`F3` 还常被当作 SSE 指令的"强制前缀"（mandatory prefix），例如 `F3 0F 10` = `movss`。

### 3. REX prefix（0x40-0x4F）

```
 7   6 5 4   3   2   1   0
0100  W   R   X   B
```

| 位 | 名称 | 作用 |
| --- | --- | --- |
| W | 操作数宽度 | 1 = 64 位操作数（`mov rax, rbx`）；0 = 默认 |
| R | reg 扩展 | 把 ModRM.reg 从 3 位扩到 4 位（r8-r15 可被寻址） |
| X | index 扩展 | 把 SIB.index 扩到 4 位 |
| B | base 扩展 | 把 ModRM.rm / SIB.base / opcode 内嵌寄存器扩到 4 位 |

**关键坑**：REX 必须是**紧邻 opcode 的最后一个前缀**。如果 REX 之后又出现 legacy prefix，REX 被丢弃作废。

### 4. ModRM 字节

```
 7 6   5 4 3   2 1 0
 mod    reg     rm
```

| mod | 含义（rm 为内存时） |
| --- | --- |
| `00` | `[rm]`，无位移；**例外** rm=101 → `[RIP + disp32]`，rm=100 → 有 SIB |
| `01` | `[rm + disp8]`（disp8 有符号，-128..127） |
| `10` | `[rm + disp32]` |
| `11` | rm **不是内存而是寄存器**，无 SIB 无位移 |

`reg` 字段有三种身份：① 通用寄存器编号（参与运算）；② opcode 的**扩展操作码**（如 `FF /2` = `call`）；③ 多余位（如 `E8` 的 `reg` 无意义）。解析时必须结合 opcode 表判定。

### 5. SIB 字节（仅当 mod≠11 且 rm=100）

```
 7 6   5 4 3   2 1 0
scale  index   base
```

- `scale`：0→1，1→2，2→4，3→8
- `index=100`（且 REX.X=0）：**无 index**（这是"没有变址"的编码方式，不是用 rsp 当变址）
- `base=101` 且 `mod=00`：无 base，改用 `disp32` 绝对地址
- 地址 = `base + index * scale + disp`

### 6. RIP 相对寻址（64 位模式）

`mod=00, rm=101` 时，操作数地址 = **下一条指令的地址** + 符号扩展后的 disp32。这是 x86-64 与 32 位最大的编址差异：位置无关代码（PIE）靠它消掉绝对地址重定位。注意基准是 `RIP of next instruction`，即当前指令起始 + 指令总长度。

## 对比 / 选型

| 方案 | 优点 | 缺点 | 适用 |
| --- | --- | --- | --- |
| 手写表驱动解码（本 demo） | 无依赖、可控、可读 | 只覆盖小指令子集；踩错表就错 | 教学 / 极小工具 |
| Capstone | 覆盖全、多架构、API 稳定 | 体积大、需链接 C 库 | 生产级反汇编 |
| libopcodes (binutils) | 与 objdump 完全一致 | API 晦涩、文档差 | 与 binutils 对齐的场景 |
| 线性扫描 vs 递归下降 | 前者快 | 前者把数据当代码 | 见 `控制流图与结构化反编译` demo |

## 环境准备

- 操作系统：Linux / macOS / WSL（Windows 原生亦可编译 C）
- C：gcc ≥ 9 或 clang ≥ 10
- Python：3.8+（仅标准库，无第三方依赖）
- 建议配 `objdump -d` 做对拍

## 运行方式

### C

```bash
gcc -O2 -Wall -Wextra -pedantic modrm_decoder.c -o modrm_decoder
./modrm_decoder
```

### Python

```bash
python3 insn_decoder.py
```

## 关键代码片段

```c
/* modrm_decoder.c —— 解析 ModRM 与 SIB，还原操作数字符串 */
typedef struct { uint8_t mod, reg, rm; } ModRM;

static ModRM parse_modrm(uint8_t byte) {
    ModRM m = { (byte >> 6) & 0x3, (byte >> 3) & 0x7, byte & 0x7 };
    return m;
}

/* 返回：消耗的额外字节数；把操作数写进 out */
static int decode_rm(const uint8_t *p, uint8_t rex, ModRM m,
                     uint8_t reg_width, char *out, size_t n) {
    if (m.mod == 3) {                    /* 寄存器直接寻址，无 SIB/disp */
        snprintf(out, n, "%s", reg_name(reg_width, m.rm | ((rex & REX_B) ? 8 : 0)));
        return 0;
    }
    if (m.rm == 4) {                     /* 有 SIB 字节 */
        uint8_t sib = p[0];
        int scale = 1 << ((sib >> 6) & 3);
        int index = ((sib >> 3) & 7) | ((rex & REX_X) ? 8 : 0);
        int base  = (sib & 7) | ((rex & REX_B) ? 8 : 0);
        /* index==4(且无 REX.X) 表示"无变址"；base==5 且 mod==0 表示"无基址+disp32" */
        ...
        return 1 + disp_bytes(m.mod);
    }
    if (m.mod == 0 && m.rm == 5) {       /* RIP 相对：RIP + disp32 */
        int32_t d; memcpy(&d, p, 4);
        snprintf(out, n, "[rip + 0x%x]", d);
        return 4;
    }
    /* 普通 [base + disp8/disp32] */
    ...
}
```

对应"原理详解"第 4、5、6 步：`mod==3` 短路、`rm==4` 走 SIB、`mod==0 && rm==5` 走 RIP 相对——**这三个分支的顺序不能换**，因为 RIP 相对的判定必须先于"rm 当普通基址寄存器"。

## 性能与边界

- 单条指令解码 O(1)（字段数固定），指令长度上界 15 字节由 Intel SDM Vol.2 §2.1 规定
- 本 demo 只覆盖整数指令子集（MOV/ADD/SUB/CMP/CALL/RET/NOP/FF 组），**不支持** VEX/EVEX、3DNow!、SSE 全部 mandatory prefix 组合
- 未实现 `66`/`67` 引发的宽度切换与 32 位地址截断

## 注意事项与常见坑

1. **REX 被后续前缀作废** —— 现象：`48 66 89 c0` 解出来不是 64 位 mov。原因：REX 必须是 opcode 前最后一个前缀。规避：遇到 legacy prefix 时把已解析的 REX 清零。
2. **`index=100` 不等于 rsp 变址** —— 现象：把 `[rax+rsp]` 和"无变址"混淆。原因：rsp（编码 4）被保留为"无 index"哨兵值，`[rsp]` 必须经 SIB 且 index 位写 100。规避：判定后直接标记 no_index。
3. **RIP 相对基准是下一条指令** —— 现象：disp32 加到了当前指令地址上，偏移全错。原因：RIP 在指令执行时已指向下一条。规避：先算出指令总长度再算地址。
4. **disp8 是有符号的** —— 现象：`-1` 显示成 `255`。原因：忘做符号扩展。规避：`int8_t` 读取后再提升。
5. **指令长度没有自描述字段** —— 现象：流式解码越界读。原因：必须先在内存里完整读入再逐段消费。规避：解码函数返回消耗字节数，并做边界检查。

## 参考资料（实际阅读过的权威来源）

- [X86-64 Instruction Encoding — OSDev Wiki](https://wiki.osdev.org/X86-64_Instruction_Encoding) —— 指令总格式、四组 legacy prefix 表、REX/ModRM/SIB 位字段表、RIP 相对寻址与 16 位寻址对比，本 README 第 1-6 步的字段表全部来自此页
- [Intel 64 and IA-32 Architectures Software Developer's Manual, Vol.2 Chapter 2 "Instruction Format"](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html) —— 15 字节上限、ModRM/SIB 语义的规范定义
- [x86 instruction listings — felixcloutier.com](https://www.felixcloutier.com/x86/) —— Intel SDM 指令页的可检索镜像，用于核对 opcode 表与操作数宽度
