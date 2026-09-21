# BinPRE 原子语义检测器（5 类型 + 6 功能）

## 简介

BinPRE（arXiv 2409.01994，CCS'24）在格式抽取之后要回答「这个字段**是什么**」。它的做法不是训练模型，而是建一个**原子语义检测器库**：每个检测器只看两样东西，按「当且仅当」的硬规则判定。

| 输入 | 含义 |
| --- | --- |
| `I(f)` | 访问字段 f 的**指令算子序列** |
| `V(f)` | 字段 f 的**取值** |

判据全部来自论文 Table 2，本 demo 就是那张表的可执行版本。

## 原理详解

### 1. 五个语义类型

| 类型 | 判据（Table 2 原文义） |
| --- | --- |
| **Static** | 与固定值比较且结果为真，**且没有额外的 functional 操作** |
| **Integer** | 涉及算术/位运算；**或**与多个连续值比较 |
| **Group** | 通过条件分支与**多个不同常量**比较 |
| **Bytes** | 字段所有字节在同一循环内被相同操作访问（同一结构） |
| **String** | 在 Bytes 的基础上，连续字节与**同一个常量**（分隔符）比较 |

### 2. 六个语义功能

| 功能 | 判据 |
| --- | --- |
| **Command** | 与固定值比较为真，**且为真时立即触发跳转** |
| **Length** | 作为循环终止条件；**或**被库 API（如 `recv`）取用；**或**涉及指针递增/计数器递减 |
| **Delim** | 作为循环终止条件 **且** 分隔相邻字段 |
| **Checksum** | 与「对多个连续字节迭代」的输出比较 |
| **Filename** | 内容符合常见文件命名约定（靠 `V(f)`，不靠执行信息） |
| **Aligned** | 不涉及**任何** functional 操作 |

### 3. 「functional operation」是最容易踩的坑

论文定义：**算子不属于 mov 系列的指令**都算 functional operation。于是 `cmp` 也是 functional。

这就带来一个必须分清的口径：

- **Aligned** 要求「不涉及任何 functional 操作」→ 有 `cmp` 就**不是** Aligned；
- **Static** 要求「没有**额外的** functional 操作」→ 指**除比较之外**没有别的，有 `cmp` 仍然是 Static。

把 Static 写成 `not functional` 会让「协议版本字段」这类最典型的 Static 全部漏判（本 demo 第一版就是这么错的，自检 E2 直接打脸）。

### 4. 类型必有，功能可有

论文的设定是：输入报文里的**每个字段都有语义类型**，但**只有部分字段有语义功能**。所以检测器的输出是 `(类型集合, 功能集合)`，功能集合为空是正常结果，不是失败。

### 5. Example-3 复现

论文 Example-3 拿 Figure 5 的 `f21,22` 举例（对应 Listing 2 的汇编）：

- 第 5-6 行 `shl edx, 0x8` / `or eax, edx` 是**位运算** → 类型 **Integer**；
- 第 7-16 行把它与「对连续字节迭代的循环输出」比较 → 功能 **Checksum**。

相同的指令序列如果去掉与循环输出的比较，就只剩 `Integer`、功能为空——这正好演示「类型必有、功能可有」。

## 运行方式

```bash
cd python && python main.py                   # 六个示例字段的判定
cd python && python selfcheck_detectors.py    # 42 条断言
cd go && go run .                             # Go 同题实现
```

## 关键代码

| 位置 | 职责 |
| --- | --- |
| `python/main.py: MOV_OPS / CMP_OPS` | mov 系列与比较类的划分（functional 判据的基石） |
| `python/main.py: Trace` | `I(f)` / `V(f)` 的字段轨迹摘要 |
| `python/main.py: detect_type` | 5 种类型 |
| `python/main.py: detect_function` | 6 种功能 |
| `go/detectors.go` | Go 同题实现（集合用 `map[string]bool`） |

## 性能边界

- 检测器是**常数时间**的布尔规则组合，与字段数线性；真正的开销在上游的污点分析。
- BinPRE 自述局限：污点分析只到**字节粒度**，处理不了 bit 级字段（bitflag）——这正是 `03-协议逆向/bit级字段切分`（ID 376）要解决的问题。
- 检测器覆盖不了协议里所有语义；论文明确说「遇到新的就往库里加一条」，所以这是个**可扩展的规则库**而不是闭集。

## 注意事项

- 这些规则是**必要条件式的启发式**，不是充分必要条件：例如长度字段若只被 `mov` 搬运、从未参与循环终止，就检测不出来。
- `Filename` 是唯一靠 `V(f)`（内容）而非 `I(f)`（执行）判定的功能；其余 10 条都靠执行信息。
- Static 与 Command 的判据高度相似（都是「与固定值比较且为真」），区别只在**有没有立刻跳转**。真实实现里要看比较之后紧跟的是不是 `jz/jnz` 到分派表。

## 参考资料（已读）

- [BinPRE: Enhancing Field Inference in Binary Analysis Based Protocol Reverse Engineering — arXiv 2409.01994v1（1.27 MB PDF）](https://arxiv.org/pdf/2409.01994v1) —— §3.4 Semantic Inference 的五个类型与六个功能的完整定义、**Table 2**（Semantic Attributes / Rules of Atomic Semantic Detectors）、functional operation 的定义（「算子不属于 mov 系列的指令」）、Example-3 对 `f21,22` 的 Integer+Checksum 判定、以及「每个字段都有类型、只有部分字段有功能」的设定
- [Boofuzz](https://github.com/jtpereyda/boofuzz) —— 论文提到其语义类型集合与 BinPRE 对齐（§3.4 末）
