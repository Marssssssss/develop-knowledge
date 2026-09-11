# Prometheus 指标模型与 exposition 文本格式

## 简介

- Prometheus 通过**周期性拉取（scrape）**目标进程暴露的指标文本，本文的 text format 0.0.4 是 2014 年至今的默认传输格式（Prometheus >=0.4.0，Content-Type `text/plain; version=0.0.4`）。
- 数据模型核心：`指标名 + 标签集 (label set)` 唯一确定一条**时间序列**，每个样本是 `float64 值 + 可选 int64 毫秒时间戳`。
- 关键概念：
  - **MetricFamily**：同名指标的一组样本 + 元信息（HELP 文档串 / TYPE 类型）
  - **四种类型**：counter（只增计数）/ gauge（可增减瞬时值）/ histogram（桶计数）/ summary（分位数）
  - **histogram 文本展开约定**：`x_bucket{le="上界"}` 累积桶 + `x_sum` + `x_count`，必须有 `le="+Inf"` 桶
  - **转义规则**：标签值中 `\\`、`\"`、`\n` 三者必须转义
- 本 demo 实现一个符合官方规范的 0.0.4 文本解析器，并校验 histogram 的三条不变式。

## 原理详解

### 文本格式分步机制（依据官方 exposition_formats 规范）

1. **行导向**：`\n` 分行，最后一行必须以 `\n` 结尾；空行忽略；行内 token 用任意空白/Tab 分隔，前后空白忽略。
2. **注释行**：以 `#` 开头（首个非空白字符）即注释；但 `# HELP <指标名> <文档串>` 与 `# TYPE <指标名> <类型>` 例外，是元数据：
   - HELP 的 docstring 中 `\` 与换行需转义为 `\\` 与 `\n`；同一指标名**只允许一条 HELP**。
   - TYPE 第二个 token 只能是 `counter|gauge|histogram|summary|untyped`；**必须出现在该指标第一个样本之前**；否则类型按 `untyped` 处理。
3. **样本行 EBNF**：`metric_name_or_labels value [ timestamp ]`
   - `metric_name_or_labels = metric_name [ "{" labels "}" ]`
   - 标签值是双引号包裹的转义字符串（`\\` `\"` `\n`）。
   - `value` 按 Go `ParseFloat()` 规则解析，**额外允许 `NaN` / `+Inf` / `-Inf`**。
   - `timestamp` 是 int64 **毫秒**（1970 epoch，不含闰秒），可省略（由 scrape 时刻补）。
4. **分组**：同一指标的所有行必须连续成组，HELP/TYPE 在组首；同一 `指标名+标签` 组合重复出现时**ingestion 行为未定义**。
5. **histogram/summary 展开**（文本格式最难表达的部分，用约定解决）：
   - `x_sum`：观测值总和；`x_count`：观测次数；
   - summary 的每个分位数：同名 `x` + 标签 `{quantile="y"}`；
   - histogram 每个桶：`x_bucket` + 标签 `{le="上界"}`，**桶按上界升序**，且必有 `le="+Inf"`，其值**必须等于** `x_count`（桶是累积计数）。

### 解析流程 ASCII 图

```text
原始文本 ──按行切分──► 逐行分类
   │                      ├── '#' 开头 ──► HELP / TYPE / 普通注释
   │                      └── 样本行
   │                             └── 右侧切出 [value][ts]，左侧为 name{labels}
   │                                     └── 反转义标签值 (\\ \" \n)
   ▼
按指标名聚合为 MetricFamily {name, type, help, samples[]}
   │
   ▼ 类型 == histogram ?
├── 提取 <name>_bucket 系列，按 le 排序
├── 校验 1：最高桶必须为 le="+Inf"
├── 校验 2：+Inf 桶值 == <name>_count 值
└── 校验 3：桶计数单调不减（累积语义）
```

### 核心 API（解析器内部约定）

| 函数 | 签名要点 | 说明 |
| --- | --- | --- |
| `parse_exposition(text)` | → families | 主入口，返回按名聚合的 MetricFamily |
| `parse_sample_line(line)` | → name, labels, value, ts | 从右往左切 value/ts，剩余为指标部 |
| `unescape(s)` | → s' | 处理 `\\` `\"` `\n` 三种转义 |
| `parse_value(str)` | → float | `NaN/+Inf/-Inf` 合法（三语言原生 strtox 均支持） |

## 对比 / 选型

| 维度 | text 0.0.4 | OpenMetrics 1.0 | protobuf |
| --- | --- | --- | --- |
| 可读性 | 人类可读、可 curl | 可读 + `# EOF` 结束符 | 二进制 |
| 类型契约 | TYPE 行可选，校验弱 | 类型强制、含 UNIT/INFO/GaugeHistogram | 强类型 |
| 传输 | HTTP GET /metrics，可选 gzip | 同左 + 内容协商 | Accept 协商（proto=io.prometheus.client.MetricFamily;encoding=delimited） |
| 现状 | 默认兜底格式 | 2020 起推广，向 IETF 推进 | 幕后协商 |

OpenMetrics 2.0（实验）进一步把 histogram 合并为单行 CompositeValue `{count:…,sum:…,bucket:[…]}`，并以 `st@` 内联起始时间戳取代 `_created` 样本。

## 环境准备

- 操作系统：任意（纯文本解析，无系统调用依赖）
- 语言版本：C99 / Python 3.8+ / Go 1.20+
- 依赖：无（全部标准库）

## 运行方式

### C

```bash
gcc -O2 -Wall -Wextra -o expose_demo c/expose_demo.c && ./expose_demo
```

### Python

```bash
python3 python/expose_demo.py
```

### Go

```bash
cd go && go run expose_demo.go
```

## 关键代码片段（Python 版）

```python
def parse_sample_line(line):
    """从右往左切分：[..., value, ts?] —— 规范 EBNF: name{labels} value [timestamp]"""
    tokens = line.split()
    has_ts = len(tokens) >= 3
    value = parse_value(tokens[-2] if has_ts else tokens[-1])
    ts = int(tokens[-1]) if has_ts else None
    name, labels = parse_metric_part(" ".join(tokens[: len(tokens) - (2 if has_ts else 1)]))
    return name, labels, value, ts

def unescape(s):
    """标签值只定义三种转义：\\\\ \\" \\n（官方规范明文列举）"""
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            out.append({"n": "\n", '"': '"', "\\": "\\"}.get(s[i + 1], s[i + 1]))
            i += 2
        else:
            out.append(s[i]); i += 1
    return "".join(out)
```

## 性能与边界

- 解析是 O(行数)；单行限长由实现自定（本 demo 512 字节）。
- 官方明确局限（Inception 表）：**类型与文档串不是语法的强组成部分**（契约校验弱）、**解析成本高于二进制**、格式较冗长。
- 时间戳为毫秒 int64；`NaN` 不是"缺失值"，只表示"非数"（OpenMetrics 语义：可用于表示除零）。
- 同一指标名+标签组合重复 → ingestion 行为 undefined，解析器应视为非法输入。

## 注意事项与常见坑

1. **`le="+Inf"` 桶缺失或值 ≠ `_count`** → 这是数据错误；官方规定 +Inf 桶值必须与 `_count` 完全一致（本 demo 校验 2）。
2. **桶必须按 le 升序输出**；乱序是违反规范的 exposition，严格解析器应拒绝或重排。
3. **TYPE 行晚于首个样本出现** → 违反规范（"must appear before the first sample"），宽松实现只能事后补类型，严格实现应报错。
4. **转义只有三种**：`\\` `\"` `\n`。标签值里的真实换行/引号/反斜杠必须转义；HELP 的 docstring 只需转义 `\\` 和 `\n`（引号无需）。
5. **`+Inf` 字面量**：Go `strconv.ParseFloat`、Python `float()`、C `strtod` 都原生接受 `+Inf`/`NaN`（大小写不敏感），不要自己写特判——但 C 的 `strtod` 不跳过前导空白，需先 trim。
6. **毫秒 vs 秒**：text format 时间戳是毫秒，OpenMetrics 是秒；混用两套格式时最容易踩的时间单位坑。
7. **样本行 token 切分**：规范允许 token 间任意多个空格/Tab，且行首行尾空白忽略——从右往左切分 value/ts 最稳（标签值里可以有花括号和空格，不能简单 split 空格）。

## 参考资料（实际阅读过的权威来源）

- [Exposition formats — prometheus.io 官方文档](https://prometheus.io/docs/instrumenting/exposition_formats/) — text 0.0.4 全部语法规则（EBNF、HELP/TYPE 约束、转义、histogram 展开约定、官方完整示例）
- [Scrape protocol content negotiation — prometheus.io](https://prometheus.io/docs/instrumenting/content_negotiation) — 五种协商协议与 Accept 头质量值构造
- [OpenMetrics 1.0 Spec — prometheus.io](https://prometheus.io/docs/specs/om/open_metrics_spec/) — 数据模型（MetricFamily/Metric/MetricPoint）、NaN 语义、时间戳单位对比
- [OpenMetrics 2.0 Migration Guide — prometheus.io](https://prometheus.io/docs/guides/open_metrics_2_0_migration) — st@ 起始时间戳与 CompositeValue 演进
