# ARM64 指令集与调用约定

> 从「固定 32 位定长编码」入手还原 A64 指令,再把 AAPCS64 的寄存器分工、栈对齐与帧记录
> 链落成可断言的模型。x86-64 的变长指令要写 length disassembler(见 `x86-64指令编码`),
> A64 的 4 字节对齐边界**天然就是指令边界**,这是两者在逆向工程里最大的工程差异。

## 一、简介

AArch64(A64)是 Armv8-A 起的 64 位执行状态指令集。它对逆向工程的意义在于三点:

1. **定长 4 字节**:不需要「试探解码 + 回退」的指令长度恢复算法,反汇编器可以单遍线性扫描;
2. **寄存器分工写在 ABI 里而非指令里**:同一个物理寄存器在「传参 / 临时 / 被调用者保存」
   三种角色间切换完全靠约定,因此判断一个函数的行为必须先读 AAPCS64;
3. **编译器输出的序言形态高度模板化**:`stp x29, x30, [sp, #-16]! ; mov x29, sp` 这一对
   几乎是「函数边界」的同义句,是函数识别与栈回溯的锚点。

## 二、原理详解

### 2.1 定长编码:组标识位在 [28:24] / [28:23]

A64 指令的最高若干位是**组选择位**,处理器在解码早期就能定出指令大类:

| 组 | 关键位域 | 本 demo 覆盖的指令 |
| --- | --- | --- |
| 加/减(立即数) | `sf op S 10001 sh(23:22) imm12(21:10) Rn Rd` | `add/sub/adds/subs` |
| 加/减(移位寄存器) | `sf op S 01011 shift(23:22) 0 Rm imm6 Rn Rd` | `add/sub/subs` |
| 逻辑(移位寄存器) | `sf opc(30:29) 01010 shift N(21) Rm imm6 Rn Rd` | `and/bic/orr/orn/eor/eon/ands/bics` |
| 宽立即数搬移 | `sf opc(30:29) 100101 hw(22:21) imm16(20:5) Rd` | `movn/movz/movk` |
| 分支(立即) | `000101`/`100101` + `imm26` | `b/bl` |
| 分支(寄存器) | `1101011 opc(24:21) 11111 000000 Rn 00000` | `br/blr/ret` |
| 条件分支 | `0101010 0 imm19(23:5) 0 cond(3:0)` | `b.<cond>` |
| 载入/存储 pair | `opc(31:30)=10 101 V(26) 0 idx(24:23) L(22) imm7 Rt2 Rn Rt` | `stp/ldp` |
| 载入/存储(偏移) | `size(31:30) 111 V 01 opc(23:22) imm12 Rn Rt` | `str/ldr` |

两个容易记混的细节,本 demo 用断言钉死:

* **加/减组的 S 位是独立的 bit29**,而**逻辑组的 S 变体是 opc=11**(没有独立 S 位)。
  所以 `sub`→`subs` 只需翻 bit29,而 `orr`→`orrs` 这个组合在 A64 里不存在。
* **SP 与 XZR 共用寄存器号 31**,是哪个由指令语义决定:`ADD Xd, SP, #0` 里 31 是 SP,
  `ORR Xd, XZR, Xm` 里 31 是 XZR。

### 2.2 别名折叠:反汇编输出比编码更"骗人"

`mov` 在 A64 里是伪指令:`mov x29, sp` = `add x29, sp, #0` = `0x910003FD`;
`mov x0, x1` = `orr x0, xzr, x1` = `0xAA0103E0`;`ret` = `ret x30` = `0xD65F03C0`。
逆向时**不要拿 mnemonic 做特征**,要拿 opcode 位域或字节(这正是 `YARA静态特征匹配` 那类
工具在 AArch64 上必须显式展开别名的原因)。

### 2.3 AAPCS64:寄存器分工与栈

| 寄存器 | 角色 | 调用后是否保持 |
| --- | --- | --- |
| x0-x7 | 参数 / 结果(32 位实参用 W 视图) | 否(caller-saved) |
| x8 | 间接结果位置 XR(结构体返回的缓冲区指针) | 否 |
| x9-x15 | 临时寄存器 | 否 |
| x16/x17 | IP0/IP1,链接器插 veneer 时用 | **否,且可能在进入函数途中就被破坏** |
| x18 | 平台寄存器 PR,平台无关代码应避免占用 | 平台相关 |
| x19-x28 | 被调用者保存 | **是** |
| x29 / x30 | FP / LR | 是(经帧记录) |
| SP | 栈指针 | 是(且任意经 SP 访存时 SP mod 16 = 0) |

要点:

* **第 9 个整型实参起入栈**(`x0-x7` 只有 8 个槽);
* C++ 成员函数的隐式 `this` 占 **X0**,显式参数从 X1 起;
* 结构体返回时 **X8 = XR** 指向调用者分配的缓冲区,它**不占参数位**;
* **NZCV 不跨调用保留** —— 所以「调用点前后的条件跳转」必须各自重新 `cmp`;
* **叶子函数不需要保存 LR**:它不执行 `bl`,X30 不会被覆盖。

### 2.4 帧记录(frame record)与栈回溯

AAPCS64 规定每个栈帧用**两个 64 位值**链接到调用者:

```
       高地址
      +----------------+  <- 调用者的 SP
      |  LR(进入时的 X30) |  高地址槽 = 本函数入口时的返回地址
      +----------------+
      |  前一帧记录指针   |  低地址槽 = 调用者的 FP
      +----------------+  <- FP(x29) 指向这里;链以地址 0 结束
```

因为 SP 恒 16 字节对齐、帧记录恒 16 字节,**帧记录自身也 16 字节对齐**。
有帧指针时回溯只需跟着低地址槽走;开了 `-fomit-frame-pointer` 后链断裂,
只能依赖 DWARF `.eh_frame`(本 demo 用「链中间出现 0」演示这种提前终止)。

## 三、x86-64 对照

| 维度 | x86-64 | AArch64 |
| --- | --- | --- |
| 指令长度 | 1~15 字节变长,需 length disassembler | 恒 4 字节,对齐即边界 |
| 返回地址 | `call` 自动压栈 | `bl` 写入 X30,由被调用者自己决定是否入栈 |
| 序言 | `push rbp; mov rbp, rsp` | `stp x29, x30, [sp, #-16]!; mov x29, sp` |
| 一次存两个寄存器 | 无(需两条 push) | `stp`/`ldp` 一条,且可带前/后变址 |
| 参数寄存器 | rdi/rsi/rdx/rcx/r8/r9 共 6 个 | x0-x7 共 8 个 |
| 大立即数 | `movabs` 直接放 64 位 | 无单条 64 位立即数,靠 `movz/movk` 拼或 `adrp`+`add` 页内寻址 |

## 四、环境与运行

无第三方依赖;无需 ARM64 硬件或交叉工具链(全部在模型层验证)。

```bash
python prologue_shape.py     # A64 解码 + 序言形态(8 条真实清单逐条断言)
python aapcs64_check.py      # AAPCS64 寄存器分工 / 参数分配 / 帧链回溯
cc -std=c99 -O2 -o arm64_decode arm64_decode.c && ./arm64_decode   # C 同题实现
```

## 五、关键代码

* `arm64_decode.py::decode()` —— 组识别 → 字段抽取 → 别名折叠,返回结构化的 decode 结果;
* `arm64_decode.py::decode_bytes()` —— 直接按 4 字节切分机器码(定长的收益);
* `prologue_shape.py::classify_prologue()` —— `stp-frame-pair` / `stp-pair-only` /
  `str-lr-preindex` / `leaf` 四形态判定,是「函数边界 + 是否建帧链」的快速分类器;
* `aapcs64_check.py::assign_integer_args()` / `walk_frames()` —— 参数落位与帧链回溯。

## 六、性能边界

* 解码是纯位运算,单条指令 O(1);本 demo 的线性扫描在百万条指令量级仍是毫秒级。
* 帧链回溯是 O(depth) 且**依赖代码保留了帧指针**;`-O2` 默认省略帧指针,实测回溯深度
  会退化为 0(链断),此时必须回退到 `.eh_frame`/`.debug_frame`。
* 本 demo 的 `imm` 字段按真实指令语义做了符号扩展与 scale(如 pair 的 imm7×8),
  不做这一层缩放会得到 8 倍偏差 —— 这是手写解码器最常见的错。

## 七、注意事项与常见坑

1. **idx 字段值**:pair 的寻址方式在 `[24:23]`,**11 = pre-index、01 = post-index**,
   不是直觉上的「10 = 偏移」。本 demo 首版就写反了,靠断言捕获。
2. **XZR 与 SP 同号**:据语义判断,不能只看寄存器号。
3. **逻辑组没有独立 S 位**:按 bit29 加 "s" 会把 `orr` 变成不存在的 `orrs`。
4. **别名会掩盖真实编码**:做特征/指纹时必须解到 opcode;
5. **x16/x17 在「进入函数之前」就可能被破坏**,不能把 IP0/IP1 当普通临时寄存器跨调用保存;
6. **32 位实参用 W 视图**:把 `mov w0, #0` 的返回值读成 X0 高半部会读到残留值;
7. 本 demo 未覆盖浮点/SIMD(HFA 传参)、原子/内存序指令与被截断的
   「参数传递规则 C 组(>16 字节复合类型)」——AAPCS64 该节在抓取时被截断,故不作断言。

## 八、参考资料(已读)

* [Learn the architecture — A64 Instruction Set Architecture Guide 102374 1.3, Procedure Call Standard](https://developer.arm.com/documentation/102374/0103/Procedure-Call-Standard) —— 寄存器角色表、XR/IP0/IP1/PR/FP/LR、ALU flags 不保留
* [Arm A-profile A64 ISA — Index by Encoding: Data Processing -- Register(ddi0602)](https://developer.arm.com/documentation/ddi0602/2023-03/Index-by-Encoding/Data-Processing----Register) —— `sf/op/S/shift/Rm/imm6/Rn/Rd` 位号与各 opc 组合的指令表
* [AAPCS64 — Procedure Call Standard for the Arm 64-bit Architecture(aapcs64.rst)](https://raw.githubusercontent.com/ARM-software/abi-aa/main/aapcs64/aapcs64.rst) —— Table 2 寄存器角色、SP mod 16 = 0、帧记录两 64 位布局与链终止条件
* [Porting to 64-bit ARM(Arm 白皮书, 2014)](http://classweb.ece.umd.edu/enee447.S2019/ARM-Documentation/Porting%20to%20ARM%2064-bit%20whitepaper.pdf) —— 8 个参数寄存器、X8 间接结果位置、X1:X0 返回 128 位
* [CH1.5 — Hello, World! (Part 2)](https://v3nn00m.github.io/posts/re4b/chapter1_5_1_6_part2) —— 本 demo 8 条 golden 指令的真实 GCC 4.8.1 ARM64 反汇编清单
* [resurgo — 静态函数恢复库(序言形态分类)](https://pkg.go.dev/github.com/maxgio92/resurgo@v0.2.0) —— `stp-frame-pair` / `str-lr-preindex` / `leaf` 等形态命名
* [AArch64 Assembly Cheat Sheet — Kenjiro Taura](https://taura.github.io/programming-languages/html/arm64_assembly_cheat_sheet.html) —— `stp/ldp` 前/后变址与序言尾声模板
