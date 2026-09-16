# 二进制差分与函数匹配(BinDiff 原理)

## 一、简介

同一个程序编译两次、或跨版本编译,函数地址、立即数、甚至符号都会变,但**结构**往往还在。
二进制差分要回答的就是:两份二进制里,哪些函数是同一个?本 demo 用纯 Python + Go 复刻
BinDiff 的核心机制,不做真实二进制解析,重点论证**"为什么这些指纹能对上"**:

- **prime signature** —— 助记符序列的乘积指纹,耐改(改立即数、改顺序都还在);
- **MD index** —— 顶点在图中的拓扑位置指纹,是多个算法的公共基础;
- **分阶段匹配 + drill down** —— 高置信算法先跑,歧义交给下一档,再用调用关系缩小候选集。

## 二、原理详解

### 2.1 结构签名:官方三元组

官方 `docs/concepts.md` 原文:

> Every function gets a signature, based on the structure of the (normalized) flow graph
> of the function. The signature consists of: Number of basic blocks / Number of edges
> between basic blocks / Number of calls to sub-functions

即 `(基本块数, 块间边数, 调用子函数次数)`。**它太弱**,只配用来缩小候选集 ——
本 demo 第 3b 节专门演示:两档强指纹跑完后,再施加结构签名,会把"被删的函数"和
"新增的函数"错误配上(`(1,0,0)` 与 `(1,0,0)` 相同)。这正是 confidence 存在的理由。

### 2.2 prime signature:为什么它比哈希耐改

官方原文:

> Each mnemonic gets assigned a unique small prime number. These primes are multiplied
> for all instructions of the function. This yields a structurally invariant, instruction
> order independent product.

每条助记符 → 一个唯一小素数,**整个函数的所有素数相乘**;乘积对指令顺序不敏感
(乘法交换律),所以块内指令被重排不影响指纹,对**立即数变化也免疫**。

对照 hash matching(官方列在最高 match quality 一档):它匹配"原始函数字节的哈希",
两个函数在字节层面必须相同或几乎相同,改一个立即数就整体失配 —— 两者正好互补。

**口径分歧(如实标注)**:manual 写 product,而 `call_graph.cc` 等源码走读资料(以及
block 级的 Prime matching)描述为"块素数之和 / 指令素数之和"。本 demo 两种都实现,
并给出改用求和的硬理由:product 数值会**迅速溢出**。`binary_diff.go` 直接断言了这点 ——
64 条素数 2 的指令相乘,`uint64` 里恰好归零。

### 2.3 MD index:官方公式

官方仓库 `match/graph_util.h` 的 `CalculateMdIndexInternal`:

```
md_index(edge) = sqrt(w0)*in_deg(src) + sqrt(w1)*out_deg(src)
               + sqrt(w2)*in_deg(tgt) + sqrt(w3)*out_deg(tgt)
               + sqrt(w4)*level(src) + sqrt(w5)*level(tgt)
返回 1.0 / md_index;默认权重 kDefaultWeightsNode = {2, 3, 5, 7, 0, 0}
```

- **顶点值 = 其所有入边与出边之和**,且求和**先排序** —— 源码注释写明
  "Summation is not commutative for doubles";
- 取**倒数**把边强度压进 0 与 1 之间,使"局部度小的边"权重更大;
- 官方提供三种变体:`top-down`(按入口点分层)、`bottom-up`(按出口点分层)、
  `relaxed`("calculated without taking topological order into account")。

本 demo 由公式直接推出一个结论并断言:因为 `kDefaultWeightsNode` 的**拓扑层系数是 0**,
默认权重下 `top-down` 与 `bottom-up` 结果**完全一致**;把层系数调成非零(11/13)后两个方向
才分道扬镳。同理 `relaxed` 与默认权重也等价 —— 层系数为 0 时两者是同一件事。

### 2.4 分阶段匹配与 drill down

官方描述的三种结局:属性在**两份二进制里都唯一** → 建立匹配;两侧都出现多次 → **歧义**,
进入 drill down;另一侧没有对应 → 留在未匹配集合。drill down 的关键是**候选集被大幅缩小**:

> If a match is known, the subsets of all functions called from a matched function are
> examined. These subsets are significantly smaller than the set of all functions, thus
> the probability for finding new unique matches is a lot higher.

第 4 节构造了最能说明问题的一组:`worker` 与 `twin` 指纹**完全相同**(两侧都歧义,
任何指纹算法都配不上),但 `worker` 是已匹配函数 `entry` 的**唯一 callee**,
于是一条调用关系就把它解开了,而 `twin` 仍留在未匹配集合里。

### 2.5 官方算法清单(按 match quality 降序)

| 算法 | 依据 | quality |
| --- | --- | --- |
| Hash matching | 原始函数字节的哈希 | very good |
| Name hash matching | 函数名哈希(不含自动生成名) | very good |
| Edges flow graph MD index | 边上源/目标函数的 flow graph MD index | very good |
| Edges call graph MD index | 该调用点之前调用图的结构 | good |
| MD index(flow graph, top-down/bottom-up) | flow graph 中的拓扑位置 | good |
| Prime signature matching | 指令素数乘积 | good |
| MD index(call graph, top-down/bottom-up) | 调用图中的位置 | good |
| Edges proximity MD index | 只跟两跳内的局部邻域 | medium |
| Relaxed MD index matching | 不计拓扑序的 MD index | medium |
| String references | 引用的字符串集合(至少引 1 个才参与) | medium |
| Address sequence | 按入口地址顺序(见下两条约束) | poor |
| Loop count matching | 循环个数(至少 1 个循环才参与) | poor |
| Call sequence matching | 调用点的 `(块层级, 块内序号, 地址)` | very poor |

两条易踩的边界:

- **edge matching 更强但可能很慢**:"edge matching is the stronger criterion, yielding
  better matches in general",但 call graph 的边数常随顶点数**超线性增长**,所以
  性能出问题时应"first try to disable edge matching based algorithms"。
- **address sequence 不能单独用**:不加约束它会不分青红皂白地把所有函数按地址配上,
  故官方要求先与 relaxed MD index 和 flow graph MD index 一致,且两侧等价函数集合**大小相等**。

### 2.6 confidence 与 similarity

> The confidence value displayed by BinDiff is the average algorithm confidence (match
> quality) used to find a particular match weighted by a sigmoid squashing function.
> The values aren't simply averaged because few single weak matches in an otherwise
> perfectly matched function/binary shouldn't drag the confidence down too much.
> Analogously, even a few strong matches will not "rescue" a binary pair ...

即**先取均值,再用 S 型函数压扁**:两侧饱和、中间陡。断言把这段自然语言变成了可执行性质
(10 个 0.95 里混一个 0.10,压扁后的偏移小于线性均值的偏移;反过来一个 0.90 也救不起 9 个 0.20)。

相似度权重:function 是 边 25% / 块 15% / 指令 10% / **flow graph MD 差异 50%**;
binary 是 边 35% / 块 25% / 函数 10% / 指令 10% / **call graph MD 差异 20%**,
最后**都再乘 confidence**。binary 版本明确"只统计非库函数",以避免"共用同一套运行库"
把相似度抬虚。

## 三、与其它方案的对比

| 方案 | 关注点 | 本 demo 对应 |
| --- | --- | --- |
| 字节哈希 / md5 | 完全相同才算同 | `byteHash`:改一个立即数即失配 |
| 助记符哈希 | 忽略块内顺序差异 | `flowgraphHash` |
| 结构签名三元组 | 极快但极弱 | 3b 节演示它造成的假匹配 |
| MD index | 拓扑位置 | 2.3 节,含方向性推论 |
| 图编辑距离 | 理论最优但 NP-hard | 故工业界退回指纹 + drill down |

## 四、环境与运行

Python 3.8+(本机 3.13.12),仅标准库;Go 1.18+(用到泛型)。三个自检互相独立,退出码 0 即全通:

```bash
python bindiff_pipeline.py     # 指纹 + 匹配流水线 + drill down + MD index
python bindiff_metrics.py      # confidence 压扁 + similarity 权重
go run *.go                    # Go 同题实现(结构与数值须与 Python 一致)
```

## 五、关键代码

```python
def staged_match(A, B, algos, lib=("libc_start",)):
    """按置信度从高到低施加指纹;只接受「在两侧候选集中都唯一」的签名。"""
    for name, conf, sig in algos:
        sa = {f: sig(A, f) for f in A if f not in matched and f not in lib}
        for val in set(sa.values()) & set(sb.values()):
            if len(fa) == 1 and len(fb) == 1:      # 两侧都唯一才接受
                matched[fa[0]] = (fb[0], name, conf)

def propagate(A, B, matched):
    """drill down:已匹配函数的唯一未匹配 callee → 直接补上。"""
    if len(kids_a) == 1 and len(kids_b) == 1:
        matched[kids_a[0]] = (kids_b[0], "drilldown", matched[a][2])
```

## 六、性能边界

- prime 的 product 口径随指令条数**指数增长**,64 条就能撑满 `uint64`,真实实现必须用大整数
  或改成求和;
- MD index 每条边都要数入度(`inDeg` 是 O(E)),整图一轮 `O(V·E)`;真实实现预存
  `in_deg/out_deg/level` 属性避免重复遍历;
- edge matching 的候选数与边数同阶,是最先该关掉的一档(见 2.5);
- 匹配要**迭代到不动点**才停(每档跑完用新匹配继续 drill down),不是一遍扫描。

## 七、注意事项与常见坑

1. **指纹算法必须能"弃权"**。不引用字符串的函数若返回空集合,所有空集合彼此"相等"会互相配上。
   官方 string references 就写了"至少引用 1 个"才参与,loop count 也要求"至少有 1 个循环"。
   本 demo 用 `sig()` 返回 `None` 表示弃权,并在流水线里过滤。
2. **`(1,0,0)` 是最危险的签名**。孤立小函数的结构签名全都一样,必须放最低优先级,
   且它的匹配结果会被 confidence 压到最低。
3. **`md_of_functions` 要的是邻接表,不是块字典**。传成 `{"b0": {...}}` 后,
   `for m in graph.get(v, [])` 会退化成**遍历字典的键**,把块名当成边,每个函数都"多出一条边",
   孤立函数的 MD index 不再是 0。本 demo 第一版踩了这个坑,断言当场失败。
4. **匹配结果的元组方向**。`matched[A 的函数名] = (B 的函数名, 算法, 置信度)`,
   断言时容易写错下标(本 demo 第一版把 `[0]` 写成 `[1]`,4 条断言齐刷刷 FAIL)。
5. **浮点求和不满足交换律**,MD index 求和前必须排序,否则同一份图可能算出不同数值,
   匹配结果会偶发漂移。
6. **默认权重的拓扑层系数为 0**,所以不做对照实验就看不出 top-down/bottom-up、
   relaxed/完整的区别,容易得出"算法对拓扑序不敏感"的错误结论。
7. **只在剩余未匹配集合上跑下一档**。每档从全量开始会反复重算已确定的 fixed point,
   既慢又可能让弱算法覆盖强算法的结果。

## 八、参考资料

1. BinDiff 官方 `docs/concepts.md`(总体策略、算法清单、confidence/similarity 权重):
   <https://raw.githubusercontent.com/google/bindiff/main/docs/concepts.md>
2. `match/graph_util.h` 的 `CalculateMdIndexInternal`(MD index 精确公式与默认权重),
   官方 raw 直取 404,经镜像读取:<https://github.com/codingman/bindiff/blob/main/graph_util.h>
3. google/bindiff 主仓库(zynamics BinDiff 开源版):<https://github.com/google/bindiff>
4. Thomas Dullien, Rolf Rolles. *Graph-Based Comparison of Executable Objects*. SSTIC '05;
   Halvar Flake. *Structural Comparison of Executable Objects*. DIMVA 2004 ——
   均见官方 concepts.md 的 Further reading,MD index 思想的原始出处。
