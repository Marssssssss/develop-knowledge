# Polyglot 污点分析启发式（token 表 / 分隔符 / 关键字 / 方向字段）

> 协议逆向 ExT 路线开山之作 **Polyglot（Caballero et al., CCS 2007）**：不抓流量，直接分析协议实现二进制的**执行轨迹**——"程序如何处理收到的数据，本身就泄露了消息格式"。核心是 shadowing：把网络输入的每个字节标为独立污点源，跟踪它们在指令里的流动，再用一组**启发式字段函数**从轨迹中恢复格式。本 demo 用"迷你执行轨迹"忠实复刻其四大启发式：token 表→分隔符提取、关键字提取、方向字段（长度字段）检测、定长字段合并。

## 一、简介

Polyglot 的消息格式模型：`消息 = 字段序列`，每个字段是一组**属性-值对**（论文 Table 1）：

| 属性 | 含义 |
| --- | --- |
| start | 字段在报文中的起始位置 |
| length | 定长（具体值）或变长 |
| boundary | 边界如何确定：`Fixed`（定长）/ `Direction`（由方向字段指出）/ `Separator`（由分隔符划界） |
| type | 是否为方向字段（length/pointer/counter） |
| keywords | 字段内含的协议关键字 |

## 二、原理详解（论文 §4-§6，本 demo 逐一实现）

### 2.1 token 表（§5.1 分隔符提取第一步）

扫描执行轨迹里**所有涉及污点字节的比较指令**，汇总成 token 表：**行 = 被比较的常量值（token），列 = 报文位置**；第 (t, p) 格打 X 表示"程序在某个时刻把位置 p 的字节与常量 t 做过比较"。实现用两张哈希表：`tokens-at-position`（每列的 token 有序列表）与 `token-series`（每行的位置列表）。

### 2.2 分隔符提取（三步）

1. **生成 token 表**（如上）；
2. **单字节分隔符**：直觉是"污点字节与非污点常量的比较可能就是分隔符判定"。对每个 token 取其被比较位置的连续段（series），**长度 ≥3** 才保留（滤掉偶发比较）；token 还必须在 series 的至少一个位置上真实出现过（防混淆）；
3. **扩展为多字节分隔符**：对候选分隔符的每次出现检查前驱/后继字节——若同一值总是伴随出现且程序也比较过它，就并入分隔符。**出现 <4 次不扩展、扩展上限 4 字节**（长分隔符罕见）。例：HTTP 的 `\r\n` 常由先匹配 `\n` 再回查 `\r` 组合发现。

### 2.3 关键字提取（§5.2）

只跟踪**为真的比较**（true comparison）：按位置升序扫描 tokens-at-position 表，某位置存在为真的比较 → 把该常量字节拼进当前关键字串；没有为真的比较或撞上分隔符 → 断开、另起一个关键字。协议常量（`GET`、`HTTP/1.1`、`Host`）必须与实现逐一比较才能分发，因此天然留下为真比较链。

### 2.4 方向字段检测（§4.1）

方向字段 = "存储另一字段（target）位置信息"的字段，包括 length / pointer / counter（如 DNS 报头 QDCOUNT、DNS 压缩指针）。检测直觉：**程序要用方向字段的值计算指针增量才能跳到变长字段末尾**。两种实现形态：

- **算术增量**：间接内存访问的**目标地址由污点数据计算而来**（base/index 寄存器带污点）→ 参与地址计算的连续字节 = 方向字段；被访问的最小位置 = target 字段末尾；
- **循环步进**：指针按常数步长推进直到停止条件——停止条件引用的污点值即方向字段。

### 2.5 定长字段合并（§6）

初始假设每个字节独立；若**一条指令同时使用多个位置的污点**（作为整体参与运算/比较），且这些位置不构成方向字段，则合并为一个定长字段。字节属于不同字段却出现在同一指令的例外是 length/pointer/checksum 这类特殊关系。

## 三、主动 vs 被动 / NetT vs ExeT 对比

| | Polyglot（ExeT） | NetT 方法（对齐/熵） |
| --- | --- | --- |
| 输入 | 协议实现二进制 + 少量会话 | 大量同类报文 |
| 语义 | 直接来自代码行为（长度/关键字/分隔符都是"程序用过的"） | 只能靠统计推断 |
| 样本量 | 一条会话即可出格式 | 需要覆盖各分支的多样本 |
| 局限 | 污点分析开销大；只恢复"扁平"结构（无嵌套/封装）；格式宽松时（HTTP 允许空格或 Tab 分隔）启发式会失真 | 变长字段位移、加密、样本不足 |

后续工作：AutoFormat（指令局部性切字段、支持嵌套）、Tupni（循环内合并）、Prospex（会话级+喂 Peach fuzzer）、BinPRE（算子序列语义相似度，小样本 F1=0.87）。

## 四、环境与运行

- Python ≥3.8：`python polyglot_heuristics.py`
- 输出：token 表、单字节/多字节分隔符、关键字、方向字段、定长字段、最终字段格式（含 Table 1 五属性）。

## 五、关键代码

```python
def extract_byte_separators(token_series, min_series=3):
    seps = []
    for tok, positions in token_series.items():
        for run in consecutive_runs(sorted(positions)):
            if len(run) >= min_series:
                seps.append((tok, run))     # 候选: 连续≥3 个位置都与同一常量比较
    return seps

def detect_direction_field(trace):
    for ev in trace.indirect_accesses:      # 目标地址由污点计算
        src = ev.addr_source_positions      # 参与地址计算的污点位置
        if src:
            return (src, min(ev.accessed))  # (方向字段位置, target 末尾)
```

## 六、性能边界

- 真实 Polyglot 的开销在**全系统污点跟踪**（QEMU + Temu 级别，每条指令都传播污点），一条 IRC 会话的轨迹就达 GB 级；启发式本身（token 表 + 扫描）是线性/近线性的。
- 本 demo 用人工构造的迷你轨迹（每条消息几十个事件）演示算法逻辑，不含污点引擎。

## 七、注意事项与常见坑

1. **连续段阈值 3 与扩展阈值 4 都是论文的经验值**，调小噪声激增、调大漏检；上限 4 字节是因为更长的分隔符罕见。
2. 关键字提取必须**只为真的比较拼串**——把"比较过但为假"也算上会把 switch-case 的所有候选分支常量（GET/POST/PUT…）错误拼成一个串。
3. 方向字段检测只记**最小被访问位置**为 target 末尾：同一方向字段被多次使用（多次间接访问）时只取第一次，论文明确该规则。
4. **格式宽松性是已知局限**（论文自述）：HTTP 允许空格或 Tab 作分隔符时，执行轨迹不一定符合启发式模式，推断出的格式不体现这种灵活性。
5. 定长合并的例外（checksum：跨字段使用多字节）会被误合并——需要先剔除方向字段再合并。
6. 多个有效分隔符（同一 scope）会造成"该 token 恰好在某位置为真"的歧义，Polyglot 通过要求 token 在 series 的至少一个位置出现来缓解。

## 八、参考资料（实际读过）

- [Polyglot: Automatic Extraction of Protocol Message Format using Dynamic Binary Analysis — CCS'07（全文 PDF）](https://www.cs.ucr.edu/~heng/pubs/polyglot-ccs07.pdf) —— §2 五属性字段模型与 Table 1；§4.1 方向字段定义（length/pointer/counter）与两种检测形态（算术增量/循环步进）、"间接访问地址由污点计算即标记方向字段、最小被访问位置为 target 末尾"；§5.1 token 表（tokens-at-position / token-series 双哈希表）与分隔符三步提取（series ≥3、扩展 ≥4 次、上限 4 字节、多分隔符 scope）；§5.2 关键字提取（只为真的比较拼串、按位置升序、遇分隔符断开、与配置信息区分靠文件读污点）；§6 定长字段合并（同一指令的污点位置合并、方向字段例外）；§7 五协议评测与"扁平结构、格式宽松性失真"局限
- [State of the art of network protocol reverse engineering tools — INRIA HAL（综述）](https://inria.hal.science/hal-01496958/file/jicv_SoA_ProtRE.pdf) —— Polyglot 启发式的外部视角复述（长度字段=上限比较、token 表识别分隔符与关键字）、"不推断封装结构"局限、AutoFormat/Prospex/Tupni 对其的扩展关系
- [Reverse engineering of industrial control protocol: A survey — Security and Safety](https://sands.edpsciences.org/10.1051/sands/2025012) —— Polyglot 在 ICP 逆向谱系中的位置：不依赖网络流量、程序级动态分析的代表；与 Prospex（会话级联合抽取）的分工
- [PRE-list — GitHub（71 篇 PRE 论文合集）](https://github.com/techge/PRE-list) —— Polyglot 2007 动态污点分析、AutoFormat 2008、Tupni 2008、Prospex 2009 的方法出处索引（本目录 README 已读，此处用于工具谱系核对）
