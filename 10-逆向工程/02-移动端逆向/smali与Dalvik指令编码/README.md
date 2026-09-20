# smali 与 Dalvik 指令编码

## 简介

smali 是 Dalvik 字节码的**汇编文本形式**，`baksmali` 把 dex 里的 16 位代码单元流还原成它，`smali` 再把它装回去。要做"改一行逻辑再重打包"、"定位某个加密函数的调用点"、"给混淆过的代码批量插桩"，都必须先能把指令流**按位拆开、按位装回**——也就是理解官方的两张表：**格式表**（位布局）和 **opcode 表**（操作码 → 助记符 + 格式）。

本目录按 AOSP 官方《Dalvik 指令格式》+《Dalvik 字节码》实现：

- `dalvik_isa.py` — 格式 ID 规则、类型代码字母表、opcode 表、`decode()`（代码单元 → 字段）
- `dalvik_encode.py` — `encode()`（字段 → 代码单元）、参数寄存器语义、payload 公式、反汇编
- `selfcheck_dalvik.py` — 281 项断言，逐条对拍官方表
- `dalvik_isa.go` / `dalvik_selfcheck.go` — 同构 Go 实现（本机无 Go 工具链，走静态校验）

## 原理详解

### 1. 格式 ID 的命名规则

官方的格式 ID 大多是三个字符：

- 第 1 个数字 = 该格式占的**16 位代码单元数**；
- 第 2 个数字 = 格式所含**寄存器数量上限**（`r` 表示"编码了一系列寄存器"）；
- 第 3 个字母 = 半助记符，表示额外数据类型。

例：`21t` = 长度 2、1 个寄存器、另带一个分支目标；`35c` = 长度 3、最多 5 个寄存器、另带常量池索引。后缀 `s` 是建议的静态链接格式，`i` 是建议的内联链接格式，它们不改变前三字符的含义。

额外数据类型的字母表（位宽来自官方）：

| 字母 | 位 | 含义 |
| --- | --- | --- |
| b | 8 | 有符号立即数（字节） |
| c | 16/32 | 常量池索引 |
| f | 16 | 接口常量（仅静态链接） |
| h | 16 | 有符号立即数 hat（32/64 位的高阶位） |
| i | 32 | 有符号立即数 / 32 位浮点 |
| l | 64 | 有符号立即数 / 64 位双精度 |
| m | 16 | 方法常量（仅静态链接） |
| n | 4 | 有符号立即数（半字节） |
| s | 16 | 有符号立即数（短整型） |
| t | 8/16/32 | 分支目标 |
| x | 0 | 无额外数据 |

语法里寄存器写成 `vX`（用 `v` 而非 `r` 是为了不和真实架构的寄存器前缀冲突），字面量 `#+X`，分支偏移 `+X`，常量池索引 `kind@X`（kind ∈ string / type / field / meth / site）。

### 2. 位布局

格式表的第一列就是位布局，读作"若干 16 位代码单元里的字段排布"，字母每重复一次代表 4 位：

| 格式 | 布局 | 例子 |
| --- | --- | --- |
| 10x | `ØØ|op` | `return-void`（高字节是被忽略的 0） |
| 12x | `B|A|op` | `move vA, vB` |
| 11n | `B|A|op` | `const/4 vA, #+B`（B 是 4 位有符号半字节） |
| 21c | `AA|op BBBB` | `const-string vAA, string@BBBB` |
| 23x | `AA|op CC|BB` | 第二单元低字节是 vBB、高字节是 vCC |
| 22t | `B|A|op CCCC` | `if-eq vA, vB, +CCCC` |
| 35c | `A|G|op BBBB F|E|D|C` | `invoke-kind {vC..vG}, meth@BBBB` |
| 3rc | `AA|op BBBB CCCC` | `invoke-kind/range {vCCCC..vNNNN}` |
| 51l | `AA|op BBBBlo BBBB BBBB BBBBhi` | `const-wide vAA, #+64 位` |

### 3. invoke-kind 的参数寄存器：35c 与 3rc

`35c` 用 `[A=N]` 变体表达"实际用了几个参数寄存器"：A 从 0 到 5，前四个来自第二单元的四个半字节 C/D/E/F，**第五个来自第一单元的 `G` 半字节**。所以 A<5 时 G 位不使用（应为 0）。

`3rc` 是"范围"变体：官方写明 `NNNN = CCCC + AA - 1`，即 A 是计数（0..255）、C 是第一个寄存器。A=0 时是空范围。

对应 opcode：`6e..72` 是 `invoke-virtual/super/direct/static/interface`（35c），`74..78` 是它们的 `/range` 变体（3rc）。`invoke-polymorphic` 用 `45cc` / `4rcc`（多一个 proto 索引 HHHH）。

### 4. 三个 payload 伪运算码

switch 与数组填充的数据不在指令里，而是对齐在代码后面的**数据表**，靠伪运算码识别：

| payload | ident | 代码单元总数 |
| --- | --- | --- |
| packed-switch-payload | 0x0100 | `size * 2 + 4` |
| sparse-switch-payload | 0x0200 | `size * 4 + 2` |
| fill-array-data-payload | 0x0300 | `(size * element_width + 1) / 2 + 4`（整除） |

逆向时如果按 `size` 而不是按这些公式跳，后续所有指令都会错位——这是手写解析器最常见的崩法。

### 5. 分支偏移不得为 0

官方在 `if-test` 一节明确注释：**分支偏移量不得为 0**。想构造自旋循环，要么用一个后向 `goto`，要么在目标处插一条 `nop`。

## 对比：DEX 指令 vs Java 字节码

| 维度 | Dalvik | JVM |
| --- | --- | --- |
| 模型 | 基于寄存器（寄存器数写在 `code_item.registers_size`） | 基于操作数栈（`max_stack` / `max_locals`） |
| 指令宽度 | 变长，1–5 个 16 位代码单元 | 变长，1 字节 opcode + 操作数 |
| 调用参数 | `35c` 最多 5 个，超了用 `3rc` 范围 | `invokevirtual` 直接从栈上取 |
| 常量引用 | 统一走 string/type/field/meth 池索引 | 走常量池条目（需解析 tag） |
| 跳转 | 相对当前指令的 16/32 位偏移 | 相对当前 bytecode 的 16/32 位偏移 |

## 环境

- Python 3.8+（只用标准库）
- Go 1.21+（本机无工具链，未实跑；已过括号配平 / 参数个数 / 交叉引用三项静态校验）

## 运行方式

```bash
python selfcheck_dalvik.py     # 281 项断言，全部通过
# Go（有工具链时）
go run .
```

## 关键代码

```python
# 35c：A 决定参数个数，第五个寄存器藏在第一单元的 G 半字节里
f["A"] = (u[0] >> 12) & 0x0F
f["G"] = (u[0] >> 8) & 0x0F
f["C"] = u[2] & 0x0F          # F|E|D|C 四个半字节
...
def arg_registers_35c(f):
    table = {0: [], 1: ["C"], 2: ["C","D"], 3: ["C","D","E"],
             4: ["C","D","E","F"], 5: ["C","D","E","F","G"]}
    return [f[k] for k in table[f["A"]]]

# 3rc：官方公式 NNNN = CCCC + AA - 1
def arg_registers_3rc(f):
    return [] if f["A"] == 0 else list(range(f["C"], f["C"] + f["A"]))

# payload 单元数必须按官方公式跳，不能按 size 跳
def packed_switch_units(size): return size * 2 + 4
def sparse_switch_units(size): return size * 4 + 2
def fill_array_data_units(size, w): return (size * w + 1) // 2 + 4
```

## 性能与边界

- `35c` 的参数寄存器 A 上限是 5；超过必须改用 `/range`（3rc），这也是为什么 `invoke-*/range` 在混淆过的代码里特别常见。
- `3rc` 的 A 是 8 位（0..255），但寄存器总数受 `code_item.registers_size`（ushort，65535）约束。
- `const-string` 与 `const-string/jumbo` 的区别不只是助记符：前者是 `21c`（16 位索引，最多 65535 个字符串），后者是 `31c`（32 位索引）。字符串池超限的 dex 必须用 jumbo。
- `21h` / `51l` 这类"hat / 64 位"字面量只编码**非零高阶位**，低位在语法里写成 0、在按位表示里被省略；还原时不要漏掉隐含的 0 位。

## 注意事项与常见坑

1. **`35c` 的第五个寄存器在 `G` 位** —— 只看第二单元的四个半字节会漏掉第 5 个参数。
2. **`3rc` 的 A 是计数不是上界** —— 寄存器区间是 `C .. C+A-1`，A=0 表示空。
3. **payload 要按官方公式跳** —— 按 `size` 跳会让后面的指令整体错位。
4. **分支偏移不能为 0** —— 汇编器会拒绝，反汇编看到 0 说明这段被篡改过。
5. **符号扩展看"类型字母"而不是格式长度** —— `21s` / `21t` 要按有符号 16 位还原，同长度的 `21c`（索引）/ `21h`（高阶位）是无符号。第一版把 `21s` 当无符号读，往返测试在 `B=-2500` 处直接失败。
6. **`10x` 的高字节必须写 0** —— 官方写作 `ØØ`；非 0 的文件虽然可能被容忍，但属于不规范形式。
7. **opcode 与格式是多对一** —— 不能反过来由格式推 opcode（如 `22t` 对应 `32..37` 六个分支），必须查 opcode 表。

## 参考资料（实际读过）

- [Dalvik 指令格式 — AOSP 官方（source.android.google.cn 中文镜像）](https://source.android.google.cn/docs/core/dalvik/instruction-formats) —— 格式 ID 命名规则、类型代码字母表、全部位布局串
- [Dalvik 字节码 — AOSP 官方](https://source.android.google.cn/docs/core/dalvik/dalvik-bytecode) —— opcode 表（00/01/0a/0e/0f/12–15/1a/1b/1c/28–2a/32–37/38–3d/6e–72/74–78/fa–ff）、`invoke-kind` 语义、三个 payload 的 ident 与单元数公式、"分支偏移量不得为 0"注释
- [Dalvik 可执行文件格式 — AOSP 官方](https://source.android.google.cn/docs/core/dalvik/dex-format) —— `code_item` 与 `insns_size` 的口径（相邻 demo）
