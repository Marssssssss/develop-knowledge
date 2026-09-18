# 协议逆向

> 从**网络流量**或**程序执行轨迹**中还原私有/未知协议的报文格式、字段语义与状态机。是流量分类、协议漏洞挖掘、恶意样本 C2 识别、IoT/工控协议互操作的共同前置能力。2026-09-14 经自动巡检按类目拓展规则新增（登记 `STATE.md` 索引表）。

## 核心研究主题

- **两条输入路线**：NetT-based（网络轨迹，靠大量报文的统计关系） vs ExeT-based（执行轨迹，反汇编服务端二进制 + 污点分析跟踪解析过程）
- **格式抽取（format extraction）**：字段边界切分——序列对齐、递归聚类、信息论、动态污点分析
- **语义推断（semantic inference）**：给字段贴上"长度/类型/命令码/校验和"等含义
- **状态机恢复（PFSM recovery）**：从消息序列推导协议状态机；依赖格式，格式又依赖字段切分
- **加密流量分析**：TLS 指纹（JA3/JA3S/JA4）、SNI、证书与 DNS 侧信道，TLS 1.3 + ECH 下的失效与替代
- **主动 vs 被动分析**：主动构造报文刺激系统，还是纯被动观察

## 概念地图（要点）

### 1. 为什么格式抽取是主干

ScienceDirect 的 PRE 综述指出：**状态机抽取往往依赖协议格式的推断，而字段切分又是语义推断的前提**。也就是说整条链是
`报文样本 → 字段边界 → 字段语义 → 消息类型 → 状态机`，前面错一步后面全崩。

从**输入**看分两族：ExeT-based 分析网络应用（动态或符号）执行过程；NetT-based 处理真实环境抓到的流量。从**控制**看分主动/被动：主动分析构造特定参数直接控制并刺激系统，被动分析只依赖观测数据计算。

### 2. NetT-based：拿报文样本换结构

优点是好操作、时效性强、报文样本足够大时出结果快；缺点是对**语法之外**的东西无能为力——网络轨迹只含语法信息。代表性思路：

| 思路 | 代表 | 做法 |
| --- | --- | --- |
| 序列对齐 | Netzob、Netplier、PIP | 把多会话报文两两比对（Needleman-Wunsch / Smith-Waterman 类算法），对齐后找不变段与可变段 |
| 递归聚类 | Discoverer | 先 tokenize 报文，再递归聚类出格式，把相似格式合并 |
| 信息论 | BinaryInferno | 把报文当信息序列，用信息论度量找边界；DynPRE 进一步用**响应报文**增强边界识别 |
| 频繁项挖掘 | AutoReEngine、Biprominer | Apriori / FP-Growth 类关联分析，按出现频次切关键字与字段 |

### 3. ExeT-based：拿执行轨迹换结构（污点分析）

把服务端二进制当输入，监视执行轨迹、跟踪报文解析过程，直接暴露内部设计逻辑。开山之作 **Polyglot（2007）** 首次把污点分析用于协议二进制，用**三类特殊字段函数**（方向字段 direction field、关键字 keyword、分隔符 separator）由执行轨迹的启发式模式切分字段。后续：

- **AutoFormat（2008）**：按已执行指令的局部性与调用栈相似性切字段
- **Tupni（2008）**：按字节出现情况找候选字段，把**同一个循环里连续处理的字节合并成一个字段**
- **Prospex（2009）**：把 AutoFormat 扩展到会话级，按系统调用跟踪并切分完整会话内的解析过程
- **BinPRE（2024）**：认为上述行为特征会因协议实现不同而失真，改比对指令序列的**算子序列语义相似度**；5 报文小样本下格式抽取 F1 = 0.87、语义类型/功能推断 F1 = 0.75 / 0.84；局限是污点分析只在**字节粒度**，处理不了 bit 级字段（如 bitflag）

### 4. 语义推断的两难

NetT 路线只能拿到语法，因此语义必须靠**聚类、监督式深度学习或启发式规则**补，且高度依赖大规模多样化报文、能覆盖的字段语义有限、准确率偏低。ExeT 路线则从执行信息里挖语义：一路抓**库函数语义**（Dispatcher），另一路设计**启发式规则**分析字段执行轨迹、识别行为语义特征再映射到字段含义（Polyglot、Tupni）。

### 5. 加密流量：从"看内容"转向"看握手形状"

TLS 握手在明文里完成，于是**握手参数本身成了指纹**。

**JA3**（Salesforce 2017）取 ClientHello 中五个字段的**十进制字节值**，字段间用 `,`、字段内用 `-` 连接：

```
SSLVersion,Cipher,SSLExtension,EllipticCurve,EllipticCurvePointFormat
769,47-53-5-10-49161-49162-49171-49172-50-56-19-4,0-10-11,23-24-25,0
   → MD5 → ada70206e40642a3e4461f35503241d5（32 字符）
```

ClientHello 里没有扩展时字段留空（如 `769,4-5-10-9-100-98-3-6-19-18-99,,,`）。JA3 对 Google 的 **GREASE**（Generate Random Extensions And Sustain Extensibility）值**完全忽略**，保证随机扩展不影响同一客户端的指纹一致性。

**JA3S** 是服务端版本，只取 `SSLVersion,Cipher,SSLExtension`。`JA3 + JA3S` 组合可指纹化**整个密码学协商**——因为服务端对不同客户端响应不同，但对同一客户端的响应恒定。仓库给出的恒定样品：Tor `e7d705a3286e19ea42f587b344ee6865`、Trickbot `6734f37431670b3ab4292b8f60f29984`、Emotet `4d7a28d6f2263ed61de88ca66eb011e3`；Trickbot 的 C2 响应 JA3S = `623de93db17d313345d7ea481e7443cf`。即使目的 IP / 端口 / 证书不断变化，客户端应用指纹不变——这正是它比 IP/域名 IOC 更能识别 DGA、Twitter C2 的原因。

**局限与演进**：`salesforce/ja3` 仓库已于 2025-05 归档，维护权移交 FoxIO 的 **JA4**；JA3 本身可被 uTLS 之类的库**伪装**（伪造 ClientHello 形状），GREASE 也会污染朴素实现。到 TLS 1.3 + **ECH（Encrypted Client Hello）**，观察者只剩外层 ClientHello，SNI 被加密，"按 SNI 过滤"与"靠 DNS 检测"同时失效——于是又回到 JA3/JA4 这类**握手形状**指纹。

## 已完成 demo

| ID | Demo | 主题 |
| --- | --- | --- |
| 192 | [报文序列对齐/](./报文序列对齐/) | Needleman-Wunsch 全局对齐 + Smith-Waterman 局部对齐 + consensus 字段切分（C/Py/Go 中的 Python/Go） |
| 193 | [信息熵字段边界检测/](./信息熵字段边界检测/) | BinaryInferno：纵向列熵 + 熵差>1bit 边界 + 滑窗熵 + 单调序列启发式 |
| 194 | [协议状态机恢复/](./协议状态机恢复/) | Veritas P-PSM：类型标注 → 逐流转移计数 → 剪枝 → 流覆盖率回测 |
| 195 | [JA3与JA4指纹/](./JA3与JA4指纹/) | ClientHello 解析 + JA3(MD5)/JA4(排序+截断SHA-256) 计算，官方向量回环验证 |
| 196 | [Polyglot污点启发式/](./Polyglot污点启发式/) | token 表/分隔符三步提取/关键字/方向字段/定长合并（轨迹驱动复刻） |
| 372 | [Discoverer递归聚类/](./Discoverer递归聚类/) | tokenize（文本/二值、UTF-16LE）→ token 三元组初聚类 → 格式推断（常量/变量 + 长度/偏移语义）→ FD 三判据递归切分 → 基于类型的序列对齐合并（≤1 处失配） |
| 373 | [AutoFormat执行上下文/](./AutoFormat执行上下文/) | 上下文感知执行监视 + 污点传播（mov 继承/解除标记、算术逻辑并染）→ `<o,c,s,l>` 记录 → 协议字段树（偏移连续 + 同调用栈合并）→ 并行字段（执行史共享前缀 ≥80%） |
| 374 | [Tupni记录序列/](./Tupni记录序列/) | 最长连续污点字节 chunk + 权重（访问指令数）→ 加权最大 k-Set Packing 贪心 → 循环识别（CFG 单入口环）与迭代相关指令 → Figure 4 记录边界 → `Qi→Q'i` 子循环折叠出记录类型 |
| 375 | [ReFormat加密报文/](./ReFormat加密报文/) | 两阶段相位剖析（算术+位运算指令累计占比 → 分片段占比找转折点）→ 函数片段（同函数 + 同运行时栈帧）→ 数据生命周期（写集 ∩ 读集、分阶段活跃性规则）定位解密完成点 |
| 376 | [bit级字段切分/](./bit级字段切分/) | 联合基数 < 基数之积 → 位级相关性判据；RFC 1035 §4.1.1 的 16 位标志字切出 9 段 vs 字节粒度 2 段；常量位（Z/RCODE 高位）统计上不可判定 = 能力边界 |

## 待研究

- [x] TCP 报文序列对齐最小实现（Needleman-Wunsch / Smith-Waterman 在协议例子上的对拍）→ 192
- [x] 用信息熵 / 字节方差从对齐结果中切分字段边界 → 193
- [x] 从消息类型序列恢复 PFSM（状态合并 + 简化）→ 194
- [x] JA3 / JA3S 计算器（解析 ClientHello 五字段 → MD5）与 JA4 差异对拍 → 195
- [x] 动态污点分析视角：Polyglot 三类字段函数（方向字段 / 关键字 / 分隔符）的判定逻辑 → 196
- [ ] 用 Frida 在加密前/后抓明文的"增强版 Wireshark"路线（与 `02-移动端逆向` 互补）
- [x] bit 级字段（bitflag）的切分——现有工具普遍只做字节粒度 → 376
- [ ] 语义推断：长度/校验和/命令码的**原子语义检测器库**（BinPRE §3.4 路线）
- [ ] 字段约束求解：Tupni 的 symbolic predicate / functional / inter-message 三类约束
- [ ] 会话级格式抽取（Prospex：把 AutoFormat 从单报文扩到按系统调用切分完整会话）

## 参考资料（已读）

- [Protocol Reverse-Engineering Methods and Tools: A Survey — Computer Networks（ScienceDirect）](https://www.sciencedirect.com/science/article/pii/S0140366421004382) —— PRE 的两个视角分类（控制：主动/被动；输入：ExeT-based / NetT-based）、NetT-based 的适用条件（大量消息样本时快而出结果）、以及"状态机抽取依赖格式推断、字段切分是语义前提"的链条关系
- [BinPRE: Enhancing Field Inference in Binary Analysis Based Protocol Reverse Engineering — arXiv 2409.01994](https://arxiv.org/pdf/2409.01994v1) —— 第 5.1-5.3 节相关工作：NetT/ExeT 两族的定义与各自输入、格式抽取方法谱系（Netzob / Netplier 对齐、Discoverer 递归聚类、BinaryInferno 信息论、DynPRE 用响应报文、Polyglot 三类字段函数、AutoFormat 指令局部性、Tupni 循环内合并、Prospex 会话级）、语义推断的两条 ExeT 路线、BinPRE 自身的算子序列语义相似度与 0.87 / 0.75 / 0.84 的 F1、以及"只到字节粒度、处理不了 bitflag"的局限
- [PRE-list: List of (automatic) protocol reverse engineering tools — techge（71 篇论文合集）](https://github.com/techge/PRE-list) —— 工具/方法总览表与年份、所用方法的原始出处（PIP 2004 关键词检测 + Needleman-Wunsch / Smith-Waterman 对齐、ScriptGen 2005 消息聚类、RolePlayer 2006 字节级对齐 + FSM 简化、Polyglot 2007 动态污点分析、AutoFormat 2008 污点分析、Tupni 2008 用循环识别消息内边界、ReFormat 2009 针对加密协议的位运算/算术运算污点、Prospex 2009 提供 Peach fuzzer 候选、ReverX 2011 语音识别思路找分隔符 + 合并简化 PFSM、Veritas 2011 关键字 + 转移概率 → 概率协议状态机、ProDecoder 2012 Biprominer + Needleman-Wunsch）；该合集自述基于三篇综述（Narayan 2016 ACM CSUR、Duchêne 2018、Sija 2018）并持续扩充，另推荐 Kleber 2019 IEEE COMST 作为流量侧方法的起点
- [JA3 - A method for profiling SSL/TLS Clients — salesforce/ja3](https://github.com/salesforce/ja3) —— 五个字段的原始列表（SSL Version / Accepted Ciphers / List of Extensions / Elliptic Curves / Elliptic Curve Formats）、字段序 `SSLVersion,Cipher,SSLExtension,EllipticCurve,EllipticCurvePointFormat`、`,` 分字段 `-` 分值的连接规则、完整示例串与 MD5 结果、无扩展时字段留空、对 GREASE 值的忽略策略、JA3S 只取三字段与 "JA3+JA3S 可指纹化整个协商"、Tor/Trickbot/Emotet 的恒定指纹与 C2 响应 JA3S 示例、以及"仓库已归档、latest 在 FoxIO-LLC/ja4"的公告
