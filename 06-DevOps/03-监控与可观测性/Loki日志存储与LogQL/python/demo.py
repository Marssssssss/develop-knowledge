#!/usr/bin/env python3
"""Loki 日志存储与 LogQL —— 可运行断言集（查询侧 A~F）。

跑法：python demo.py    （本机 Python 3.13 实跑通过）
纯标准库，不依赖真实 Loki 集群。存储/摄入侧的 G~J 见 checks_ingest.py。

  A limits_config 默认值与按租户覆盖
  B 分发器限流均摊（含 burst 不均摊的坑）
  C 流选择器匹配语义（缺失标签 = 空串、=~ 完全锚定）
  D 行过滤与流选择器的锚定不对称
  E 解析器、__error__ 传播、指标查询遇错即失败
  F 范围聚合数值（Prometheus 口径的分位数与总体方差）
"""

from __future__ import annotations

import json
import math
import sys

from checkframe import check, close, eq, raises, report, section
from logql_eval import (
    ERROR_LABEL,
    JSON_PARSER_ERR,
    LOGFMT_PARSER_ERR,
    PATTERN_PARSER_ERR,
    SAMPLE_EXTRACTION_ERR,
    Entry,
    run_pipeline,
)
from logql_parse import parse_query
from loki_limits import (
    cluster_effective_rate_mb,
    distributor_burst_mb,
    distributor_rate_mb,
    resolve_limits,
)
from loki_range import evaluate, population_stdvar, prom_quantile, series_key

# ----------------------------------------------------------------- 公共夹具
T0 = 1_700_000_000_000_000_000  # 固定基准时刻，避免断言依赖真实时钟
SEC = 10**9
API = (("app", "api"), ("env", "prod"))
WEB = (("app", "web"), ("env", "prod"))


def E(ts: int, line: str, labels: tuple) -> Entry:
    return Entry(ts, line, dict(labels))


def stages(text: str) -> tuple:
    return parse_query(text).stages


def sel(text: str):
    return parse_query(text).selector


def entry_of(text: str, line: str, base: dict | None = None) -> Entry | None:
    return run_pipeline(
        Entry(T0, line, base if base is not None else {"app": "api-server"}), stages(text)
    )


def labels_of(text: str, line: str, base: dict | None = None) -> dict:
    out = entry_of(text, line, base)
    return {} if out is None else out.labels


def keep(text: str, line: str, labels: dict | None = None) -> bool:
    return entry_of(text, line, labels) is not None


def run(text: str, entries: list, streams: list | None = None) -> dict:
    return evaluate(parse_query(text), entries, now_ns=T0, streams=streams)


def one(result: dict) -> float:
    check("one() 单序列前提", len(result) == 1, f"得到 {len(result)} 条: {result}")
    return next(iter(result.values()))


# ==================================================================== A
section("A")
base = resolve_limits()
eq("A1 ingestion_rate_mb 默认 4MB/s", base["ingestion_rate_mb"], 4.0)
eq("A2 ingestion_burst_size_mb 默认 6MB", base["ingestion_burst_size_mb"], 6.0)
eq("A3 限流策略默认 global", base["ingestion_rate_strategy"], "global")
eq("A4 reject_old_samples 默认开", base["reject_old_samples"], True)
eq("A5 旧样本窗口默认 168h", base["reject_old_samples_max_age"], "168h")
eq("A6 max_entries_limit_per_query 默认 5000", base["max_entries_limit_per_query"], 5000)
eq("A7 max_streams_per_user 默认 0(本机不限)", base["max_streams_per_user"], 0)
eq("A8 max_global_streams_per_user 默认 5000", base["max_global_streams_per_user"], 5000)
eq("A9 每序列标签数默认 30(最佳实践推荐 15)", base["max_label_names_per_series"], 30)
eq("A10 活跃流窗口 = chunk_idle_period 30m", base["chunk_idle_period"], "30m")
ov = resolve_limits(overrides={"team-a": {"ingestion_rate_mb": 16.0}}, tenant="team-a")
eq("A11 租户覆盖生效", ov["ingestion_rate_mb"], 16.0)
eq("A12 覆盖不污染全局默认", resolve_limits()["ingestion_rate_mb"], 4.0)
eq("A13 未覆盖租户继承全局", resolve_limits(overrides={"team-a": {}}, tenant="team-b")["ingestion_rate_mb"], 4.0)
eq("A14 全局项可整表替换", resolve_limits({"ingestion_rate_mb": 10.0})["ingestion_rate_mb"], 10.0)

# ==================================================================== B
section("B")
eq("B1 global 策略按实例均摊 4/10", distributor_rate_mb(4.0, 10), 0.4)
eq("B2 扩容后每实例额度再降 4/20", distributor_rate_mb(4.0, 20), 0.2)
eq("B3 local 策略不做均摊", distributor_rate_mb(4.0, 10, "local"), 4.0)
eq("B4 global 集群总额 = 配置值", cluster_effective_rate_mb(4.0, 10), 4.0)
eq("B5 local 集群总额 = 配置值 x N(坑)", cluster_effective_rate_mb(4.0, 10, "local"), 40.0)
eq("B6 burst 不随实例数均摊(官方原文)", distributor_burst_mb(6.0, 10), 6.0)
eq("B7 实例减少反而抬高每实例阈值", distributor_rate_mb(4.0, 5) > distributor_rate_mb(4.0, 10), True)
raises("B8 实例数为 0 直接报错", lambda: distributor_rate_mb(4.0, 0), ValueError)

# ==================================================================== C
section("C")
prod = {"app": "api-server", "env": "prod", "cluster": "c1"}
noenv = {"app": "api-server"}
eq("C1 精确匹配不做前缀", sel('{app="api"}').matches(prod), False)
eq("C2 精确匹配命中", sel('{app="api-server"}').matches(prod), True)
eq("C3 流选择器的正则完全锚定(坑)", sel('{app=~"api"}').matches(prod), False)
eq("C4 需要 .* 才能命中", sel('{app=~"api.*"}').matches(prod), True)
eq("C5 缺失标签 == 空串故 env!=prod 命中", sel('{env!="prod"}').matches(noenv), True)
eq("C6 缺失标签也能被 .* 匹配", sel('{env=~".*"}').matches(noenv), True)
eq("C7 env!~\".+\" 同样命中缺失标签(反直觉)", sel('{env!~".+"}').matches(noenv), True)
eq("C8 env!~\".+\" 对已有 env 不命中", sel('{env!~".+"}').matches(prod), False)
eq("C9 多 matcher 之间是 AND", sel('{app="api-server", env="prod"}').matches(prod), True)
eq("C9b 一个不满足即整体不匹配", sel('{app="api-server", env="dev"}').matches(prod), False)
raises("C10 非法正则提前报错", lambda: parse_query('{app=~"("}'))

# ==================================================================== D
section("D")
LINE = 'level=error msg="disk full" path=/api/v1/users'
eq("D1 |= 是子串包含", keep('{a="b"} |= "error"', LINE), True)
eq("D2 行过滤大小写敏感", keep('{a="b"} |= "ERROR"', LINE), False)
eq("D3 (?i) 可显式开启忽略大小写", keep('{a="b"} |~ "(?i)error"', LINE), True)
eq("D4 |~ 是搜索语义(非锚定)", keep('{a="b"} |~ "err"', LINE), True)
eq("D5 ^err 不命中说明确实非锚定", keep('{a="b"} |~ "^err"', LINE), False)
eq("D6 同一个 ~ 在流选择器锚定", sel('{app=~"api-server"}').matches({"app": "api-server-x"}), False)
eq("D6b 对照:行过滤能命中子串", keep('{a="b"} |~ "api"', LINE), True)
eq("D7 != 是不包含", keep('{a="b"} != "debug"', LINE), True)
eq("D8 多个行过滤是 AND", keep('{a="b"} |= "error" != "timeout"', LINE), True)
eq("D9 !~ 取反", keep('{a="b"} !~ "err.*"', LINE), False)

# ==================================================================== E
section("E")
GOOD = '{"level":"error","status":500,"d":12}'
BAD = "not a json line"
g = labels_of('{a="b"} | json', GOOD)
eq("E1 json 提取标量字段", g.get("level"), "error")
eq("E1b 数字字段转成标签", g.get("status"), "500")
eq("E2 合法行没有 __error__", ERROR_LABEL in g, False)
eq("E3 json 过滤可下推", keep('{a="b"} | json | level="error"', GOOD), True)
eq("E4 解析失败不丢行(官方原文)", labels_of('{a="b"} | json', BAD).get(ERROR_LABEL), JSON_PARSER_ERR)
eq("E5 想丢错误行必须显式过滤", keep('{a="b"} | json | __error__=""', BAD), False)
eq("E6 只想看错误行", keep('{a="b"} | json | __error__!=""', BAD), True)
eq("E6b __error__!=\"\" 对正常行不命中", keep('{a="b"} | json | __error__!=""', GOOD), False)
lf = labels_of('{a="b"} | logfmt', 'level=info msg="disk full" code=500')
eq("E7 logfmt 解析带空格引号值", lf.get("msg"), "disk full")
eq("E7b logfmt 其余键也提取", lf.get("code"), "500")
eq("E7c logfmt 失败仅打错误标签", labels_of('{a="b"} | logfmt', "not logfmt").get(ERROR_LABEL), LOGFMT_PARSER_ERR)
pt = labels_of('{a="b"} | pattern "<_> <level> <msg>"', "2026-01-01 info hello world")
eq("E8 pattern <_> 丢弃首列", pt.get("level"), "info")
eq("E8b pattern 末位捕获吃掉剩余部分", pt.get("msg"), "hello world")
eq("E8c pattern 不匹配只打错误标签", labels_of('{a="b"} | pattern "<x>=<_>"', "nomatch").get(ERROR_LABEL), PATTERN_PARSER_ERR)
raises("E9a 双引号里 \\w 是非法转义(Go 口径)", lambda: parse_query('{a="b"} | regexp "(?P<m>\\w+)"'))
eq("E9b 写成双反斜杠才合法", parse_query('{a="b"} | regexp "(?P<m>\\\\w+)"').stages[0].pattern, "(?P<m>\\w+)")
eq("E9c 反引号是原始字符串免转义", parse_query('{a="b"} | regexp `(?P<m>\\w+)`').stages[0].pattern, "(?P<m>\\w+)")
raises("E9d 匿名捕获组在解析期就被拒", lambda: parse_query('{a="b"} | regexp "(\\\\w+)"'))
eq("E9e regexp 命名组提取", labels_of('{a="b"} | regexp `(?P<method>\\w+) (?P<path>\\S+)`', "GET /api/v1/users").get("method"), "GET")
eq("E10 嵌套 JSON 用 _ 摊平", labels_of('{a="b"} | json', '{"a":{"b":1}}').get("a_b"), "1")
eq("E11 json 支持重命名表达式", labels_of('{a="b"} | json status="http_status"', '{"http_status":200}').get("status"), "200")
eq("E12 line_format 改的是显示行", entry_of('{a="b"} | json | line_format "{{.level}}"', GOOD).line, "error")
eq("E13 label_format 可从已有标签派生", labels_of('{a="b"} | json | label_format lv=level', GOOD).get("lv"), "error")
eq("E14 数值过滤取不到数字时不丢行(坑)", ERROR_LABEL in labels_of('{a="b"} | json | status >= 500', '{"other":1}'), True)
errs = [E(T0 - 2 * SEC, GOOD, dict(API)), E(T0 - SEC, BAD, dict(API))]
raises("E15 指标查询含错误直接失败(官方原文)", lambda: run('rate({app="api"} | json [5m])', errs))
close("E15b 补上 __error__ 过滤后查询可用", one(run('rate({app="api"} | json | __error__ = "" [5m])', errs)), 1 / 300.0)
eq("E16 unwrap 遇非数字打错误标签", labels_of('{a="b"} | json | unwrap d', '{"d":"abc"}').get(ERROR_LABEL), SAMPLE_EXTRACTION_ERR)
raises("E16b unwrap 后不带过滤依然失败", lambda: run('sum_over_time({app="api"} | json | unwrap d [5m])', [E(T0 - SEC, '{"d":"abc"}', dict(API))]))
close("E17 加上过滤后成功", one(run('sum_over_time({app="api"} | json | unwrap d | __error__ = "" [5m])', [E(T0 - SEC, '{"d":7}', dict(API))])), 7.0)
eq("E18 unwrap 会消费掉被 unwrap 的标签", "d" in labels_of('{a="b"} | json | unwrap d', '{"d":7}'), False)

# ==================================================================== F
section("F")
api_entries = [E(T0 - (100 - i) * SEC, "hello", dict(API)) for i in range(100)]
web_entries = [E(T0 - (100 - i) * SEC, "world!", dict(WEB)) for i in range(50)]
mixed = api_entries + web_entries
eq("F1 count_over_time 数行数", one(run('sum(count_over_time({env="prod"}[5m]))', mixed)), 150.0)
close("F2 rate = 行数/窗口秒数", one(run('rate({app="api"}[5m])', mixed)), 100 / 300.0)
byapp = run('sum by (app) (count_over_time({env="prod"}[5m]))', mixed)
eq("F3 by 分组产生两条序列", len(byapp), 2)
eq("F3b 分组键是标签对", byapp.get((("app", "api"),)), 100.0)
eq("F3c 第二组", byapp.get((("app", "web"),)), 50.0)
eq("F4 count 外层算子数序列条数", one(run('count(count_over_time({env="prod"}[5m]))', mixed)), 2.0)
eq("F5 bytes_over_time 累加行字节", one(run('sum(bytes_over_time({app="api"}[5m]))', api_entries)), 500.0)
close("F5b bytes_rate = 字节/窗口", one(run('sum(bytes_rate({app="api"}[5m]))', api_entries)), 500 / 300.0)
q4 = [E(T0 - (4 - i) * SEC, json.dumps({"d": v}), (("app", "q"),)) for i, v in enumerate([10, 20, 30, 40])]
close("F6 分位数走线性插值(非取中位元素)", one(run('quantile_over_time(0.5, {app="q"} | json | unwrap d [5m])', q4)), 25.0)
close("F6b φ=0.25 插值", one(run('quantile_over_time(0.25, {app="q"} | json | unwrap d [5m])', q4)), 17.5)
close("F6c φ=0 取最小", one(run('quantile_over_time(0, {app="q"} | json | unwrap d [5m])', q4)), 10.0)
close("F6d φ=1 取最大", one(run('quantile_over_time(1, {app="q"} | json | unwrap d [5m])', q4)), 40.0)
q3 = [E(T0 - (3 - i) * SEC, json.dumps({"d": v}), (("app", "q3"),)) for i, v in enumerate([10, 20, 30])]
close("F6e 奇数个样本落在元素上不插值", one(run('quantile_over_time(0.5, {app="q3"} | json | unwrap d [5m])', q3)), 20.0)
close("F7 avg_over_time", one(run('avg_over_time({app="q"} | json | unwrap d [5m])', q4)), 25.0)
close("F8 sum_over_time", one(run('sum_over_time({app="q"} | json | unwrap d [5m])', q4)), 100.0)
close("F9 max_over_time", one(run('max_over_time({app="q"} | json | unwrap d [5m])', q4)), 40.0)
close("F10 min_over_time", one(run('min_over_time({app="q"} | json | unwrap d [5m])', q4)), 10.0)
close("F11 first_over_time 按时间序", one(run('first_over_time({app="q"} | json | unwrap d [5m])', q4)), 10.0)
close("F12 last_over_time 按时间序", one(run('last_over_time({app="q"} | json | unwrap d [5m])', q4)), 40.0)
close("F13 stdvar 用总体口径", one(run('stdvar_over_time({app="q"} | json | unwrap d [5m])', q4)), 125.0)
close("F14 stddev = sqrt(总体方差)", one(run('stddev_over_time({app="q"} | json | unwrap d [5m])', q4)), math.sqrt(125.0))
dur = [E(T0 - (2 - i) * SEC, '{"d":"1.5s"}', (("app", "q"),)) for i in range(2)]
close("F15 unwrap duration() 把 1.5s 转成 1.5 秒", one(run('sum_over_time({app="q"} | json | unwrap duration(d) [5m])', dur)), 3.0)
szt = [E(T0 - SEC, '{"sz":"2KB"}', (("app", "q"),))]
close("F16 unwrap bytes() 按 1024 进制转字节", one(run('sum_over_time({app="q"} | json | unwrap bytes(sz) [5m])', szt)), 2048.0)
streams = [dict(API), {"app": "idle"}]
eq("F17 absent_over_time 空流返回 1", run('absent_over_time({app="idle"}[5m])', api_entries, streams).get((("app", "idle"),)), 1.0)
eq("F17b 有数据的流返回 0", run('absent_over_time({app="api"}[5m])', api_entries, streams).get(series_key(dict(API))), 0.0)
win = [E(T0 - 400 * SEC, "old", dict(API)), E(T0 - 10 * SEC, "new", dict(API))]
close("F18 窗口外的行不计入", one(run('count_over_time({app="api"}[5m])', win)), 1.0)
sparse = [E(T0 - SEC, "a", dict(API)), E(T0 - 2 * SEC, "b", {"app": "web"})]
grp = run('sum by (env) (count_over_time({app=~"api|web"}[5m]))', sparse)
eq("F19 by 时缺失标签归入空串组", grp.get((("env", ""),)), 1.0)
eq("F19b 有值的标签单独成组", grp.get((("env", "prod"),)), 1.0)
eq("F20 without 按剩余标签分组", run('sum without (env) (count_over_time({app="api"}[5m]))', api_entries).get((("app", "api"),)), 100.0)
raises("F21 count_over_time 不接受 unwrap 后的数值", lambda: run('count_over_time({app="q"} | json | unwrap d [5m])', q4))
raises("F22 avg_over_time 必须配合 unwrap", lambda: run('avg_over_time({app="q"} | json [5m])', q4))
raises("F23 只支持一层外层聚合", lambda: parse_query('sum(sum(rate({app="a"}[5m])))'))
eq("F24 分位数实现独立可测", prom_quantile(0.5, [10, 20, 30, 40]), 25.0)
eq("F25 总体方差独立可测", population_stdvar([1, 2, 3, 4]), 1.25)

# ==================================================================== G~J
import checks_ingest  # noqa: E402  —— 放在此处：G~J 不依赖上面的夹具

checks_ingest.run_all()

# ----------------------------------------------------------------- 汇总
if __name__ == "__main__":
    sys.exit(report())
