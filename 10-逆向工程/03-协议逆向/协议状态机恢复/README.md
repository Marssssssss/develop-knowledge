# 协议状态机恢复（PFSM / Veritas 方法）

> 协议逆向链路 `报文样本 → 字段边界 → 字段语义 → 消息类型 → 状态机` 的最后一环：**从消息类型序列恢复协议状态机（PFSM, Protocol Finite State Machine）**。代表工作 Veritas（ACNS 2011）：不用协议规范先验，纯统计地从真实流量推出**概率协议状态机 P-PSM**，在 SMTP/PPLIVE/XUNLEI 上平均正确刻画 92% 的协议流。

## 一、简介

Veritas 四阶段流水线（本 demo 全部实现其骨架）：

1. **报文分析**：从应用层报文头里找高频消息单元（keyword），用 **K-S 检验**（Kolmogorov–Smirnov）确定消息单元的最优数量；
2. **状态消息推断**：对每个消息格式提取特征，用 **PAM 聚类**（Partitioning Around Medoids）把相似消息聚成簇，**每簇的 medoid（中心点报文）即一个协议状态消息**；
3. **状态机推断**：把每条流的报文序列按状态消息打标签，统计状态转移计数，得到一阶马尔可夫转移概率；
4. **输出 P-PSM**：状态 + 带概率的转移边；低于阈值的边视为噪声剪掉。

## 二、原理详解

### 2.1 关键定义（Veritas 原文）

- **协议状态消息（state message）**：能标识协议所处状态的消息；不一定能直接观测，靠"**频繁出现在它旁边的消息**"来估计。
- **协议格式消息（format message）**：协议格式里出现频率最高的字符串（即 keyword）。状态消息 ⊆ 格式消息。
- **P-PSM**：协议状态机的概率推广——每条转移边带经验概率 `P(s'|s) = C(s→s') / ΣC(s→·)`，用于在知识不完整（真实流量永远只覆盖部分路径）时仍能表达协议行为。

### 2.2 消息类型标注（本文实现简化）

Veritas 的 K-S 检验定"最优消息单元数"针对的是 binary 协议切 token；对文本协议（SMTP 类），消息类型可直接取**首 token（命令字 / 应答码）+ 方向**。本 demo 合成 SMTP 风格流量：`EHLO/250/MAIL/RCPT/DATA/354/./250/QUIT/221`，每条消息 `(方向, 类型码)`。

### 2.3 状态合并（PFSM 通用难点）

同一逻辑状态可能对应多条不同报文（如多个不同的 250 应答）。工程做法：**先按 (方向,类型) 建原子状态 → 观测不到区分意义的等价类合并**。本 demo 演示最简单的一种：把"只在同一上下文出现"的状态合并（可选）；更强的等价（k-续等价 / Nerode 等价）是 ReverX、概率自动机学习的核心，见参考资料。

### 2.4 转移概率与剪枝

- `P(s' | s) = C(s→s') / Σ_{x} C(s→x)`；
- 噪声（乱序抓包、重传、异常流）会产生虚假低频边，**阈值剪枝**（如 P < 0.05 且 C < 3 的边删除）是必要步骤；
- 剩下的图即 PFSM：`START → EHLO → 250 → MAIL → … → QUIT → END`，边上的概率表示该协议实现的**真实行为偏好**（如 RCPT 出现 0~N 次的自环概率）。

## 三、对比

| 方法 | 输入 | 产物 | 局限 |
| --- | --- | --- | --- |
| Veritas（本 demo） | 纯流量 | 概率 PFSM | 假设流量未加密、单一协议 |
| Prospex（ExeT 路线） | 执行轨迹（污点分析） | 格式 + 状态机，直接可喂 Peach fuzzer | 需要可执行二进制 |
| RolePlayer | 流量 + 环境约束 | 可重放的消息 + 状态机 | 格式推断靠先行工具 |

## 四、环境与运行

- Python ≥3.8：`python pfsm.py`
- Go ≥1.18：`go run pfsm.go`
- 输出：合成 SMTP 流量的类型序列、转移计数矩阵、剪枝后的 PFSM 边表（含概率）、自检断言。

## 五、关键代码

```python
def transitions(seq):            # seq = [('C','EHLO'), ('S','250'), ...]
    C = defaultdict(lambda: defaultdict(int))
    for a, b in zip(seq, seq[1:]):
        C[a][b] += 1
    return C

def pfsm(C, p_min=0.05, c_min=3):  # 转移计数 → 剪枝后的概率边
    edges = []
    for s, row in C.items():
        tot = sum(row.values())
        for s2, c in row.items():
            if c >= c_min and c / tot >= p_min:
                edges.append((s, s2, c, c / tot))
    return edges
```

## 六、性能边界

- 转移统计 O(总报文数)；聚类 O(N·k·iter)。十万级报文毫秒~秒级。
- 状态数上限：原子状态数 ≤ 方向×类型数；真实二进制协议要先做格式聚类（Veritas 用 PAM），那里才是复杂度大头。
- 一阶马尔可夫假设：依赖前一个状态就够。FTP/SMTP 基本成立；有强跨状态依赖（会话 ticket、分片重组）的协议会丢边。

## 七、注意事项与常见坑

1. **方向必须进状态标签**：客户端 `250` 和服务端 `250` 是不同消息；不区分方向会把请求/应答合并成自环，状态机退化。
2. **首 token 标注只对"命令码前置"的协议成立**：binary 协议类型字段在固定偏移，需要先做字段切分（见同目录前两个 demo）再取类型列。
3. **剪枝阈值是双刃剑**：阈值过高压掉真实但低频的分支（如错误处理路径）；Veritas 论文用"覆盖 92% 流"作为保真度度量——剪枝后要回测**流覆盖率**（每条流的转移序列是否都被 PFSM 接受）。
4. **乱序/重传流量**会造出 `(250, EHLO)` 这类伪边；先按 TCP seq 排序重组再喂。
5. **medoid ≠ 众数**：PAM 的 medoid 是"到簇内其他成员距离和最小"的真实样本点，不是统计模式；用众数代替会在簇内多峰时选错代表。

## 八、参考资料（实际读过）

- [Inferring Protocol State Machine from Network Traces: A Probabilistic Approach（Veritas, ACNS 2011）— Springer](https://link.springer.com/content/pdf/10.1007/978-3-642-21554-4_1.pdf) —— P-PSM 形式化定义、状态消息/格式消息定义、四阶段架构（K-S 检验定消息单元数、PAM 聚类 medoid 即状态消息、转移概率统计）、SMTP/PPLIVE/XUNLEI 评测（92% 平均流刻画、86% SMTP 分类）
- [Veritas 摘要页 — Springer Link](https://link-hkg.springer.com/chapter/10.1007/978-3-642-21554-4_1) —— "无协议规范先验、基于格式统计、文本与二进制协议通用"三点定位与实验数字（92% 平均 / 86% SMTP / 100% PPLIVE / 90% XUNLEI）
- [State of the art of network protocol reverse engineering tools — INRIA HAL](https://inria.hal.science/hal-01496958/file/jicv_SoA_ProtRE.pdf) —— 综述视角：Polyglot 不做状态机推断、ReverX 用语音识别思路找分隔符并合并简化 PFSM、RolePlayer 字节级对齐 + FSM 简化；Veritas 属"从消息序列推状态机"一线
