# JSONB 半结构化与 GIN 索引

## 一、简介

「要不要把这个字段拆成列」是表设计里的高频争论。PostgreSQL 的 `json`/`jsonb` 给出了折中方案，但两者差别极大：

- `json` 存的是**原文副本**，每次处理都要重新解析；
- `jsonb` 存的是**分解后的二进制**，输入慢一点、处理快得多，而且**唯一能建索引**的那个。

更麻烦的是 `jsonb` 的语义细节：包含运算符 `@>` 对嵌套结构有一套「越级就不算」的规则，而 GIN 上的两个操作符类支持的运算符还不一样。本 demo 按 PostgreSQL 18《8.14. JSON Types》与《65.4. GIN Indexes》做成可执行模型，43 条断言逐条钉死。

## 二、原理详解

### 2.1 json 与 jsonb 的差异

| 维度 | `json` | `jsonb` |
| --- | --- | --- |
| 存储 | 输入文本的精确副本 | 分解后的二进制 |
| 空白 / 键序 | 保留 | **不保留**（键会被排序） |
| 重复键 | 全部保留（处理函数取**最后一个**） | 只留最后一个 |
| 索引 | 不支持 | 支持（GIN） |
| `\u0000` | 允许 | **拒绝**（无法用 text 表示） |
| 非法代理对 | 不检查 | **拒绝**（合法代理对会被折叠成单字符） |
| 数字 | 原文照存 | 映射为 `numeric`，**超出 numeric 范围直接报错** |

官方特意提醒的互操作风险：**很多系统用 IEEE 754 double 表示 JSON 数字**，与 PG 的 `numeric` 互换时可能丢精度；`1.230e-5` 在 jsonb 里打印出来是 `1.23e-05`（按 numeric 的打印规则）。

### 2.2 包含 `@>` 的三条规则

官方定义：*the contained object must match the containing object as to structure and data contents, possibly after discarding some non-matching array elements or object key/value pairs*。落到实现上是三条：

1. **对象包含对象**：被包含的每个键都要在包含者里出现且值也「包含」；
2. **数组包含数组**：被包含的每个元素都要能在包含者的元素里找到（**顺序不重要**，重复元素只算一次）；
3. **越级不算**：嵌套结构必须作为**整体元素/值**匹配。

于是有这些官方点名的结果：

```sql
SELECT '[1, 2, [1, 3]]'::jsonb @> '[1, 3]';    -- false（[1,3] 不是顶层元素）
SELECT '[1, 2, [1, 3]]'::jsonb @> '[[1, 3]]';  -- true
SELECT '{"foo": {"bar": "baz"}}'::jsonb @> '{"bar": "baz"}';  -- false
SELECT '{"foo": {"bar": "baz"}}'::jsonb @> '{"foo": {}}';     -- true
```

还有一个**唯一的例外**：数组可以包含顶层标量，而且**不可反向**：

```sql
SELECT '["foo", "bar"]'::jsonb @> '"bar"';   -- true
SELECT '"bar"'::jsonb @> '["bar"]';          -- false
```

### 2.3 存在 `?`

`?` 是包含的变体：判断字符串是否作为**顶层**对象键或顶层数组元素出现。`'{"foo": {"bar": "baz"}}' ? 'bar'` 是 false —— 嵌套键不算。

### 2.4 GIN 的两个操作符类

| | `jsonb_ops`（默认） | `jsonb_path_ops` |
| --- | --- | --- |
| 索引条目 | **键**与**值**各自成条目 | 路径 + 值**哈希成一条** |
| 支持 `@>` | 是 | 是 |
| 支持 `?` `?|` `?&` | **是** | **否** |
| 支持 `@?` `@@`（jsonpath） | 是 | 是 |
| 体积 / 性能 | 条目多、体积大 | 条目少、对 `@>` 更快 |

选错操作符类的典型症状是：`jsonb_path_ops` 索引上的 `?` 查询**不走索引**（因为根本不支持）。

### 2.5 fastupdate 与 pending list

GIN 是倒排索引，插一行会引发大量索引条目插入。官方的缓解办法是先写进一个**未排序的 pending list**：

- 表被 vacuum / autoanalyze、调用 `gin_clean_pending_list`、或 pending list 超过 `gin_pending_list_limit` 时，才批量合并进主结构；
- **查询必须同时扫主结构和 pending list**，否则会漏（本 demo D6 断言）；
- 代价：pending list 越大查询越慢；而**触发前台清理的那一次更新会明显更慢**（响应时间的毛刺就来自这里）；
- 关掉 `fastupdate` 可以换取稳定的响应时间，代价是更新变慢。

另外 GIN 是 **lossy** 的：索引只保证「候选不漏」，最终必须 **recheck** 原始数据（本 demo `search()` 同时返回候选集与真结果，并断言候选集是超集）。

## 三、对比：什么时候该用 jsonb

| 场景 | 建议 |
| --- | --- |
| 结构稳定、要参与约束/连接/聚合 | 拆成列 |
| 结构多变、只做整体读写与少量过滤 | `jsonb` + GIN |
| 只做存储与转发、从不查询内部 | `json`（省一次转换） |
| 需要对内部字段做范围查询 / 排序 | 拆成列，或用生成列 + btree 索引 |
| 需要精确还原原始文本（审计、签名） | `json`（`jsonb` 会丢空白与键序，原文不可复原） |

MySQL 也有 `JSON` 类型与多值索引（本轮 `dev.mysql.com` 返回 “Technical Difficulties”，三条通道都没取到正文，故 MySQL 侧只作文献著录、不参与断言）。

## 四、环境

- Python 3.8+（仅标准库）
- Go 1.21（无外部依赖）

## 五、运行方式

```bash
cd python && python selfcheck_jsonb.py    # 43 条断言
cd go     && go run .
```

## 六、关键代码

**包含里的「越级不算」**（`python/main.py`）：

```python
def contains(doc, needle):
    if isinstance(needle, list):
        if not isinstance(doc, list):
            return False               # 数组包含顶层标量是唯一例外，且不反向
        return all(_array_contains_one(doc, e) for e in needle)
```

**两个操作符类的条目抽取差别**（`python/main.py`）：

```python
if self.opclass == "jsonb_ops":
    out.append("K" + prefix + "." + k)      # 键单独成条目
else:
    out.append("H" + prefix + "=" + repr(v))  # 路径+值哈希成一条
```

## 七、性能边界

- **jsonb 输入有转换成本**：写入密集且从不查内部字段时，`json` 更划算（省掉分解这一步）。
- **TOAST 门槛**：单行超过约 2 KB 后 jsonb 会被压缩/移出行外，更新大 JSON 文档会写出**整份新版本**，写放大明显。尽量只更新小字段（用 `jsonb_set` 也会重写整份）。
- **GIN 的 pending list 是把双刃剑**：`gin_pending_list_limit` 调大能减少前台清理次数，但一旦触发前台清理，那一次的耗时会随调大而变长；同时查询要扫的 pending 也更长。
- **`@>` 的代价与候选数相关**，而候选数是「索引条目命中数」，不是行数。高频值（如 `{"status": "ok"}`）会让候选集远大于真结果，recheck 成为瓶颈。
- **`jsonb_path_ops` 更小更快，但丢了 `?`**。混合需求可以建两个索引，代价是写入放大两倍。

## 八、注意事项与常见坑

1. **`jsonb` 不可复原原文**：键序、空白、重复键全丢。做审计/签名留存请用 `json`。
2. **`\u0000` 与非法代理对在 jsonb 下是硬错误**，从外部系统导入时容易在边界数据上炸掉。
3. **数字一律走 numeric**：`1` 与 `1.0` 相等，但 `1e999999` 直接报错；与 JS 侧交换大整数要留意精度。
4. **`?` 运算符在 SQL 里容易被当成占位符**：在不少驱动里要写成 `??` 或用 `jsonb_exists()` 函数。
5. **嵌套匹配不越级**：`{"foo":{"bar":1}}` 不被 `{"bar":1}` 包含，但被 `{"foo":{}}` 包含。
6. **数组包含标量不可反向**：`'["a"]' @> '"a"'` 真，`'"a"' @> '["a"]'` 假。
7. **Go 版里 `nil` 有两义**：JSON 的 `null` 与「键不存在」都用 nil 表示，判断存在性必须显式取 `ok`。
8. **GIN 是 lossy 的**：永远不要直接把候选集当结果，必须 recheck。

## 九、参考资料（本轮实际读过）

- PostgreSQL 18 官方文档 — 8.14. JSON Types（json/jsonb 差异、包含与存在、下标）
  <https://www.postgresql.org/docs/18/datatype-json.html>
- PostgreSQL 18 官方文档 — 65.4. GIN Indexes（两个 jsonb 操作符类、fastupdate 与 pending list）
  <https://www.postgresql.org/docs/18/gin.html>
- MySQL 8.0 官方文档 — 12.18 JSON Functions
  <https://dev.mysql.com/doc/refman/8.0/en/json.html>
  （本轮站点返回 “Technical Difficulties”，未取到正文，仅作文献著录）
