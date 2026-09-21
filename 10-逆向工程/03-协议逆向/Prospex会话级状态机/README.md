# Prospex —— 会话级协议规范抽取与状态机推断

## 简介

Prospex（**Pro**tocol **Spe**cification E**x**traction，Comparetti / Wondracek / Kruegel / Kirda，IEEE S&P 2009）解决的是「前一代 PRE 工具做不到的那一半」：Polyglot、AutoFormat、Tupni、ReFormat 都能抽**单条报文**的格式，但都不抽**协议状态机**，于是产出的不是可用的协议规范（没法拿去做深度包检测或有状态 fuzz）。

Prospex 在行为式（ExeT-based）格式抽取之上补了两步：

1. **消息类型聚类**——不只比结构，还比「这条报文对服务器行为的影响」（执行轨迹里的库函数、系统调用、文件系统操作）；
2. **状态机推断**——把会话写成消息类型序列，用 APTA + **前置条件标注** 造出带标签的状态树，再交给 Exbar 求最小一致 DFA。

最终产物可直接导出成 Peach fuzzer 的 XML（论文用它发现了真实服务端的漏洞）。

## 原理详解

### 1. 距离：三组特征、每组 1/3

| 特征组 | 具体特征 | 相似度 |
| --- | --- | --- |
| message | direction、keywords | 相等判据 / Jaccard |
| execution | funcs、syscalls | Jaccard |
| file-system | fileops（`⟨open, "/CONFIG/TAINT"⟩` 形式） | Jaccard |

```text
d(a,b) = 1 − Σ_i w_i · s_i(a,b)
```

权重规则：三组各占 `1/3`，组内等权。所以 direction / keywords / funcs / syscalls 各 `1/6`，fileops 独占 `1/3`。**只改方向**的两条报文距离恰好是 `1/6`（自检 E3 实测）。

### 2. 聚类：PAM + 泛化 Dunn 指数

聚类用 **PAM**（Partitioning Around Medoids，围绕代表点划分）：BUILD 阶段贪心挑代表点，SWAP 阶段反复尝试「用一个非代表点换掉一个代表点」直到代价不再下降。

k 由**泛化 Dunn 指数**定：

```text
D(k) = min_{i≠j} δ(Ci,Cj) / max_i Δ(Ci)
```

- δ 取 **single-linkage**（两簇最近点距离）——分子是簇间分离度；
- Δ 取基于 **Relative Neighborhood Graph** 的直径：RNG 中 `(a,b)` 成边 ⇔ 不存在 c 使 `max(d(a,c),d(b,c)) < d(a,b)`，直径取 RNG 上最大边权。

选使 `D(k)` 最大的 k。RNG 直径比「最大点距」抗离群点，这是它比原始 Dunn 指数更适合的原因。

### 3. APTA：把会话压成前缀树

每个会话是一条消息类型序列，全部会话的前缀构成一棵树（root 是空序列）。每个状态可以标 `accept` 或 `reject`——**Prospex 观察到「有效会话的任意前缀也是有效会话」**，所以全部标 accept。

问题来了：全是 accept 的树去跑最小化，结果必然是**只有一个状态的过泛化机器**。Gold 早就证明：只给正例学不出正则语言。

### 4. 前置条件标注（论文式 2 / 式 3）

出路是给状态打上**更细的标签**。观察：应用层协议普遍存在「必须先发 r，才能发 m」的模式（Agobot 要先 login；SMB/CIFS 要先 TREE CONNECT）。

```text
prerequisite:  .* r (a1 | .. | aj)*
```

- `r`：在**所有**会话中都出现在 `m` 之前的消息类型；
- `M_r`：所有满足该条件的 `m`；
- `A_r`：至少在一个会话中，出现在「最后一个 `r`」与某个 `m ∈ M_r` 之间的类型集合。

于是状态 `q` 的标签 = **路径满足其全部前置条件的那些消息类型**。只有标签相同的状态才允许被 Exbar 合并——这就是「限制假设空间」的领域知识。

两个补丁：

- **hitting set 启发式**（式 3）：`. * (r1|..|rk)(a1|..|aj)*`——允许多条等价路径（SMTP 的 HELO / EHLO）；
- **end-state 启发式**：只在所有会话末尾出现过的类型（如 QUIT）之后的状态，标签置为空集。

### 5. 合并：Exbar 的最小一致 DFA

一致性判据（本 demo 的实现）：

1. 同块状态必须**标签相同**；
2. 同块状态对同一符号的**已定义**后继必须落在同一块（未定义不算冲突）。

块数由小到大**穷举**全部划分，取第一个合法解 → 得到**可判定的最小**机器（状态多时退回贪心）。

## Agobot 示例（论文 Fig 2~4 逐条对拍）

```text
session 1: login, bot.dns, bot.status, mac.logout
session 2: login, mac.logout, login, bot.status, bot.dns, mac.logout
```

前置条件推导：`login` 在所有会话中都早于 `bot.dns / bot.status / mac.logout`；最后一次 login 与它们之间只出现过 `bot.dns` 与 `bot.status`。于是三者共用一条前置条件：

```text
.* login (bot.dns | bot.status)*
```

标注结果（10 个状态，与论文 Figure 3 **逐条一致**）：

| 状态路径 | 标签 |
| --- | --- |
| `[]` | `{login}` |
| `[login]` / `[login,bot.dns]` / `[login,bot.dns,bot.status]` | 全部四种 |
| `[login,bot.dns,bot.status,mac.logout]` | `{login}` |
| `[login,mac.logout]` | `{login}` |
| `[login,mac.logout,login]` 及其后续（未以 logout 结尾） | 全部四种 |
| `[login,mac.logout,login,bot.status,bot.dns,mac.logout]` | `{login}` |

`mac.logout` 在会话 2 里不是末位，所以 **end-state 启发式不触发**——这点很容易想当然。

## 一个值得记的能力边界

若把一致性加强为「标签里允许的类型必须真的有转移」，那么**任何划分都无解**：`[login]` 之后标签允许再一次 `login`，但两个会话里从未出现连续的 `login`。

这不是实现 bug，而是论文自己承认的局限——*trace-based 方法学不到训练集中不存在的 behavior*。本 demo 把它做成显式断言（`label_realizable` 恒为 `False`），而不是悄悄放宽判据去凑一个好看的状态数。论文 §3.1 报 Agobot 为 4 状态，那是**真实数据集**（消息类型远多于这 4 种）的结果；在上述 4 类型示例上按同一判据得到的是 2 个接收状态 + 1 个 reject。

## 三个实现里最容易写反的地方

1. **`A_r` 是「最后一个 r 之后」而不是「任意 r 之后」**。写成任意 r 之后，`A_r` 会被第一个 r 之后的报文污染，`mac.logout` 也会被算进去，前置条件就松掉了。
2. **`r ∉ M_r`**。第一次出现的 r 前面没有 r，所以 r 自己永远不在 `M_r` 里；把 r 加进去会让 `.*login(...)* ` 变成自我依赖，标签全乱。
3. **未定义的转移不是冲突**。合并时只有「两边都定义了且落到不同块」才算冲突；把「一边未定义」也当冲突，会把机器切成碎片（本 demo 第一版就是这样切出 8 个状态）。

## 环境

- Python 3.9+（仅标准库）
- Go 1.21+

## 运行方式

```bash
# Python（演示 + 自检，81 条断言）
cd python && python main.py && python selfcheck_prospex.py

# Go（同题实现，贪心合并）
cd go && go run .
```

## 关键代码

| 位置 | 职责 |
| --- | --- |
| `python/main.py: feature_weights / distance` | 三组 1/3 的权重与距离 |
| `python/main.py: pam / dunn_index / _rng_diameter` | PAM 聚类、单链分离度、RNG 直径 |
| `python/main.py: APTA` | 前缀树 |
| `python/main.py: infer_prerequisites` | 式 (2) 前置条件 |
| `python/main.py: label_states / end_types` | 标签与 end-state 启发式 |
| `python/main.py: is_consistent / merge_states / _rgs` | 一致性判据 + 最小块数穷举 |
| `go/features.go / apta.go / prospex.go` | Go 同题实现（集合用排序切片落地） |

## 性能边界

- 穷举合并的复杂度是 **Bell 数级**：`_rgs` 枚举受限增长串，状态数 > 11 或块数 > 4 会自动退回贪心（贪心不保证最小）。
- PAM 的 SWAP 是 O(k·n²)，报文上千时建议先按关键字粗筛。
- RNG 直径是 O(|C|³)，簇很大时是热点。

## 注意事项

- 论文里「只有正例」是根本性障碍（Gold 定理），前置条件标注只是**启发式地**加入了负例信息，不能保证学到真状态机。
- 聚类质量直接决定后续一切：报文类型被拆散（如 login 带可选参数）时，需要靠式 (3) 的 hitting set 兜底。
- 本 demo 不实现污点分析/执行监控，特征集合由调用方喂入。

## 参考资料（已读）

- [Prospex: Protocol Specification Extraction（IEEE S&P 2009，PDF 全文 252 KB）](https://www.cs.ucsb.edu/~chris/research/doc/oakland09_prospex.pdf) —— §2.2 三组特征与 `d(a,b)=1−Σwᵢsᵢ` 的权重规则（每组 1/3）、PAM 与泛化 Dunn 指数（单链 δ + RNG 直径）、§2.3.1 APTA 与「全 accept 会塌成单状态」、§2.3.2 前置条件式 (2) 的 `r / M_r / A_r` 定义与 Agobot 示例的 `.*login(bot.dns|bot.status)*`、hitting set 式 (3)、end-state 启发式、§2.3.3 Exbar 为 NP-complete 的最小一致 DFA 推断、§3.1「Agobot 4 状态 / SMB 13 状态」与「学不到训练集中不存在的 behavior」
- [BinPRE（arXiv 2409.01994）](https://arxiv.org/pdf/2409.01994v1) —— §5 对 Prospex 的定位：行为式格式抽取的代表，但语义推断能力弱于污点 + 算子序列方案
