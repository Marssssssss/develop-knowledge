# DWARF 调试信息解析

> 把 `.debug_info` / `.debug_abbrev` / `.debug_line` 的字节流还原成「结构与源码坐标」。本 demo 手搓合成数据，逐实现对 DWARF5 规范的对照：**数据是假的，格式是真的**。

## 一、简介

DWARF 不是一张表，而是**三层嵌套的压缩结构**：

| 层 | 所在节 | 回答的问题 |
| --- | --- | --- |
| 编译单元头 | `.debug_info` | 这套调试信息用的版本、地址宽度、去哪找缩写表 |
| 缩写表 | `.debug_abbrev` | 每种 DIE 有哪些属性、每个属性怎么编码 |
| DIE 树 | `.debug_info` | 函数/变量/类型的层级与属性取值 |
| 行号程序 | `.debug_line` | 机器地址 ↔ 源码（文件、行、列）的映射 |

之所以要这么绕：直接存"每条语句一行"的表会大到无法接受，于是 DWARF 用 **LEB128 变长整数**压缩数值、用**缩写表**去掉重复的 tag/form 组合、把行号表编成**一台有限状态机的程序**由消费端跑一遍还原。

本 demo 覆盖：DWARF5 initial length、CU 头（含 DWARF5 新增字段）、缩写表、DIE 取值（含最容易理解错的 `DW_FORM_implicit_const`）、LEB128、以及行号程序的完整状态机。

## 二、原理详解

### 2.1 一切从 LEB128 开始（§7.6）

每个字节的**低 7 位**放数据、**最高位**是 continuation bit（还有后续字节），字节排列是 little-endian：

- `0` → `0x00`（注意：0 也占一个字节，不是零字节）
- `128` → `0x80 0x01`
- SLEB128 额外约定：**终止字节的第 6 位（0x40）是符号位**。`-1` 因此只需一个字节 `0x7f`。

这一条直接决定了后面所有"变长字段"的读法，也解释了为什么 `-1` 比 `1` 便宜。

### 2.2 initial length：32 位与 64 位 DWARF（§7.4）

```text
先读 4 字节 first
  first != 0xffffffff  ->  长度就是 first，后面所有 section offset 都是 4 字节
  first == 0xffffffff  ->  64 位 DWARF，真长度在紧接着的 8 字节里，offset 都是 8 字节
```

关键点是**它同时决定了两件事**：单元长度、以及之后所有 `section offset` 类字段的宽度。只看长度不看宽度会把 `.debug_abbrev` 偏移读错。

### 2.3 编译单元头（§7.5.1.1）

DWARF5 的 CU 头比 DWARF4 多了 `unit_type`：

| 序 | 字段 | 宽度 | 说明 |
| --- | --- | --- | --- |
| 1 | `unit_length` | 4 或 12 | 初长度，不含自身 |
| 2 | `version` | uhalf | **必须等于 5** |
| 3 | `unit_type` | ubyte | DWARF5 新增，见 Table 7.2 |
| 4 | `address_size` | ubyte | 目标机地址宽度 |
| 5 | `debug_abbrev_offset` | 4 或 8 | 指向本 CU 的缩写表 |

Table 7.2（节选）：`DW_UT_compile=0x01`、`DW_UT_type=0x02`、`DW_UT_partial=0x03`、`DW_UT_skeleton=0x04`、`DW_UT_split_compile=0x05`、`DW_UT_split_type=0x06`。

注意 `unit_type` 的定位意义：**多个 CU 可以共用同一张缩写表**（§7.5.3 原文 "multiple compilation units may share the same abbreviations table"），所以 `debug_abbrev_offset` 是必需的间接层，而不是"永远从 0 开始"。

### 2.4 缩写表（§7.5.3）

`.debug_abbrev` 的每个声明形如：

```text
ULEB code   ULEB tag   ubyte DW_children   [ (ULEB attr, ULEB form) ... ]   (0,0)
```

整张表以 `code == 0` 结束。`code == 0` 另有用途：在 `.debug_info` 里它表示 **null DIE**（兄弟链表的终止标记）。

**最容易读错的一点是 `DW_FORM_implicit_const`（0x21）**：它的值写在**缩写表里**（紧跟 form 的一个 SLEB），**DIE 侧一个字节都不占**。例：

```text
code 2 | DW_TAG_base_type | no children | (DW_AT_byte_size, DW_FORM_implicit_const, 8)
```

意思是"这条 base_type 的 byte_size 恒为 8"——每个 DIE 都省下一个字节。如果照普通 form 去 DIE 里读一个字节，后面所有属性都会整体错位。

### 2.5 行号程序：一台会"溢出进位"的状态机

#### 头部（§6.2.4）

按序 13 项；特别注意两条：

- `version` 是**行号信息自己的版本号**，规范明说它 "is independent of the DWARF version number"。
- `header_length` 是"**从 header_length 字段之后**到程序首字节的字节数"。把它当成"读完最后一个已知字段还剩多少"会整体错位——这是本 demo 自检里唯一一次真报错的位置。

#### 特殊操作码（§6.2.5.1）

一字节取值范围 ≥ `opcode_base` 的都叫特殊操作码，一次做七件事：加行号 → 推进地址/操作指针 → 追加一行 → 清 `basic_block` → 清 `prologue_end` → 清 `epilogue_begin` → 清 `discriminator`。

规范给的原文公式：

```text
opcode = (desired line increment - line_base)
         + (line_range * operation advance) + opcode_base

adjusted opcode    = opcode - opcode_base
operation advance  = adjusted opcode / line_range
line increment     = line_base + (adjusted opcode % line_range)

new address  = address + minimum_instruction_length
                          * ((op_index + operation advance)
                             / maximum_operations_per_instruction)
new op_index = (op_index + operation advance) % maximum_operations_per_instruction
```

`maximum_operations_per_instruction == 1` 时 `op_index` 恒为 0，公式退化成 DWARF v3 及以前的样子 —— 这也是为什么绝大多数 x86 实际样本里看不出 `op_index` 的价值。

三个常被误解的操作码：

| 操作码 | 值 | 易错点 |
| --- | --- | --- |
| `DW_LNS_const_add_pc` | 0x08 | 无操作数，推进量等于"特殊操作码 **255**"的那一份，不是 0xff 字节数 |
| `DW_LNS_fixed_advance_pc` | 0x09 | 唯一一个**操作数不是变长数**的标准操作码（uhalf），且**不乘** `minimum_instruction_length`；执行后 `op_index` 归零 |
| `DW_LNE_end_sequence` | 0x01（扩展） | 先追加一行（`end_sequence = true`），再把状态机寄存器全部复位到初值 |

#### 两种用法（§6.2.4 开头的原话）

> If you want to set a breakpoint at a particular line, the table gives you the memory address … if your program has a fault … you can look for the source line that is **closest to** the memory address.

所谓 "closest to"，实际实现是"取地址不超过目标值的最后一行"——本 demo 的 `lookup()` 即此。

## 三、对比

| 维度 | DWARF4 | DWARF5 |
| --- | --- | --- |
| CU 头 | unit_length / version / abbrev_offset / address_size | 插入 `unit_type`，且所有单元头共享前三个字段 `initial_length`+`version`+`unit_type` |
| 行号头 | 起始就是 `minimum_instruction_length` | 新增 `address_size` / `segment_selector_size`（为支持剥离除行号以外所有节的场景） |
| 字符串 | 单一 `.debug_str` | 拆出 `.debug_line_str`、`.debug_str_offsets`（`DW_FORM_strx*` / `line_strp`） |
| 常量属性 | 无 | `DW_FORM_implicit_const`：值搬到缩写表侧，DIE 省字节 |
| 类型单元 | `.debug_types` | 并入 `.debug_info`（`DW_UT_type`），引入"签名"而非偏移做引用 |

## 四、环境

- Python 3.8+（只用标准库 `struct`）
- Go 1.17+（只用标准库 `encoding/binary`）
- 不依赖任何真实编译器产物：所有输入都是按规范手工拼出来的合成字节

## 五、运行方式

```bash
python dwarf_check.py   # 10 组断言，全部实跑
go run dwarf_line.go    # Go 侧对照：LEB128 / initial length / 特殊操作码换算
```

文件角色：`dwarf_const.py`（编号常量表）、`dwarf_parse.py`（CU 头/缩写表/DIE）、`dwarf_line.py`（行号状态机）、`dwarf_check.py`（自检）。

## 六、关键代码

```python
def uleb128_decode(data, off):
    result, shift = 0, 0
    while True:
        byte = data[off]
        off += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, off
        shift += 7
```

```python
# §7.5.3:implicit_const 的取值写在缩写表里,DIE 侧不占字节
for at, form, implicit in spec["attrs"]:
    if form == 0x21:
        attrs[...] = implicit          # 直接取缩写表里的 SLEB
        continue
    attrs[...] = read_form(rd, form, cu, str_sec)
```

```python
# §6.2.5.1 必须「整体」实现,只写一半会错得悄无声息
def _advance(prog, st, operation_advance):
    total = st.op_index + operation_advance
    st.address += min_inst_len * (total // max_ops)
    st.op_index = total % max_ops
```

## 七、性能边界

- **LEB128 解码是逐字节 shift-or**，一个值最多几次迭代；真正的成本不在这一层。
- **行号表展开是整个 DWARF 里最贵的操作之一**：一个 CU 的行数 ≈ 源码行数 × 若干（优化会增加），展开后的矩阵常是原节点字节的数十倍。生产级 libdwarves / LLVM 的做法是**惰性展开 + 按地址区间缓存**，而不是一次全展开。
- **`op_index` / `max_ops` 只对 VLIW 有意义**（规范原话："For non-VLIW architectures, this field is 1, the op_index register is always 0"）。在 x86 / ARM 上永远取值 1，看到非 1 就该怀疑节内容或架构判断有问题。
- 本 demo 的 `parse_dies` 只做线性扫描、**不递归 children**（`limit` 参数即可见证），完整的 DIE 树构造需要按 `has_children` 维护一个显式栈。

## 八、注意事项与常见坑

1. **`header_length` 的起点**。它在规范里的定义是"header_length 字段之后"，不是"当前读取位置之后"。写反了会导致程序偏移整体后移 4~18 字节，症状是把正常操作码读成乱码。
2. **`version` 有两套编号**：CU 头里的 `version`（必须为 5）与行号头里的 `version` 是独立的两套值，规范明文写了一句是 "independent of the DWARF version number"。混为一谈会在混合版本对象上误判。
3. **`DW_FORM_implicit_const` 的值在缩写表里**。这是唯一一个"属性取值不在 DIE 里"的 form（除 `flag_present` 的隐含 1）。
4. **`code == 0` 有两层含义**：缩写表里表示"表结束"，DIE 流里表示"null entry"。同一个 0，上下文不同含义不同。
5. **`DW_LNS_const_add_pc` 的推进量是 `(255 - opcode_base) / line_range`**，不是某个固定字节数——它依赖头部参数，换一个 CU 就换一个值。
6. **`end_sequence` 之后记得复位**：忘记复位会让下一个 sequence 的行号从上一个 sequence 的末行继续，产生完全错误的源码归属。
7. **`SLEB` 的符号位是终止字节的第 6 位而不是"最后一字节的最高位"**（最高位永远是 continuation bit）。写成后者会把 `-1` 解成 `127`。
8. **`parse_dies` 未处理 `DW_FORM_indirect`**（form 值的真正 form 紧随其后），真实archives 里很少见但 ABI 允许，遇到应当报错而不是静默跳过。

## 九、参考资料（本轮实测下载并提取正文）

- [DWARF Debugging Information Format Version 5 (February 13, 2017)](https://dwarfstd.org/doc/DWARF5.pdf) — §6.2.4 Line Number Program Header、§6.2.5.1 Special Opcodes、§6.2.5.2 Standard Opcodes、§6.2.5.3 Extended Opcodes、§7.4 Initial Length、§7.5.1.1 CU Headers、§7.5.3 Abbreviations Tables、Table 7.2 / 7.3 / 7.25 / 7.26
- [binutils `include/dwarf2.def`](https://sourceware.org/git/?p=binutils-gdb.git;a=blob_plain;f=include/dwarf2.def) — `DW_TAG` / `DW_AT` / `DW_FORM` 的编号取值（与其头文件 `include/dwarf2.h` 通过宏展开成 enum）
- [Introduction to the DWARF Debugging Format — Michael J. Eager, April 2012](https://dwarfstd.org/doc/Debugging-using-DWARF-2012.pdf) — 为什么要引入缩写表，以及行号表为什么必须压成程序而不是直接存表
