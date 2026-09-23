# Ghidra SLEIGH 处理器规范语言

> Ghidra 之所以能支持几十种指令集，是因为它不写死反汇编器，而是用 **SLEIGH** 这门规范语言描述「指令字节怎么切、切成什么、语义是什么」。本 demo 实现 SLEIGH 的一个子集：`define token` / 字段 / `attach variables` / 构造函数位模式 / 解码，用一台玩具 ISA 把它跑通。

## 一、规范文件的骨架

```text
define endian=little;              # 必须是第一条
define alignment=1;
define space RAM type=ram_space size=2 default;
define register offset=0x00 size=1 [ A X Y ];

define token opbyte (8)            # 位宽必须是 8 的倍数
   op  = (0,7)
   aaa = (5,7)
   cc  = (0,1)
;

attach variables [ r1 ] [ reg0 reg1 reg2 reg3 ];

:halt    is op=0x00 { ... }         # 空标识符 = 根指令表
```

## 二、token 与字段

- **最低位标 0，区间是闭区间** `(lo, hi)`。
- **字段可以任意重复与重叠**，只要名字不同（6502 的 `op / aaa / bbb / cc` 就是重叠的）。
- **大于 1 字节的 token，位编号受字节序影响**：先把字节按字节序拼成整数，再从最低位编号。实测 16 位 token 取 `0x12 0x34`：

  | 字节序 | 整数 | `hi8=(8,15)` | `lo8=(0,7)` |
  | --- | --- | --- | --- |
  | big | `0x1234` | `0x12` | `0x34` |
  | little | `0x3412` | `0x34` | `0x12` |

  可以用 `define token instr (32) endian=little ...` 单独覆盖某个 token。

- 属性只有 `signed` / `hex` / `dec`，**默认按十六进制显示**（`dec` 目前不受支持）。`signed` 影响的是「字段取值的解释」与显示：`0xFE` 作为 signed 是 `-2`，显示成 `-0x2`。

## 三、`attach variables`

```text
attach variables fieldlist registerlist;
```

两侧**不要求等长**：`fieldlist` 里的**每一个**字段都会变成同一张寄存器查表（原文："For each field in the fieldlist ... the field becomes a look-up table for the given list of registers"）。索引从 0 起，字段全 0 就取第一个寄存器。`attach` 同时改掉字段的**显示**与**语义**。

## 四、构造函数：五段与位模式

```text
<table>: <mnemonic 与操作数>  is  <位模式>  [ <反汇编动作> ]  { <语义> }
```

- 表头为空标识符 = 根指令表；`instruction` 这个名字被保留给根表，但**不要**写在表头里（解析器靠空标识符区分助记符与操作数）。
- 位模式在 `is` 与 `{`/`[` 之间，由**约束** `field=value`、`&`、`|`、括号组成。
- **约束比较的是字段的原始整数编码**，即使 `attach` 给这个字段换了含义也一样（原文特别提醒，尤其是 `attach values` 的情况）。所以 `r1=1` 匹配的是字段值为 1 的那条指令，显示出来才是 `reg1`。
- 单独出现、不带 `=` 的标识符就是**操作数**：它把构造函数里的本地符号链接到同名的全局 family 符号（可以是字段，也可以是另一张表）。
- 变长指令靠 `...` / `;` 引入下一个 token：`:lda imm8 is op=0xA9 ... & imm8` —— 本 demo 里 `...` 会再吃掉第二个 token 的字节。

## 五、实测输出

```text
big    -> 整数 0x1234，hi8=0x12 lo8=0x34
little -> 整数 0x3412，hi8=0x34 lo8=0x12
0xFE 作为 signed 是 -2，显示成 -0x2
r1=0 -> R0   r1=1 -> R1   r1=2 -> R2   r1=3 -> R3
0x00 -> halt      0xea -> nop
0xfa -> inc R2    0xf7 -> lda R3     0xFF -> None
A9 99 -> lda（长度 2 字节，操作数 ['0x99']）
```

## 六、代码结构

| 文件 | 内容 |
| --- | --- |
| `python/sleigh.py` | `Token`（区间/bits/字节序/signed/显示）、`Spec`（定义与构造函数表）、`decode` |
| `python/sleigh_pattern.py` | 位模式的解析（`&` / `|` / 括号 / `...`）与求值 |
| `python/selfcheck_sleigh.py` | **67 条断言实跑全绿** |
| `python/main.py` | 玩具 ISA 的逐步演示 |
| `go/sleigh.go` + `go/main.go` | Go 侧同题实现（四项静态检查全过） |

自检覆盖：定义语句的唯一性与次序（`endian` 只能定义一次）、字段区间闭且不得越界、token 位宽必须是 8 的倍数、两种字节序下的位编号、per-token 覆盖、`signed` 与默认十六进制、`attach` 的索引从 0 与「两侧不等长」语义、`&`/`|`/括号的求值、约束按整数比（不认 attach 后的含义）、根表与子表、同模式时先定义者优先、`...` 的变长与字节不足、以及负控（残缺模式、未声明字段 attach、没有 token 就解码）。

## 参考资料（已读）

- [Ghidra `sleigh_definitions.html`](https://raw.githubusercontent.com/NationalSecurityAgency/ghidra/master/GhidraDocs/languages/html/sleigh_definitions.html) — `define endian` 必须第一条、`alignment`、`space`、`register`、`bitrange`
- [Ghidra `sleigh_tokens.html`](https://raw.githubusercontent.com/NationalSecurityAgency/ghidra/master/GhidraDocs/languages/html/sleigh_tokens.html) — `define token` 语法、位编号与字节序、属性、`attach variables` 的查表语义
- [Ghidra `sleigh_constructors.html`](https://raw.githubusercontent.com/NationalSecurityAgency/ghidra/master/GhidraDocs/languages/html/sleigh_constructors.html) — 五段结构、表头与保留名、约束、`&`/`|`、操作数链接、`...` 与 `;`
- [Ghidra `6502.slaspec`](https://raw.githubusercontent.com/NationalSecurityAgency/ghidra/master/Ghidra/Processors/6502/data/languages/6502.slaspec) — 真实规范样例（重叠字段 `op/aaa/bbb/cc`、`...` 的用法）
