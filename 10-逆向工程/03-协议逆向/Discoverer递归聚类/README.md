# Discoverer：标识化 → 递归聚类 → 基于类型的序列比对合并

> NetT-based 协议逆向的**开山之作**：只拿网络轨迹（libpcap / Netmon），不碰二进制，就能自动推断应用层报文格式。W. Cui, J. Kannan, H. J. Wang, *Discoverer: Automatic Protocol Reverse Engineering from Network Traces*, **16th USENIX Security Symposium, 2007**。本目录复现它的三阶段流水线，并把它自己承认的两类缺陷（标识化误差、过度分类）做成可执行断言。

## 一、简介

Discoverer 的定位是"协议无关"——不去理解具体协议，而是推断**多数应用层协议共用的惯用语（protocol idioms）**：常量魔数、长度字段、偏移字段、cookie、FD（format distinguisher）字段等。

三个阶段，每阶段解决一个具体问题：

| 阶段 | 输入 | 输出 | 解决什么 |
| --- | --- | --- | --- |
| ① 标识化 + 初始聚类 | 原始报文 | 若干粗簇 | 变长字段让逐字节对齐失效 → 改用 **token 模式**聚类 |
| ② 递归聚类 | 粗簇 | 细簇 + 格式 | 同一 token 模式 ≠ 同一格式 → 用 **FD token** 递归切分 |
| ③ 合并 | 全簇格式 | 精简格式集 | 保守切分导致 **过度分类** → 用**基于类型的序列比对**回并 |

## 二、原理详解

### 2.1 标识化（§3.2.1）

token = "很可能同属一个应用层字段的连续字节序列"，只有两类：**text** 与 **binary**。

1. 逐字节判断是否落在可打印 ASCII（本 demo 取 `0x20..0x7E`）；
2. 夹在两个 binary 字节之间的**连续可打印段**视为 text 段，并要求**最短长度**（论文 §4.3 Table 3 给的是 **3 个字母**）——这是为了不把偶然可打印的二进制字节误判成文本；
3. text 段再用分隔符（空格、tab）切成 token；
4. **binary 字段边界极难判定，于是直接把单个 binary 字节当成一个 token**（本 demo `discoverer.py::tokenize` 的 `else` 分支）。

论文明确列出这套规则会犯的三种错，本 demo 全部复现：

- 连续可打印的二进制字节被误标成 text；
- 短于最短长度的文本被误标成 binary —— 本 demo 用 `body="OK"`（2 字节）触发，产物 `FAMILY_A_ODD`；
- 由空白字符组成的文本字段被切成多个 token。

论文还说 "We also look for Unicode encodings in messages"，本 demo 实现了 UTF-16LE 探测（`find_utf16le` / `findUTF16LE`）：可打印字节后紧跟 `0x00` 的偶长度段被整体识别为一个 text token。

> **关键取舍**：binary 侧"一字节一 token"是**故意的粗粒度**。它把边界判定的难题全部推给后面的聚类与合并，换来的是不需要任何协议先验。

### 2.2 初始聚类（§3.2.2）

论文否掉了逐字节 Needleman-Wunsch 对齐：它适合"字节模式相似"的报文，但不适合"格式相同"的报文——**变长字段会让两条同格式报文错位**。

替代方案是 **token pattern**：

```
(dir, class_of_token_1, class_of_token_2, ...)
```

`dir` 是方向（C2S / S2C），参与模式是因为"**相反方向的报文往往格式不同**"。本 demo 断言：`C2S` 与 `S2C` 的同一条报文必然落到不同簇。

### 2.3 格式推断（§3.3.1）

格式 = **token 规范序列**，每个 token 带**属性**与**语义**：

- **属性**：binary/text（标识化已定）、constant/variable（同 token 偏移上取值全同即常量）。
- **语义**（本 demo 实现两种）：
  - **length**：候选 = 连续 1~4 个 binary token（或一个十进制/十六进制 text token）组成的整数；判据是**对簇内所有报文对**都满足
    `值差 == 报文长度差` 或 `值差 == 某个后续 token 的长度差`。
  - **offset**：同样构造候选，但比较的是**值差 == 某个后续 token 的起始偏移之差**。
  - **cookie**：论文说它在合并阶段末尾才做（需要关联同一会话的多条报文，用 RolePlayer 的启发式），本 demo 未复现。

**length 与 offset 会互相误判**，本 demo 用夹具 `FAMILY_B` 把它变成可断言的事实：offset 字段值差 `-6`、报文长度差 `-10`、任一单个 token 长度差 ∈ `{-3, 0, -4}`，三者互不相等，于是**只有 offset 判据命中**。设计夹具时踩到的坑：如果只让一个前置字段变长，那么"偏移差"与"报文长度差"必然相等，两个判据会同时命中——必须让**两个**前置 text token 变长，且再让一个后置 token 变长，才能把三者分开。

### 2.4 格式比较（§3.3.2）

逐 token 从左到右比对**类型（语义 + 属性）**，全中才算同一格式。没有语义的 token 才退回比值，且策略**刻意保守**：

- 常量 vs 变量：变量**至少取过**该常量的值 → 匹配；
- 变量 vs 变量：两个取值集合**有交集** → 匹配；
- 常量 vs 常量：取值必须相等。

### 2.5 递归聚类（§3.3.3）—— FD token 三判据

**FD（format distinguisher）** 是"决定报文后续部分格式"的字段（典型是命令码）。找 FD 的判据：

1. 该 token 的**取值个数 < 阈值**（FD 通常只取少数几个值，对应少数几种格式）。阈值在 §4.3 正文提到、但 Table 3 **未给数值**，本 demo 取 `8`（`FD_MAX_DISTINCT`），并在代码注释中标注为 demo 自选；
2. 按取值切簇后，**最大子簇 ≥ 最小簇大小**（Table 3 给 `20` 条报文，本 demo 夹具小改为 `2`）——否则切下去也做不出有意义的格式推断；
3. 对子簇做格式比较，**格式相同的合并回去**，保留真正不同的。

扫到一个 FD 就切，然后**对子簇重新做格式推断再往下扫**（集合变小后，原来 variable 的可能变成 constant，原来不是长度字段的可能变成长度字段），递归直到切不动。

> **实现坑**：常量 token 的取值个数是 1，也满足判据 1，但按取值只能切出 **1 个子簇**——等于没切，若不加排除会**递归不终止**。本 demo 在 `find_fd` 里显式加了 `len(groups) < 2 → continue`。

### 2.6 基于类型的序列比对合并（§3.4）

保守切分的代价是**过度分类**：论文实测 CIFS/SMB 近 400 万条报文产生约 **7000 个簇/格式**，而真实格式只有 **130** 个。

关键洞察：序列比对**不能**用来聚类同格式报文，但**可以**用来对齐**格式**（此时比对的是 token 类型而非字节）。三条约束：

1. **只有同 class 的 token 才能互相对齐**；
2. 为补偿标识化误差允许 gap，但加限制：一侧的连续 binary token 对上 gap 时，个数不得超过另一侧那个 text token 的大小（处理"二进制被误判成文本 / 反之"）；text token 对上 gap **至多 2 次**（处理"空白文本被切成多段"）；
3. **至多 1 处失配**即判为可合并——失配的那个 token 可以看成"取值集合被扩大了的变量 token"。

论文特别指出：由于判据是"gap 约束 + 失配个数"，**性能对 match/mismatch/gap 打分不敏感**。

## 三、与其他方法的对比

| 方法 | 输入 | 粒度 | 能拿到 | 拿不到 |
| --- | --- | --- | --- | --- |
| **Discoverer (2007)** | NetT | token（字节） | 字段边界 + 长度/偏移语义 + 消息类型 | 字段的深层语义、状态机 |
| Polyglot (2007) | ExeT | 字节 | 字段边界 + 关键字/方向字段 | 递归结构 |
| AutoFormat (2008) | ExeT | 字节 | **层次/并行/顺序**结构 | 需能跑二进制 |
| Tupni (2008) | ExeT | 字节 | 记录序列 + 记录类型 + **约束** | 同左 |
| ReFormat (2009) | ExeT | 字节 | 以上全部，**即使报文被加密** | 同上 |

## 四、环境

- Python 3.13（仅标准库）；Go 1.18+（仅标准库）。无第三方依赖。

## 五、运行方式

```bash
python discoverer_check.py      # 22 条断言，全部实跑通过
go run .                        # 11 条断言，无工具链时按人工审查 + 机械核查
```

## 六、关键代码

| 文件 | 行 | 内容 |
| --- | --- | --- |
| `discoverer.py` | 252 | 标识化 / token 模式 / 格式推断（length+offset）/ 匹配策略 |
| `discoverer_merge.go` | 175 | 格式比较、FD 三判据、NW 对齐与合并判据（Go 侧） |
| `discoverer.go` | 252 | 同上前三项的 Go 实现 |
| `discoverer_fixture.py` / `main.go` | 24 / 64 | 测试夹具与自检入口 |

核心片段——FD 三判据（Python）：

```python
for k, spec in enumerate(fmt):
    vals = [tokenize(m)[k][1] for m in msgs]
    if len(set(vals)) >= FD_MAX_DISTINCT:      continue      # 判据 1
    groups = {}
    for m, v in zip(msgs, vals): groups.setdefault(v, []).append(m)
    if len(groups) < 2:                        continue      # 常量 token 切不出子簇
    if max(len(g) for g in groups.values()) < MIN_CLUSTER: continue   # 判据 2
    subfmts = [infer_format(g) for g in groups.values()]
    if any(format_equal(subfmts[0], s) for s in subfmts[1:]): continue # 判据 3
    return k
```

## 七、性能边界与注意事项

- **合并是最慢的一步**：论文自述未优化实现处理数百万条报文要 **6–12 小时**，瓶颈是"所有推断格式两两比对"（O(N²) 对格式数）。
- **最大报文前缀 2048 字节**：超过部分直接不看，因此**尾部字段可能永远推断不出来**。
- **参数不敏感是设计目标而非巧合**：论文实测把前缀从 2048 改 1024、最小簇从 20 改 10，性能相当。
- **cookie 语义需要会话上下文**，单条报文无法判定；本 demo 未实现。
- **binary 侧一字节一 token** 会让一个真 4 字节整数字段先裂成 4 个 token，靠语义推断（length）再合回去——语义推断失败时字段就一直是碎的。

## 八、常见坑（本 demo 实跑暴露）

1. **常量 token 会让递归聚类不终止**（2.5 的实现坑）；
2. **length 与 offset 判据会同时命中**，夹具必须让"偏移差 ≠ 报文长度差 ≠ 任一 token 长度差"才能区分（2.3）；
3. **NW 回溯时若不限定同 class**，binary token 会与 text token 对齐，合并判据立刻失效；
4. **`infer_format` 要求簇内 token 数一致**——同一 token 模式簇是这个前提的保证，跨簇调用会直接断言失败。

## 九、参考资料（均已实际阅读）

- W. Cui, J. Kannan, H. J. Wang, [Discoverer: Automatic Protocol Reverse Engineering from Network Traces](https://www.usenix.org/legacy/event/sec07/tech/full_papers/cui/cui.pdf), USENIX Security 2007 —— §3.2.1 标识化三类误差与 UTF 探测、§3.2.2 token pattern 与"逐字节对齐不适合同格式报文"的论证、§3.3.1 属性/语义推断与 length/offset 启发式、§3.3.2 保守匹配策略、§3.3.3 FD 三判据、§3.4 两条 gap 约束与"至多 1 处失配"、§4.2 正确性/简洁性/覆盖率三指标（>90% 簇单格式、5:1 冗余比、>95% 报文覆盖 / 30–40% 格式覆盖）、§4.3 Table 3 参数、§4.4–4.6 HTTP/RPC/CIFS-SMB 结果与"7000 簇 vs 130 真实格式""6–12 小时"等数字。
