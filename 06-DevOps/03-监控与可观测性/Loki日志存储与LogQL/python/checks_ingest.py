"""Loki 存储与摄入侧校验（G~J）。

由 demo.py 调用 `run_all()`，共用 checkframe 的计数器，因此汇总数字是全局的。

  G ingester 时序/去重/chunk 滚动/WAL/仲裁
  H 摄入校验与租户流上限
  I 存储 schema 与索引约束
  J 单位换算（1024 进制）
"""

from __future__ import annotations

from checkframe import check, close, eq, raises, section
from logql_ast import classify_number, parse_bytes, parse_duration
from logql_eval import render_template
from loki_chunk import (
    CHUNK_DEFAULTS,
    DEFAULT_ENCODING,
    ENCODINGS,
    ILLUSTRATIVE_RATIO,
    Stream,
    chunk_object_key,
    quorum,
)
from loki_ingester import Ingester
from loki_limits import StreamRegistry, check_labels, check_line, resolve_limits
from loki_schema import (
    SCHEMA_DEFAULTS,
    schema_check,
    schema_version_number,
    validate_from_date,
    validate_index_period,
)

T0 = 1_700_000_000_000_000_000
SEC = 10**9
API = (("app", "api"), ("env", "prod"))

__all__ = ["run_all"]


def run_all() -> None:
    # ================================================================ G
    section("G")
    ing = Ingester()
    eq("G1 递增时间戳可追加", ing.push("team-a", API, T0, "line0"), "appended")
    eq("G1b 第二条", ing.push("team-a", API, T0 + SEC, "line1"), "appended")
    eq("G2 同 ts 同内容 = 重复被静默忽略", ing.push("team-a", API, T0 + SEC, "line1"), "duplicate")
    eq("G3 同 ts 不同内容 = 接受(坑)", ing.push("team-a", API, T0 + SEC, "other"), "appended")
    eq("G4 时间戳回退被拒", ing.push("team-a", API, T0 - SEC, "back"), "out_of_order")
    st = ing.stream("team-a", API)
    eq("G4b 被拒的行没有入账", len(st.entries), 3)
    eq("G4c 重复计数单独统计", st.ignored_duplicates, 1)
    eq("G4d 乱序计数单独统计", st.rejected_out_of_order, 1)
    idle_ing = Ingester()
    idle_ing.push("t", API, T0, "x")
    eq("G5 空闲 30m 触发刷写", idle_ing.stream("t", API).flush_reasons(T0 + 1800 * SEC), ["idle"])
    eq("G5b 未到 30m 不触发", idle_ing.stream("t", API).flush_reasons(T0 + 1799 * SEC), [])
    age_ing = Ingester(idle_period_s=10**9)
    age_ing.push("t", API, T0, "x")
    eq("G6 存活 2h 强制刷写", age_ing.stream("t", API).flush_reasons(T0 + 7200 * SEC), ["age"])
    size_ing = Ingester(target_size=100)
    for i in range(6):
        size_ing.push("t", API, T0 + i * SEC, "x" * 100)
    eq("G7 压缩后达标触发刷写", size_ing.stream("t", API).flush_reasons(T0 + 5 * SEC), ["size"])
    tri = Stream(tenant="t", labels=API, target_size=100, idle_period_s=1800.0, max_age_s=7200.0)
    for i in range(6):
        tri.push(T0 + i * SEC, "x" * 100)
    eq("G8 三条件可同时成立且顺序固定", tri.flush_reasons(T0 + 7200 * SEC), ["idle", "size", "age"])
    chunk = tri.flush(T0 + 7200 * SEC, "idle")
    eq("G9 刷写后 chunk 记录条目数", chunk.entries, 6)
    eq("G9b 刷写后内存清空", tri.entries, [])
    eq("G9c flush 记录进 flushed", len(tri.flushed), 1)
    eq("G10 对象键带租户前缀(存储层隔离)", chunk.object_key.startswith("t/"), True)
    eq("G11 rf=3 时仲裁数 2", quorum(3), 2)
    eq("G11b rf=5 时仲裁数 3", quorum(5), 3)
    eq("G11c rf=1 时仲裁数 1", quorum(1), 1)
    eq("G11d rf=2 时仲裁数 2(两副本都要成功)", quorum(2), 2)
    eq("G12 租户不同则对象键不同", chunk_object_key("a", API, 1, 2, "c") != chunk_object_key("b", API, 1, 2, "c"), True)
    wal_ing = Ingester()
    for i in range(3):
        wal_ing.push("t", API, T0 + i * SEC, f"l{i}")
    eq("G13 WAL 记录未刷写数据", len(wal_ing.wal.pending()), 3)
    wal_ing.crash()
    eq("G13b 崩溃后内存流清空", len(wal_ing.streams), 0)
    eq("G13c WAL 重放恢复全部条目", wal_ing.recover().get(("t", API)), 3)
    sd_ing = Ingester()
    for i in range(3):
        sd_ing.push("t", API, T0 + i * SEC, f"l{i}")
    eq("G14 优雅关闭会刷写 chunk", len(sd_ing.shutdown(T0 + 10 * SEC)), 1)
    eq("G14b 刷写后 WAL 可截断", sd_ing.wal.pending(), [])
    eq("G14c 优雅关闭后无需重放", sd_ing.recover(), {})
    ck_ing = Ingester()
    ck_ing.push("t", API, T0, "x")
    eq("G15 未到 checkpoint 周期不动", ck_ing.wal.checkpoint(T0 + 100 * SEC), False)
    eq("G15b 到 5m 折叠 checkpoint", ck_ing.wal.checkpoint(T0 + 300 * SEC), True)
    eq("G15c checkpoint 后无待重放记录", ck_ing.wal.pending(), [])
    eq("G16 chunk_encoding 默认 gzip", DEFAULT_ENCODING, "gzip")
    eq("G16b 官方最佳实践推荐 snappy(≠ 默认)", DEFAULT_ENCODING == "snappy", False)
    eq("G17 压缩比表覆盖全部编码", set(ILLUSTRATIVE_RATIO) == set(ENCODINGS), True)
    eq("G18 未压缩上限 256KiB", CHUNK_DEFAULTS["chunk_block_size"], 262144)
    eq("G18b 压缩后目标 1.5MiB", CHUNK_DEFAULTS["chunk_target_size"], 1572864)
    eq("G18c 未压缩上限小于压缩目标", CHUNK_DEFAULTS["chunk_block_size"] < CHUNK_DEFAULTS["chunk_target_size"], True)
    eq("G18d 存活上限 2h", CHUNK_DEFAULTS["max_chunk_age"], 7200.0)
    raises("G19 未知 chunk_encoding 报错", lambda: Ingester(encoding="brotli"), ValueError)

    # ================================================================ H
    section("H")
    lim = resolve_limits()
    eq("H1 行长等于上限可通过", check_line(lim, 262144), "ok")
    eq("H2 超限默认拒绝(truncate=false)", check_line(lim, 262145), "rejected")
    eq("H3 开启 truncate 则截断而非拒绝", check_line(resolve_limits({"max_line_size_truncate": True}), 262145), "truncated")
    eq("H4 max_line_size=0 表示不限", check_line(resolve_limits({"max_line_size": "0b"}), 10**9), "ok")
    eq("H5 标签数超限被拒", check_labels(lim, {f"l{i}": "v" for i in range(31)}), "too_many_labels")
    eq("H5b 恰好 30 个可通过", check_labels(lim, {f"l{i}": "v" for i in range(30)}), "ok")
    eq("H6 标签名过长被拒", check_labels(lim, {"x" * 1025: "v"}), "label_name_too_long")
    eq("H7 标签值过长被拒", check_labels(lim, {"x": "v" * 2049}), "label_value_too_long")
    lim3 = resolve_limits({"max_global_streams_per_user": 3})
    reg = StreamRegistry(idle_period_s=1800.0)
    for i in range(3):
        reg.touch("t", (("a", str(i)),), T0)
    eq("H8 已存在的流不占新额度", reg.admit("t", (("a", "1"),), T0, lim3, T0).status, 200)
    eq("H8b 超上限的新流返回 429", reg.admit("t", (("a", "new"),), T0, lim3, T0).status, 429)
    eq("H8c 超限原因串", reg.admit("t", (("a", "new"),), T0, lim3, T0).reason, "stream_limit")
    eq("H9 流上限按租户隔离", reg.admit("t2", (("a", "x"),), T0, lim3, T0).status, 200)
    later = T0 + 1801 * SEC
    eq("H10 活跃窗口到期后额度释放", reg.admit("t", (("a", "new"),), later, lim3, later).status, 200)
    eq("H11 默认无限流上限时不拦", StreamRegistry().admit("z", (("a", "1"),), T0, lim, T0).status, 200)

    # ================================================================ I
    section("I")
    eq("I1 推荐 store 为 tsdb", SCHEMA_DEFAULTS["store"], "tsdb")
    eq("I2 推荐 schema 为 v13", SCHEMA_DEFAULTS["schema"], "v13")
    eq("I3 tsdb 索引 period 必须 24h", SCHEMA_DEFAULTS["index_period"], "24h")
    eq("I4 row_shards 默认 16", SCHEMA_DEFAULTS["row_shards"], 16)
    eq("I5 tsdb+v13 可启动", schema_check("tsdb", "v13"), None)
    eq("I6 boltdb-shipper 已移除", (schema_check("boltdb-shipper", "v13") or "").startswith("CONFIG ERROR"), True)
    eq("I7 非 tsdb 触发配置错误", "tsdb` index type is required" in (schema_check("boltdb", "v13") or ""), True)
    eq("I8 v12 触发 schema 版本错误", "schema v13 is required" in (schema_check("tsdb", "v12") or ""), True)
    eq("I9 关掉 structured metadata 后老配置可用", schema_check("boltdb", "v11", allow_structured_metadata=False), None)
    eq("I10 schema 版本按数字比较", schema_version_number("v13") > schema_version_number("v9"), True)
    eq("I11 tsdb 只接受 24h", validate_index_period("tsdb", "12h"), False)
    eq("I11b 非 tsdb 不校验 period", validate_index_period("boltdb", "12h"), True)
    eq("I12 全新安装 from 须在过去", validate_from_date(True, "2024-01-01", "2026-09-18").ok, True)
    eq("I12b 全新安装 from 写未来则不可用", validate_from_date(True, "2030-01-01", "2026-09-18").ok, False)
    eq("I13 追加 schema 段 from 须在未来", validate_from_date(False, "2030-01-01", "2026-09-18").ok, True)
    eq("I13b 追加时写过去会让旧数据不可读", validate_from_date(False, "2020-01-01", "2026-09-18").ok, False)

    # ================================================================ J
    section("J")
    eq("J1 时长解析 5m", parse_duration("5m"), 300.0)
    eq("J2 复合时长 1h30m", parse_duration("1h30m"), 5400.0)
    eq("J3 字节 256KB 是 1024 进制", parse_bytes("256KB"), 262144.0)
    eq("J3b 1.5MB 与 chunk_target_size 对齐", parse_bytes("1.5MB"), 1572864.0)
    eq("J4 duration 字面量归类", classify_number("1s"), ("duration", 1.0))
    eq("J5 bytes 字面量归类", classify_number("20MB"), ("bytes", 20 * 1024**2))
    eq("J6 纯数字归类", classify_number("500"), ("plain", 500.0))
    eq("J7 模板函数 upper", render_template("{{.l | upper}}", {"l": "err"}), "ERR")
    eq("J7b 模板截断", render_template("{{.l | trunc 3}}", {"l": "abcdef"}), "abc")
    close("J8 断言框架自检(浮点容差)", 0.1 + 0.2, 0.3, 1e-9)
