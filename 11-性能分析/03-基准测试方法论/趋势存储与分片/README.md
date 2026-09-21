# 基准结果的长期趋势存储与分片

> 待研究项落地：`基准结果的长期趋势存储与分片（benchstat 之外的时序方案，如 Skia Perf / Firefox Perfherder 的数据模型）`。因为 benchstat 只做**一次比较**，把它当趋势库会立刻撞上"系列怎么标识、怎么分片、怎么回填"三件事。本 demo 逐行实读两套真实系统的源码并做成可执行模型。

## 简介

| 维度 | Mozilla Perfherder | Chrome Perf（Catapult dashboard） |
| --- | --- | --- |
| 系列标识 | 40 字符 SHA-1（`signature_hash`，内容寻址） | **完整路径字符串** `master/bot/test/metric/page` |
| 分片键 | `(repository, signature, push_timestamp)` 复合索引 | test path 分实体，`Row` 主键含 `revision`（整数） |
| 父子关系 | `parent_signature` 外键 + `has_subtests` 布尔 | **路径前缀推导**（`parts[:-1]`），无外键 |
| 额外维度 | `extra_options`(422) / `tags`(360) 两个自由字段 | `Expando` 的 `d_` / `r_` / `a_` 前缀补充列 |
| 改名成本 | 换 hash = 换系列，历史断开 | 路径是主键，改名要走迁移脚本 |

## 原理详解

### 1. Perfherder 的签名哈希：key 与 value 混在一起排序

```python
def _get_signature_hash(signature_properties):
    signature_prop_values = list(signature_properties.keys())
    str_values = []
    for value in signature_properties.values():
        if not isinstance(value, str):
            str_values.append(json.dumps(value, sort_keys=True))
        else:
            str_values.append(value)
    signature_prop_values.extend(str_values)          # ← 键和值进了同一个列表

    sha = sha1()
    sha.update("".join(map(str, sorted(signature_prop_values))).encode("utf-8"))
    return sha.hexdigest()
```

`list(keys)` 之后 `extend(str_values)`，然后**一次 `sorted`**——键与值被当成同一个多重集排序拼接。后果是两个真实可复现的性质（自检 E2）：

- **取值在属性间对调，hash 不变**：`{"suite":"tp6","test":"facebook"}` 与 `{"suite":"facebook","test":"tp6"}` 给出同一个 hash；
- **键当值、值当键，hash 也不变**：`{"a":"1"}` 与 `{"1":"a"}` 同一个 hash。

这不是安全问题（属性名与取值空间不重叠），但它意味着"hash 相同"不能反推出属性集合——**只适合做主键，不适合做校验**。

另一个细节：`json.dumps(..., sort_keys=True)` 只排 **dict 的键**，**列表保持原序**。所以 `{"test_options": ["e10s","webrender"]}` 与 `{"test_options": ["webrender","e10s"]}` 是**两个不同系列**（自检 E5e）。官方在调用侧补了一次 `sorted`：

```python
suite_extra_properties = {"test_options": sorted(suite["extraOptions"])}
suite_extra_options = _order_and_concat(suite["extraOptions"])   # " ".join(sorted(...))
```

少这一次 `sorted` 就会把同一组选项裂成两条历史线——而它的表现是"某个系列的数据从某天起断了"，很难查。

### 2. summary 与 subtest：父子关系靠 `parent_signature`

```python
if suite.get("value") is not None:                    # 只有 suite 自带汇总值才建 summary
    summary_properties = {"suite": suite["name"]}
    summary_properties.update(reference_data)
    summary_signature_hash = _get_signature_hash(summary_properties)
...
subtest_properties.update({"parent_signature": summary_signature_hash})
```

`has_subtests` 是 signature 上的一个布尔；`TestMetadata` 一侧则完全没有这个字段——父子关系**由路径深度推导**：

```python
@ndb.ComputedProperty
def parent_test(self):
    parts = self.key.id().split('/')
    if len(parts) < 4:
        return None                       # test suite
    return ndb.Key('TestMetadata', '/'.join(parts[:-1]))
```

同理 `bot` 只在路径**恰好 3 段**时非空（自检 E13/E14）。也就是说"这是不是一个顶层 suite"这件事，在这套模型里是路径长度的函数，不需要额外字段，但**路径一旦多切一段或少切一段，语义就变了**。

### 3. 分片与去重：两个系统的去重键完全不同

Perfherder 的 `PerformanceDatum`：

```python
unique_together = ("repository", "job", "push", "push_timestamp", "signature")
models.Index(fields=["repository", "signature", "push_timestamp"])
```

**job 与 push 都在键里**，所以"同一个 push 上重跑同一个 job"产生的是**新点**（时间戳不同）而不是覆盖。

Catapult 的 `Row` 主键是 `(test path, revision)`，`revision = key.integer_id()`——**同一 revision 重复上报是覆盖**（自检 E15c）。代价是 X 轴必须是**单调递增的整数**，源码注释写得很直白："This is usually a Chromium SVN version number, but it might also be any other integer, as long as newer points have higher numbers."

### 4. 写放大：LastAddedRevision 为什么被单独拆出来

```python
class LastAddedRevision(ndb.Model):
    """Represents the last added revision for a test path.
    The reason this property is separated from TestMetadata entity is to avoid
    contention issues (Frequent update of entity within the same group)."""
```

每加一个点就要更新"最后修订号"，如果它挂在 `TestMetadata` 上，同一系列的所有写会挤在同一个实体组里。拆成独立实体后，热点被隔离（自检 E17 建模了它"只增不减"的行为）。Perfherder 对侧的做法是 `last_updated` 挂在 signature 上、并在 `_create_or_update_signature` 里显式保证**只增不减**（自检 E9）。

### 5. 补充维度：两个自由字段 vs 前缀约定

- Perfherder：`tags`(360) 存" "` 拼接的**已排序**标签串，`extra_options`(422) 存 extraOptions，另有 `measurement_unit`(50)、`application`(10) 等小字段。命名维度是**固定槽位**，好处是可索引、可枚举，坏处是加维度要改 schema。
- Catapult：`Row` 是 `ndb.Expando`，任意属性都可写，靠前缀区分语义——`d_` 数据点（如 `d_50th_percentile`）、`r_` 其它仓库的修订号、`a_` 标注（如 `a_chrome_bugid`）。**无前缀的列名不合规**（自检 E18）。另外 `value` 索引而 `error`（标准差）**不索引**（自检 E19），因为查询模式是按 revision 取点，不按标准差过滤。

### 6. 告警侧的几个默认值

`lower_is_better` 默认 **True**（`suite.get("lowerIsBetter", True)`）——CPU 时间、延迟这类"越小越好"是默认假设，吞吐类指标必须显式关掉。`should_alert` / `monitor` 是**三态**（True / False / None，None 表示"没设过"），严重度排序 `SEVERITY_RANK = {None: 0, normal: 1, subcritical: 2, critical: 3}`——**None 排最低**，专门为了让"早于该字段存在"的历史告警不抢占。

## 环境依赖

- Python ≥ 3.9（仅标准库）；Go ≥ 1.21（`go run .`）

## 运行方式

```bash
cd 11-性能分析/03-基准测试方法论/趋势存储与分片
python python/main.py              # 冒烟：两套模型各自的主键推导
python python/selfcheck_store.py   # 54 条断言，全绿
cd go && go run .                  # Go 版同模型
```

## 关键代码

| 文件 | 职责 |
| --- | --- |
| `python/main.py` | `signature_hash` / `_order_and_concat` / `suite_ingest` / `PerfherderStore`（两条唯一约束 + last_updated 单调）/ `TestMetadata`（bot、parent_test ComputedProperty）/ `Row` 与 `CatapultStore` |
| `python/selfcheck_store.py` | 54 条断言：键值互换碰撞、列表不排序的后果、summary/parent 条件、唯一约束、路径前缀推导、revision 覆盖、补充列前缀、索引策略 |
| `go/store.go` | 同模型的 Go 版（238 行） |

## 性能边界与注意事项

- **`signature_hash` 只适合做主键**：键值互换会碰撞，不能当内容校验用。
- **增长维度必须先排序再进 hash**，否则同一组选项会裂成两个系列，且表现为"数据从某天起断线"。
- **`Row` 的 X 轴必须是单调整数**，非单调的修订号（如 git SHA）不能直接当 `revision`。
- **同一 revision 重复上报是覆盖而不是报错**——重跑补数据时要知道你会在覆盖旧点。
- **改名 = 换系列**：Perfherder 侧 hash 变、Catapult 侧要走 `migrate_test_names.py`（源码注释明确要求把废弃属性加进 `TEST_EXCLUDE_PROPERTIES`）。
- **`lower_is_better` 默认 True**，吞吐/带宽类指标务必显式设为 False，否则告警方向是反的。

## 参考资料（实际阅读过的来源）

- [`mozilla/treeherder` — `treeherder/etl/perf.py`](https://github.com/mozilla/treeherder/blob/master/treeherder/etl/perf.py) — `_get_signature_hash` 的键桶排序与 `json.dumps(sort_keys=True)`、`_order_and_concat`、summary 仅在 `value is not None` 时建立、`parent_signature` 注入、`extraOptions` 双重写法、`_create_or_update_signature` 的 `last_updated` 单调性
- [`mozilla/treeherder` — `treeherder/perf/models.py`](https://github.com/mozilla/treeherder/blob/master/treeherder/perf/models.py) — `SIGNATURE_HASH_LENGTH=40`、`PerformanceSignature` 的字段长度与两条 `unique_together`、`PerformanceDatum` 的 `unique_together` 与复合索引、`SEVERITY_RANK`、`ALERT_CHANGE_TYPES`、`has_subtests` / `parent_signature`
- [`catapult-project/catapult` — `dashboard/dashboard/models/graph_data.py`](https://github.com/catapult-project/catapult/blob/master/dashboard/dashboard/models/graph_data.py) — `TestMetadata` 以完整路径为 key、`bot` 与 `parent_test` 两个 ComputedProperty 的段数判据、`Row` 的 Expando 与 `revision = key.integer_id()`、`value` 索引而 `error` 不索引、`d_`/`r_`/`a_` 补充列前缀约定、`LastAddedRevision` 拆实体的 contension 说明、"> 3 trillion Rows" 的规模注记
