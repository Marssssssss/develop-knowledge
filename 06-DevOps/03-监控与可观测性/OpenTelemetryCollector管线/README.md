# OpenTelemetry Collector 管线

纯手写实现 OpenTelemetry Collector 的**配置装配语义**与**数据通路语义**:组件 ID 的
`type[/name]` 复合键、六段组件表与 pipeline 引用校验、receiver/exporter 两侧 fanout、
connector 依赖图与环检测、`memory_limiter` 的软/硬限三态、`batch` 的阈值触发与切分、
exporter helper 的退避重试与发送队列背压。

不引入第三方依赖也不联网。Python 版**直接实跑断言**(108 条全绿);Go/C 版因本机无
对应工具链,只做实跑以外的机械核查(见「运行方式」)。

## 原理详解

### 1. 配置 = 六段组件表 + 一个 service 段

```yaml
receivers:  {otlp: {}, prometheus: {}}
processors: {memory_limiter: {}, batch: {}}
exporters:  {otlp: {}, debug: {}}
connectors: {spanmetrics: {}}
extensions: {file_storage: {}}
service:
  extensions: [file_storage]
  pipelines:
    traces:  {receivers: [otlp], processors: [memory_limiter, batch], exporters: [spanmetrics, debug]}
    metrics: {receivers: [spanmetrics], processors: [memory_limiter], exporters: [otlp]}
```

- **组件 ID 是 `type` 或 `type/name`**,两段共用同一条正则 `^[a-zA-Z][0-9a-zA-Z_]*$`。
  `type` 决定实现,`name` 只区分**同类实例** —— `batch` 与 `batch/logs` 是两份独立实例。
- **命名空间按段划分**:`receivers.otlp` 与 `exporters.otlp` 是两回事,同名合法。
- **pipeline key 也是复合键**(`traces`、`traces/primary`):同一信号可有多条 pipeline。
- **`connectors` 是唯一能出现在 pipeline 两端的组件**:一条 pipeline 的输出经它变成
  另一条的输入,这就是跨信号聚合(traces → spanmetrics → metrics)的实现方式。

### 2. 装配:两侧 fanout + connector 依赖图

```
入口侧(receiver fanout)          出口侧(exporter fanout)
   otlp ──┬── traces                traces ──┬── spanmetrics ──→ metrics
          ├── traces/primary                └── debug
          └── logs
```

一个 receiver 被 N 条 pipeline 引用,同一份入站数据扇出 N 路;一条 pipeline 有 M 个
exporter,出站数据广播 M 份。扇出是**同步调用**:串行耗时是各分支之和,并行耗时等于
**最慢的一路**(尾部延迟由此决定)。

connector 让 pipeline 之间产生依赖,需要拓扑排序;**成环则启动失败** —— 真实 Collector
对这种配置直接报错退出,而不是运行时死锁。

### 3. `memory_limiter` 的三态与「顺序即语义」

processor 按 pipeline 中书写的顺序执行,顺序就是语义。`memory_limiter` 必须放**首位**,
否则它前面的组件已经为整批数据分配过内存,限额拦不住这轮分配。

```
limit_mib = 4000, spike_limit_mib = 800(默认 = limit 的 20%)
soft = 4000 − 800 = 3200
  rss ≤ 3200         → ok       放行
  3200 < rss ≤ 4000  → refused  拒绝入站数据并向上游回压
  rss > 4000         → gc       强制 GC(该批仍被丢弃)
```

检查是**周期性**的(`check_interval`),两次检查之间内存本来就可能冲过软限 ——
`spike_limit_mib` 那 20% 就是吸收这段时间的余量。

### 4. `batch`:两个触发条件 + 一个切分上限

- `send_batch_size`(默认 **8192**):攒够就发。
- `timeout`(默认 **200ms**):到点就发,不等攒满。
- `send_batch_max_size`(默认 **0** = 不切分):非 0 时每批不超过它,**且必须 ≥
  `send_batch_size`**。

切分只在**一次投递就是一大坨**时才起作用:真实 Collector 的输入单元是一个 request
(可能含上万 spans),此时 flush 会按 max_size 切成多批,尾批可以小于 `send_batch_size`;
若上游逐条到达,缓冲永远刚好到阈值就发出,切分根本不会发生。本 demo 的 `push()` 与
`extend()` 正是分别模拟这两种到达方式。

### 5. exporter helper:队列与退避

```
发送链:timeoutSender → retrySender → queueSender → exporterBatcher → 真实 exporter
        (单次超时)     (指数退避)     (内存队列/背压)  (出站批)
```

- 重试默认 `initial_interval=5s`、`max_interval=30s`、`multiplier=1.5`、
  `max_elapsed_time=300s`,间隔 `5 → 7.5 → 11.25 → 16.875 → 25.3125 → 30 → 30 …`。
  300s 预算只够重试 **12 次**(累计 275.9375s,第 13 次要 305.9375s)。
- 队列默认 `num_consumers=10`、`queue_size=5000`(sizer=requests)、
  `block_on_overflow=false`。**队列满 → 直接丢弃**,且发生在进入重试逻辑之前,
  因此不计入重试,由 `otelcol_exporter_enqueue_failed_*` 指标暴露。
- 容量估算(官方):`queue_size = 想缓冲的秒数 × RPS ÷ 每批请求数`。

## 对比

| 维度 | agent 模式 | gateway 模式 |
| --- | --- | --- |
| 形态 | 每节点一个(DaemonSet) | 独立 Deployment |
| 职责 | 采集、富化、批量 | 采样、聚合、路由多后端 |
| 相关参数 | 队列小、timeout 短 | 队列大、batch 大、上持久化 |

| 缓冲方案 | 进程崩溃 | 后端长时不可用 |
| --- | --- | --- |
| 纯内存队列 | **已入队批次全丢** | 队列满即丢弃 |
| file_storage 持久化队列 | 重启后继续投递 | 队列满仍丢弃 |
| Kafka 等消息队列 | 不受影响 | 积压在 topic |

## 环境准备

Python 3.8+(本 demo 用 3.13 实跑,仅标准库);Go 1.21+ / C11 编译器为可选项。无需网络与任何第三方包。

## 运行方式

```bash
cd python && python demo.py                  # ALL PASS  108 assertions(已实跑)
cd go && go run .                            # 预期 100 条,本机无 go 工具链未实跑
cd c && gcc -std=c11 -Wall -Wextra -lm -o otel_demo otel_demo.c && ./otel_demo
                                             # 预期 63 条,本机无 C 工具链未实跑
```

Go/C 走机械核查替代编译器(工具在本仓库 `_docs/tools/`):

```bash
python _docs/tools/go_sanity.py go/*.go                       # 未用 import / 参个数 / 重名
python _docs/tools/c_sanity.py --tu c/otel_demo.c c/*.h       # 合并 TU 查 static 函数参个数
python _docs/tools/bracket_check.py go/*.go c/* python/*.py   # 括号平衡
```

C 版是**实现头**结构:`otel_demo.c` 文本级包含 `otel_core.h` 与 `otel_pipeline.h`,
三者同处一个翻译单元 —— `static` 可见性与构建命令都不变(只编译 `.c`)。

## 关键代码片段

「顺序即语义」的量化——limiter 在首位时拒绝是零成本的:

```python
def wasted_work(processors_after_limiter, n_records):
    """limiter 拒绝整批时,它前面那些组件已经白做的工作量。"""
    cost = 0
    for p in processors_after_limiter:
        if isinstance(p, FilterProcessor):
            cost += n_records * 2 * len(p.conditions)
        elif isinstance(p, AttributesProcessor):
            cost += n_records * len(p.actions)
    return cost
```

批量到达才触发切分:

```python
def push(self, records, now_ms):
    """一次投递 N 条后检查阈值。真实 Collector 的输入单元是一个 request,
    所以 send_batch_max_size 的切分只在 push 语义下才发生。"""
    self.buf.extend(records)
    if len(records) > 0 and self.first_add_ms is None:
        self.first_add_ms = now_ms
    if len(self.buf) >= self.send_batch_size:
        return self.flush(now_ms, "size")
    return []
```

## 性能与边界

- `check_interval` 越长,越可能在两次检查之间冲过硬限;尖峰流量应调小它或调大
  `spike_limit_mib`。官方默认写作 **0s**,但同一文档建议「最优值是 1s」——生产要显式设置。
- `batch` 的 timeout 直接决定尾延迟:timeout=5s 时第 0 秒产生的 span 可能到第 5 秒才
  出站。trace 通常可接受,喂告警的 metrics/logs 要压到 1–2s。
- 重试预算耗尽后旧数据被丢弃;`max_elapsed_time=0` 的语义是**永不停止**。

## 注意事项与常见坑

1. **`queue_size` 默认值有文档分歧**:上游 `exporterhelper` README 写 `5000`,官网
   resiliency 页写「often 1000」;新版引入 `sizer`(`requests`/`items`/`bytes`)还改变了
   计量单位 —— 跨版本比参数前先确认版本。
2. **`max_elapsed_time` 同样有版本差**:上游 README 现为 `300s`,较老的 fork 快照是
   `120s`。同样预算下前者重试 12 次、后者 6 次。
3. **`send_batch_max_size` 小于 `send_batch_size` 是非法配置**,只能取 `>= size` 或 `= 0`。
4. **`traces/2` 这类 pipeline 名会被拒**:`name` 与 `type` 共用一条正则,必须以字母开头。
5. **队列满默认静默丢弃**,不体现在重试指标里 —— 只看重试指标会误判为「没丢数据」,
   要盯 `otelcol_exporter_enqueue_failed_*`。
6. **`memory_limiter` 放错位置等于没配**:放末位时前面的组件已为整批数据跑过一遍。
7. **filter 的 `!=` 在属性缺失时会命中**,与直觉相反 —— 误用会静默删数据。
8. **跨语言正则语义不同**:Go `regexp.MatchString` 是**搜索**语义,Python `re.fullmatch`
   是整串匹配;Go 版显式加 `^(?:…)$` 锚点对齐,否则 `/readyz` 会被 `/ready` 命中。
   C 版无正则,该分支退化为前缀匹配(仅演示差异,未对齐语义)。
9. **边界取等号必须写死并跨语言一致**:软限取等号(`rss == soft`)判为放行,这个选择在
   三语言实现里若不一致,跨语言断言就会打架。

## 参考资料

- OpenTelemetry 官方 [Collector architecture](https://opentelemetry.io/docs/collector/architecture/) —— 组件与 pipeline 模型
- OpenTelemetry 官方 [Collector configuration](https://opentelemetry.io/docs/collector/configuration/) —— 六段结构、`type/name` 复合键
- OpenTelemetry 官方 [Resiliency](https://opentelemetry.io/pt/docs/collector/resiliency/) —— 发送队列、持久化 WAL、Kafka 方案与丢失场景
- Red Hat [build of OpenTelemetry](https://docs.redhat.com/en/documentation/openshift_container_platform/4.13/pdf/red_hat_build_of_opentelemetry/openshift_container_platform-4.13_red_hat_build_of_opentelemetry-en-us.pdf) —— batch 与 memory_limiter 的默认值表(200ms / 8192 / 0,spike = limit 的 20%)
- 上游 [exporterhelper README](https://github.com/humivo/opentelemetry-collector/blob/main/exporter/exporterhelper/README.md) —— retry/queue 默认值、持久化队列、`otelcol_exporter_enqueue_failed_*`
- OpenSearch [Batching & Performance](https://observability.opensearch.org/docs/send-data/data-pipeline/batching) —— 各阶段 batching 默认值与生产建议
- DeepWiki [grafana/opentelemetry-collector: Exporters](https://deepwiki.com/grafana/opentelemetry-collector/6-exporters) —— helper 责任链(timeout/retry/queue/batcher)

> 官方文档未给出显式默认值的项(如 `wasted_work` 的各部分权重、spike 取整方式)在代码
> 注释里均标注为**建模假设**,不代表 Collector 的真实实现。
