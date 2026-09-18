"""自检:OTel Collector 配置装配 / processor 链 / exporter helper 的语义断言。

直接 `python demo.py` 实跑。不依赖第三方包与网络。
数值预期均按 README 里列出的官方默认值推导;浮点一律给容差。
"""

import sys

from otel_config import (
    Config, ConfigError, component_type, is_same_type, memory_limiter_first,
    parse_component_id,
)
from otel_exporter import (
    PersistentQueue, RetryPolicy, SendingQueue, memory_queue_crash_loss,
    suggested_queue_size,
)
from otel_pipeline import (
    AttributesProcessor, BatchProcessor, FilterProcessor, MemoryLimiter,
    PipelineRunner, Record, eval_condition, fanout_export, fanout_latency_ms,
    wasted_work,
)

PASS = 0
FAIL = []


def check(label, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append("%s  %s" % (label, detail))


def near(a, b, tol=1e-9):
    return abs(a - b) < tol


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return False
    except ConfigError:
        return True


def raises_value(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return False
    except ValueError:
        return True


def recs(n, **attrs):
    return [Record(dict(attrs, i=i)) for i in range(n)]


RAW = {
    "receivers": {"otlp": {}, "prometheus": {}},
    "processors": {
        "memory_limiter": {}, "batch": {}, "batch/logs": {},
        "attributes": {}, "filter/drop_health": {}, "resourcedetection": {},
    },
    "exporters": {"otlp": {}, "debug": {}, "otlphttp": {}},
    "connectors": {"spanmetrics": {}},
    "extensions": {"file_storage": {}, "health_check": {}},
    "service": {
        "extensions": ["file_storage", "health_check"],
        "pipelines": {
            "traces": {"receivers": ["otlp"],
                       "processors": ["memory_limiter", "batch", "attributes"],
                       "exporters": ["spanmetrics", "debug"]},
            "traces/primary": {"receivers": ["otlp"],
                               "processors": ["memory_limiter", "batch"],
                               "exporters": ["otlp"]},
            "metrics": {"receivers": ["spanmetrics"],
                        "processors": ["memory_limiter", "batch"],
                        "exporters": ["otlphttp"]},
            "logs": {"receivers": ["otlp"],
                     "processors": ["batch/logs", "filter/drop_health", "memory_limiter"],
                     "exporters": ["otlp"]},
        },
    },
}

# ---------------------------------------------------------------- A 组件 ID
check("A1 type/name 拆分", parse_component_id("batch/traces") == ("batch", "traces"))
check("A2 无 name 的形式", parse_component_id("otlp") == ("otlp", None))
check("A3 同 type 不同 name 是两个实例", is_same_type("batch", "batch/logs"))
check("A4 不同 type 不同组件", not is_same_type("batch", "otlp"))
check("A5 提取 type", component_type("filter/drop_health") == "filter")
check("A7 name 也须以字母开头(坑)", raises(parse_component_id, "traces/2"))
for bad in ("batch/", "/traces", "batch/traces/x", "1batch", "", "batch//x", "a b",
            "traces/2", "batch/2x"):
    check("A6 非法 ID %r" % bad, raises(parse_component_id, bad))

# ---------------------------------------------------------------- B 配置校验
cfg = Config.from_dict(RAW)
warns = cfg.validate()
check("B1 pipeline 数量", len(cfg.pipelines) == 4, str(sorted(cfg.pipelines)))
check("B2 未使用组件告警", warns == ["unused receiver: prometheus",
                                     "unused processor: resourcedetection"], str(warns))
check("B3 receiver/metrics 名同实不同", cfg.defined("receivers") & cfg.defined("exporters") == {"otlp"})
check("B4 pipeline key 形态", cfg.pipelines["traces/primary"].name == "primary")
check("B5 空 pipelines 非法",
      raises(Config.from_dict({"service": {"pipelines": {}}}).validate))
check("B6 未定义 receiver 非法",
      raises(Config.from_dict({"service": {"pipelines": {
          "traces": {"receivers": ["nope"], "exporters": []}}}}).validate))
check("B7 未定义 processor 非法",
      raises(Config.from_dict({"processors": {"batch": {}}, "service": {"pipelines": {
          "traces": {"processors": ["memory_limiter"], "exporters": []}}}}).validate))
check("B8 未定义 extension 非法",
      raises(Config.from_dict({"service": {"extensions": ["fs"], "pipelines": {
          "traces": {"receivers": [], "exporters": []}}}}).validate))
check("B9 未知信号类型非法",
      raises(Config.from_dict, {"service": {"pipelines": {"traces/x/y": {}}}}))

# ---------------------------------------------------------------- C fanout
rf = cfg.receiver_fanout()
check("C1 receiver 扇出多路", rf["otlp"] == ["traces", "traces/primary", "logs"], str(rf))
check("C2 connector 作 receiver", rf["spanmetrics"] == ["metrics"])
check("C3 exporter 侧广播", cfg.exporter_fanout()["traces"] == ["spanmetrics", "debug"])
check("C4 串行扇出求和", near(fanout_latency_ms([3, 5, 1], False), 9.0))
check("C5 并行扇出取最慢", near(fanout_latency_ms([3, 5, 1], True), 5.0))
check("C6 空分支", near(fanout_latency_ms([], True), 0.0))

# ---------------------------------------------------------------- D connector 图
check("D1 connector 边", cfg.connector_edges() == [("traces", "metrics")],
      str(cfg.connector_edges()))
check("D2 拓扑序", cfg.topo_order() == ["logs", "traces", "traces/primary", "metrics"],
      str(cfg.topo_order()))
CYCLE = dict(RAW)
CYCLE["connectors"] = {"spanmetrics": {}, "forward": {}}
CYCLE["service"] = dict(RAW["service"])
CYCLE["service"]["pipelines"] = dict(RAW["service"]["pipelines"])
CYCLE["service"]["pipelines"]["metrics"] = {
    "receivers": ["spanmetrics"], "processors": ["memory_limiter"],
    "exporters": ["otlphttp", "forward"]}
CYCLE["service"]["pipelines"]["traces"] = {
    "receivers": ["otlp", "forward"], "processors": ["memory_limiter"],
    "exporters": ["spanmetrics", "debug"]}
cyc = Config.from_dict(CYCLE)
check("D3 环检测报错", raises(cyc.topo_order))
check("D4 单向图无环", Config.from_dict(RAW).topo_order())

# ---------------------------------------------------------------- E limiter
lm = MemoryLimiter(limit_mib=4000)
check("E1 spike 默认 20%", lm.spike_limit_mib == 800)
check("E2 软限 = 硬限 - 尖峰", lm.soft_limit_mib == 3200)
check("E3 低于软限放行", lm.check(3000) == ("ok", 200), str(lm.check(3000)))
check("E4 恰好等于软限仍放行", lm.check(3200) == ("ok", 0))
check("E5 刚过软限拒绝", lm.check(3201) == ("refused", -1))
check("E6 等于硬限仍拒绝", lm.check(4000) == ("refused", -800))
check("E7 超过硬限触发 GC", lm.check(4001) == ("gc", -801))
check("E8 limit=0 视为不设限", MemoryLimiter(limit_mib=0).check(99999) == ("ok", float("inf")))
check("E9 显式 spike", MemoryLimiter(limit_mib=4096, spike_limit_mib=1024).soft_limit_mib == 3072)
check("E10 默认 check_interval=0s", lm.check_interval_s == 0.0)

runner = PipelineRunner("traces", [MemoryLimiter(limit_mib=4000), BatchProcessor()])
check("E11 首位判定为真", runner.limiter_is_first)
r2 = PipelineRunner("logs", [BatchProcessor(), FilterProcessor(["x == \"1\""]),
                            MemoryLimiter(limit_mib=4000)])
check("E12 末位判定为假", not r2.limiter_is_first)
check("E13 硬限拒绝整批", runner.run(recs(10), rss_mib=5000) == [] and runner.refused == 10)
check("E14 GC 计数", runner.gc_forced == 1)
ok_recs = runner.run(recs(3), rss_mib=1000)
check("E15 正常放行", len(ok_recs) == 3 and runner.refused == 10)
check("E16 末位白做工作量", wasted_work(r2.processors[:2], 100) == 200,
      str(wasted_work(r2.processors[:2], 100)))

# ---------------------------------------------------------------- F batch
b0 = BatchProcessor()
check("F1 默认值 8192/200ms/0", (b0.send_batch_size, b0.timeout_ms, b0.send_batch_max_size) == (8192, 200, 0))
check("F2 未达阈值不发送", BatchProcessor().extend(recs(8191), 0) == [])
b1 = BatchProcessor()
out1 = b1.extend(recs(8192), 0)
check("F3 达到阈值立即发送", [len(x) for x in out1] == [8192] and b1.emitted == [("size", [8192])])
b2 = BatchProcessor(send_batch_size=8192, send_batch_max_size=0)
out2 = b2.extend(recs(10000), 0)
check("F4 max=0 不切分", [len(x) for x in out2] == [8192] and len(b2.buf) == 1808)
b3 = BatchProcessor(send_batch_size=8192, send_batch_max_size=10000)
out3 = b3.push(recs(25000), 0)
check("F5 push 时按 max 切分且尾批可小", [len(x) for x in out3] == [10000, 10000, 5000]
      and len(b3.buf) == 0, str([len(x) for x in out3]))
b4 = BatchProcessor(send_batch_size=1000, send_batch_max_size=2000)
out4 = b4.push(recs(5000), 0)
check("F6 小规模切分", [len(x) for x in out4] == [2000, 2000, 1000])
b4b = BatchProcessor(send_batch_size=8192, send_batch_max_size=0)
out4b = b4b.push(recs(25000), 0)
check("F7 max=0 时单批全发", [len(x) for x in out4b] == [25000])
b5 = BatchProcessor(send_batch_size=8192, timeout_ms=200)
b5.extend(recs(5), 1000)
check("F8 未到 timeout 不发送", b5.tick(1199) == [])
tick_out = b5.tick(1200)
check("F9 timeout 兜底发送", [len(x) for x in tick_out] == [5] and b5.emitted == [("timeout", [5])])
check("F10 flush 后计时器复位", b5.first_add_ms is None and b5.tick(9999) == [])
check("F11 max < size 非法", raises(BatchProcessor, 8192, 200, 4096))
check("F12 max == size 合法", BatchProcessor(8192, 200, 8192).send_batch_max_size == 8192)
b6 = BatchProcessor()
check("F13 空 push 不启动计时", b6.push([], 500) == [] and b6.first_add_ms is None)

# ---------------------------------------------------------------- G 变换与过滤
ap = AttributesProcessor([
    {"action": "insert", "key": "env", "value": "prod"},
    {"action": "update", "key": "svc", "value": "api"},
    {"action": "upsert", "key": "env", "value": "stage"},
    {"action": "extract", "key": "route", "from_attribute": "http.route"},
    {"action": "hash", "key": "user.email"},
    {"action": "delete", "key": "secret"},
])
r = ap.apply(Record({"env": "dev", "http.route": "/health", "user.email": "a@b.c",
                     "secret": "x", "duration_ms": 3}))
check("G1 insert 不覆盖已有值", AttributesProcessor(
    [{"action": "insert", "key": "k", "value": 2}]).apply(Record({"k": 1})).attrs["k"] == 1)
check("G1b upsert 覆盖 insert 写过的值", r.attrs["env"] == "stage")
check("G2 update 缺 key 不新增", "svc" not in r.attrs)
check("G3 extract 拷贝", r.attrs["route"] == "/health")
check("G4 hash 为 sha1 长度", len(r.attrs["user.email"]) == 40)
check("G5 delete 移除", "secret" not in r.attrs)
check("G6 upsert 覆盖", AttributesProcessor(
    [{"action": "upsert", "key": "k", "value": 2}]).apply(Record({"k": 1})).attrs["k"] == 2)
check("G7 update 覆盖存在值", AttributesProcessor(
    [{"action": "update", "key": "k", "value": 2}]).apply(Record({"k": 1})).attrs["k"] == 2)
check("G8 未知动作非法", raises(AttributesProcessor, [{"action": "boom", "key": "k"}]))
check("G9 hash 可复现", AttributesProcessor.hash_value("a@b.c") ==
      AttributesProcessor.hash_value("a@b.c"))
f = FilterProcessor(["http.route == \"/health\"", "http.route =~ \"/(ready|live)\""])
check("G10 等值命中", f.matches(Record({"http.route": "/health"})))
check("G11 正则整串匹配", f.matches(Record({"http.route": "/ready"})))
check("G12 前缀不算命中", not f.matches(Record({"http.route": "/readyz"})))
check("G13 数值比较", FilterProcessor(["duration_ms < 5"]).matches(Record({"duration_ms": 3})))
check("G14 缺失属性不等于命中", not FilterProcessor(["duration_ms < 5"]).matches(Record({})))
check("G15 缺失属性使 != 命中(坑)", FilterProcessor(["env != \"prod\""]).matches(Record({})))
check("G16 不匹配则放行", not f.matches(Record({"http.route": "/api"})))
check("G17 非法条件抛错", raises(eval_condition, "duration_ms ~~ 3", Record({})))
check("G18 error_mode 校验", raises(FilterProcessor, [], "silent"))
check("G19 ignore 模式吞掉坏条件",
      FilterProcessor(["bad ~~ 1"], "ignore").matches(Record({})) is False)
runner3 = PipelineRunner("logs", [FilterProcessor(["http.route == \"/health\""])])
kept = runner3.run([Record({"http.route": "/health"}), Record({"http.route": "/api"})])
check("G20 过滤计数", len(kept) == 1 and runner3.filtered == 1)

exps = {"otlp": lambda b: True, "debug": lambda b: False}
fo = fanout_export([["x"], ["y"]], exps)
check("G21 广播到每个 exporter", fo == {"otlp": [True, True], "debug": [False, False]}, str(fo))

# ---------------------------------------------------------------- H exporter
rp = RetryPolicy()
check("H1 默认退避参数", (rp.initial_interval, rp.max_interval, rp.max_elapsed_time, rp.multiplier)
      == (5.0, 30.0, 300.0, 1.5))
check("H2 退避序列封顶", rp.schedule(7) == [5, 7.5, 11.25, 16.875, 25.3125, 30, 30],
      str(rp.schedule(7)))
check("H3 首次等待 5s", near(rp.backoff(1), 5.0))
check("H4 预算内重试 12 次", rp.attempts_within_budget() == 12, str(rp.attempts_within_budget()))
check("H5 累计等待", near(rp.total_wait(12), 275.9375))
check("H6 旧口径 120s -> 6 次", RetryPolicy(max_elapsed_time=120).attempts_within_budget() == 6)
check("H7 0 表示永不停止", RetryPolicy(max_elapsed_time=0).attempts_within_budget() == -1)
check("H8 关闭重试", RetryPolicy(enabled=False).attempts_within_budget() == 0)
check("H9 关闭后间隔无穷", RetryPolicy(enabled=False).backoff(1) == float("inf"))
check("H10 窗口覆盖 60s 故障", rp.window_covers(60.0))
check("H11 短预算覆盖不了", not RetryPolicy(max_elapsed_time=12).window_covers(60.0))
q = SendingQueue()
check("H12 默认队列参数", (q.queue_size, q.num_consumers, q.block_on_overflow) == (5000, 10, False))
check("H13 入队 5000 全成功", all(q.enqueue(["b%d" % i]) is True for i in range(5000)))
check("H14 第 5001 个被丢弃", q.enqueue(["overflow"]) is False and q.enqueue_failed == 1)
check("H15 容量归零", q.capacity_left == 0 and q.enqueued == 5000)
qb = SendingQueue(queue_size=2, block_on_overflow=True)
qb.enqueue(["a"]); qb.enqueue(["b"])
check("H16 overflow 阻塞而非丢弃", qb.enqueue(["c"]) == "blocked" and qb.enqueue_failed == 0)
check("H17 并发消费时长", near(SendingQueue(num_consumers=10).drain(1.0), 0.0))
qd = SendingQueue(num_consumers=10)
qd.enqueue_many([["b%d" % i] for i in range(20)])
check("H18 20 批 10 消费者 = 2 轮", near(qd.drain(1.0), 2.0))
check("H19 建议队列长度", suggested_queue_size(60, 100, 1) == 6000)
check("H20 per_batch 非法", raises_value(suggested_queue_size, 60, 100, 0))
pq = PersistentQueue(queue_size=10)
pq.enqueue(["b1"]); pq.enqueue(["b2"])
check("H21 持久化队列恢复", pq.crash_and_restart() == 2 and len(pq) == 2)
check("H22 纯内存队列崩溃全丢", memory_queue_crash_loss(["b1", "b2"]) == 2)
check("H23 恢复后清空盘上副本", len(pq.on_disk) == 0)

# ---------------------------------------------------------------- 收尾
if FAIL:
    print("FAILED %d / %d" % (len(FAIL), len(FAIL) + PASS))
    for line in FAIL:
        print("  - " + line)
    sys.exit(1)
print("ALL PASS  %d assertions" % PASS)
