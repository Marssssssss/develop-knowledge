# 分析器链与中文分词（char filter → tokenizer → token filter）

## 一、简介

Elasticsearch 的 `text` 字段写入和查询时都要走一遍 **分析（analysis）**。分析器（analyzer）不是黑盒，它是三类构件的一个"包裹"：

```text
原始文本
  → char filter  (0..n 个，按配置顺序作用，改字符流)
  → tokenizer    (恰好 1 个，切出 token，并记录 position 与 offset)
  → token filter (0..n 个，按配置顺序作用，增删改 token，但不得改 position/offset)
  → 写入倒排表 / 参与查询
```

本 demo 用纯标准库把这三层 + 中文分词粒度 + `discount_overlaps` 对 norm 的影响跑通，回答四个问题：

1. 为什么 char filter 的顺序敏感、而 tokenizer 只能有一个；
2. 为什么"停用词删掉后位置不重排号"是硬性规则；
3. `ik_max_word` 与 `ik_smart` 到底差在哪（不是两套词典）；
4. 分词的粒度如何一路传导到 BM25 的分数上。

## 二、原理详解

### 2.1 三层构件各自的职责（官方文档明文）

| 构件 | 数量 | 职责 | 能不能改 position/offset |
| --- | --- | --- | --- |
| character filter | 0..n，**按序** | 在字符层面增删改写（去 HTML 标签、字符映射） | 还没有 token，谈不上 |
| tokenizer | **恰好 1** | 切词；**同时负责记录每个 term 的 position 与字符 offset** | 是产生方 |
| token filter | 0..n，**按序** | 大小写、停用词、同义词、词干…… | **不允许改**（官方原文） |

"token filter 不允许改 position/offset"这一条是整个短语查询（phrase query）能成立的前提：位置信息一旦在过滤阶段被重排，`"quick fox"` 这种带 slop 的短语匹配就会错位。

由此可推出一个常被忽略的后果：**停用词被删除后留下位置空洞**。存活 token 的 position 保持原值（本 demo 断言 `The Quick brown fox` 去掉 `the` 后 position 仍是 `1,2,3`，而不是 `0,1,2`）。

### 2.2 同义词与 overlap token

同义词展开出来的词与源词**共占一个 position**，即 `positionIncrement = 0`。这类 token 在 Lucene 里叫 **overlap token**，正好是 BM25 的 `discount_overlaps` 开关的处理对象（见 2.4）。

### 2.3 中文：ik_max_word 与 ik_smart 不是两套词典

IK 分词器主类 `IKSegmenter` 的关键一行是：

```java
this.arbitrator.process(context, configuration.isUseSmart());
```

即：**同一批词元先被 4 个子分词器（`Letter` / `SurrogatePair` / `CN_Quantifier` / `CJK`）产出，再由歧义裁决器 `IKArbitrator` 决定输出哪些**。`isUseSmart()` 是裁决的开关，不是另一套词典。

- `ik_max_word`：细粒度，把所有能参与某种完整切分的词元都吐出来（互相重叠）；
- `ik_smart`：粗粒度，只留裁决后的最优一条路径。

对 `中华人民共和国成立了`，本 demo 给出：

```text
ik_max_word : 中华人民共和国 / 中华 / 人民共和国 / 人民 / 共和国 / 共和 / 国 / 成立 / 了   (9)
ik_smart    : 中华人民共和国 / 成立 / 了                                                (3)
```

### 2.4 分词粒度 → 字段长度 → norm → BM25

`norms.html`：norm"大约每文档每字段 1 字节"，不打分就该关掉。

`index-modules-similarity.html`（BM25 为默认相似度）：

- `k1` 默认 **1.2**，控制 tf 饱和；
- `b` 默认 **0.75**，控制文档长度对 tf 的压制强度；
- `discount_overlaps` 默认 **true**：`position increment` 为 0 的 overlap token **不计入 norm**。

于是同一段中文，用 `ik_smart` 还是 `ik_max_word` 索引，字段长度 `dl` 与平均长度 `avgdl` 都不同，同一查询的分数也不同：

```text
smart    粒度 dl=[3,2] avgdl=2.50  score=0.640724
max_word 粒度 dl=[9,8] avgdl=8.50  score=0.676859
```

**工程结论**：索引期和查询期必须用同一个 analyzer（同一套粒度），否则 `dl/avgdl` 口径不一致，分数不可比。

## 三、对比：什么时候该动哪一层

| 需求 | 动哪一层 | 例子 |
| --- | --- | --- |
| 把 `<b>` 从正文里剥掉 | char filter | `html_strip` |
| 把 ① → 1 这类字符统一 | char filter（**顺序敏感**） | `mapping` |
| 决定"什么是一个词" | tokenizer | `standard`（按 Unicode 文本切分算法） |
| 大小写/停用词/同义词/词干 | token filter | `lowercase` / `stop` / `synonym` |
| 中文切分粒度 | tokenizer（IK 的 `useSmart`） | `ik_smart` / `ik_max_word` |

`standard` analyzer 的官方描述：按 Unicode 文本切分算法在词边界切分，去掉大部分标点，小写化，支持停用词移除。

## 四、环境

- Python 3.9+（仅标准库）；Go 1.21+；C（C99）。
- 无第三方依赖。

## 五、运行方式

```bash
cd python && python analyzer_chain.py      # 16 条断言，全部实跑通过
cd go     && go run .
cd c      && cc -std=c99 analyzer_chain.c -lm -o a.out && ./a.out
```

> 本机无 Go / C 工具链时，这两个版本走人工代码审查；Go 侧静态检查需带
> `python _docs/tools/go_sanity.py --spec check=2 <file>`（本 demo 的 `check` 是 2 参签名）。

## 六、关键代码

Python 侧把三层串起来就是一个 reduce：

```python
def analyze(self, text):
    for cf in self.char_filters:      # 0..n，按序
        text = cf(text)
    toks = self.tokenizer(text)       # 恰好 1
    for f in self.token_filters:      # 0..n，按序
        toks = f(toks)
    return toks
```

停用词保留位置空洞（不改 position）：

```python
def tf_stop(tokens, stops):
    return [t for t in tokens if t.term not in stops]   # 存活 token 的 pos 原样保留
```

`discount_overlaps` 对 norm 的作用：

```python
def norm_length(tokens, discount_overlaps=True):
    if discount_overlaps:
        return sum(1 for t in tokens if t.pos_inc != 0)
    return len(tokens)
```

## 七、性能边界与注意事项

1. **norm 是 1 字节/文档/字段的有损压缩**，只用于评分；精确长度存在别处，别用它做精确计算。
2. **`discount_overlaps=false` 会让同义词/重叠词把 `dl` 撑大**，`b=0.75` 下长文档被压得更狠 —— 同义词多的字段要留意这个副作用。
3. `b=0` 时长度归一化完全关闭：本 demo 断言 `dl=3` 与 `dl=9` 得分相同；`b=0.75` 下短文档更高。
4. **norms 关掉后不能再打开**（官方 Note：只能用 update mapping API 关，不能开回来）。
5. 本 demo 的中文分词是**最小模型**：词典穷举 + 最长匹配，不是 IK 的完整实现（IK 还有量词合并、歧义打分、远程词典热更新）。
6. **建模声明（非官方原文）**：IK 源码没有规定重叠词元的 `positionIncrement`。本 demo 按 Lucene 通用约定把不在主路径上的重叠词元标成 `pos_inc=0`，这样它恰好落在 `discount_overlaps` 的处理范围内，便于演示；真实 IK 的产物请以插件实际输出为准。

## 八、参考资料（均为本轮实际读过）

1. Anatomy of an analyzer — <https://www.elastic.co/guide/en/elasticsearch/reference/current/analyzer-anatomy.html>
2. Text analysis — <https://www.elastic.co/guide/en/elasticsearch/reference/current/analysis.html>
3. Built-in analyzer reference — <https://www.elastic.co/guide/en/elasticsearch/reference/current/analysis-analyzers.html>
4. Similarity module（BM25 `k1=1.2` / `b=0.75` / `discount_overlaps` 默认 true）— <https://www.elastic.co/guide/en/elasticsearch/reference/current/index-modules-similarity.html>
5. `norms` mapping parameter（约 1 byte/doc/field、不可重开）— <https://www.elastic.co/guide/en/elasticsearch/reference/current/norms.html>
6. IK `IKSegmenter.java`（4 个子分词器 + `IKArbitrator.process(ctx, cfg.isUseSmart())`）— <https://cdn.jsdelivr.net/gh/infinilabs/analysis-ik@master/core/src/main/java/org/wltea/analyzer/core/IKSegmenter.java>
