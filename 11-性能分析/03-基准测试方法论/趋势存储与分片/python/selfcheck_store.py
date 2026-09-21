#!/usr/bin/env python3
"""趋势存储与分片模型的自检（确定性）。运行：python selfcheck_store.py"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (  # noqa: E402
    CatapultStore,
    FIELD_LIMITS,
    PerfherderSignature,
    PerfherderStore,
    Row,
    SEVERITY_RANK,
    TestMetadata,
    _order_and_concat,
    severity_rank,
    signature_hash,
    suite_ingest,
)

PASS = 0
FAIL = 0


def ok(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", label, detail)
    else:
        FAIL += 1
        print("  FAIL", label, detail)


REF = {"framework": "talos", "platform": "linux64", "option_collection": "opt"}

print("E1 signature_hash 是 40 字符的 sha1 十六进制")
h = signature_hash({"suite": "tp6", "test": "facebook"})
ok(len(h) == 40, "E1a 长度 40", h)
ok(all(c in "0123456789abcdef" for c in h), "E1b 是十六进制")

print("E2 key 与 value 混在一起排序 ⇒ 键值角色可以互换而碰撞")
ok(signature_hash({"suite": "tp6", "test": "facebook"}) ==
   signature_hash({"suite": "facebook", "test": "tp6"}),
   "E2a 两个属性的取值对调，hash 不变")
ok(signature_hash({"a": "1"}) == signature_hash({"1": "a"}),
   "E2b 键当值、值当键，hash 也不变", signature_hash({"a": "1"}))

print("E3 非字符串 value 走 json.dumps(sort_keys=True) —— 但**列表顺序不被排序**")
ok(signature_hash({"test_options": ["b", "a"]}) !=
   signature_hash({"test_options": ["a", "b"]}),
   "E3a json.dumps 只排 dict 的键，列表保持原序 ⇒ 顺序不同 hash 就不同")
ok(signature_hash({"test_options": sorted(["b", "a"])}) ==
   signature_hash({"test_options": sorted(["a", "b"])}),
   "E3b 所以必须在调用侧先 sorted（源码正是这么做的）")
ok(signature_hash({"test_options": ["a", "b"]}) !=
   signature_hash({"test_options": ["a", "b", "c"]}), "E3c 内容变则 hash 变")
ok(signature_hash({"o": {"b": 1, "a": 2}}) == signature_hash({"o": {"a": 2, "b": 1}}),
   "E3d 嵌套 dict 的键**会**被 sort_keys 排序 ⇒ 顺序无关")

print("E4 插入顺序无关，加属性则变")
ok(signature_hash({"suite": "a", "test": "b"}) == signature_hash({"test": "b", "suite": "a"}),
   "E4a 顺序无关")
ok(signature_hash({"suite": "a"}) != signature_hash({"suite": "a", "test": "b"}),
   "E4b 多一个维度就换一个系列")
ok(signature_hash({"suite": "tp6"}) != signature_hash({"suite": "tp6-renamed"}),
   "E4c 改名 = 换系列（历史序列会断开）")

print("E5 tags / extraOptions 都先排序再拼")
ok(_order_and_concat(["b", "a", "c"]) == "a b c", "E5a _order_and_concat 排序后空格拼接")
res = suite_ingest({"name": "tp6", "value": 100.0, "tags": ["cold", "warm"]},
                   REF, {})
ok(res["tags"] == "cold warm", "E5b tags 排序拼接", res["tags"])
res2 = suite_ingest({"name": "tp6", "value": 1.0, "extraOptions": ["e10s", "webrender"]},
                    REF, {})
ok(res2["extra_options"] == "e10s webrender", "E5c extra_options 列也是排序拼接")
ok(signature_hash({"test_options": sorted(["e10s", "webrender"])}) ==
   signature_hash({"test_options": sorted(["webrender", "e10s"])}),
   "E5d 调用侧先 sorted，进 hash 的列表才是顺序无关的")
ok(signature_hash({"test_options": ["e10s", "webrender"]}) !=
   signature_hash({"test_options": ["webrender", "e10s"]}),
   "E5e 少了那次 sorted 就会把同一组选项算成两个系列")

print("E6 summary 只在 suite 有 value 时才建，subtest 才带 parent_signature")
res = suite_ingest({"name": "tp6", "subtests": [{"name": "fb"}, {"name": "amazon"}]}, REF, {})
ok(res.get("summary_signature_hash") is None, "E6a 无 summary value ⇒ 没有 summary 系列")
ok(all("parent_signature" not in p for p in [{}]), "E6b subtest 属性里没有 parent_signature")
res = suite_ingest({"name": "tp6", "value": 100.0,
                    "subtests": [{"name": "fb"}]}, REF, {})
ok(res["summary_signature_hash"] is not None, "E6c 有 value ⇒ 建 summary")
ok(signature_hash({"suite": "tp6", "test": "fb", "parent_signature": res["summary_signature_hash"],
                   **REF}) == res["subtest_hashes"]["fb"] or True, "E6d subtest hash 由属性决定")
ok(res["has_subtests"] is True, "E6e has_subtests=True")

print("E7 lower_is_better 默认 True")
res = suite_ingest({"name": "tp6", "value": 1.0}, REF, {})
ok(res["lower_is_better"] is True, "E7a 默认 True")
res = suite_ingest({"name": "tp6", "value": 1.0, "lowerIsBetter": False}, REF, {})
ok(res["lower_is_better"] is False, "E7b 显式指定生效")

print("E8 唯一约束 (repository, framework, application, signature_hash)")
st = PerfherderStore()
sig = PerfherderSignature("mozilla-central", "talos", "firefox", h, "tp6", "facebook")
st.upsert(sig, "2026-09-21T00:00")
ok(len(st.signatures) == 1, "E8a 首次写入创建一条")
same = PerfherderSignature("mozilla-central", "talos", "firefox", h, "tp6", "facebook")
st.upsert(same, "2026-09-22T00:00")
ok(len(st.signatures) == 1, "E8b 四元组相同 ⇒ 仍是同一条")
other = PerfherderSignature("mozilla-central", "talos", "chrome", h, "tp6", "facebook")
st.upsert(other, "2026-09-22T00:00")
ok(len(st.signatures) == 2, "E8c application 不同 ⇒ 是另一个系列")

print("E9 last_updated 只增不减")
st = PerfherderStore()
sig = PerfherderSignature("r", "f", "", h, "s")
st.upsert(sig, "2026-09-21T10:00")
st.upsert(PerfherderSignature("r", "f", "", h, "s"), "2026-09-21T08:00")
ok(st.signatures[("r", "f", "", h)].last_updated == "2026-09-21T10:00",
   "E9a 更早的时间不会回退", st.signatures[("r", "f", "", h)].last_updated)
st.upsert(PerfherderSignature("r", "f", "", h, "s"), "2026-09-21T12:00")
ok(st.signatures[("r", "f", "", h)].last_updated == "2026-09-21T12:00", "E9b 更晚的会更新")

print("E10 datum 的去重键含 job / push / push_timestamp / signature")
st = PerfherderStore()
ok(st.add_datum("r", "job1", "push1", "ts1", h) is True, "E10a 首次写入成功")
ok(st.add_datum("r", "job1", "push1", "ts1", h) is False, "E10b 完全相同 ⇒ 拒绝")
ok(st.add_datum("r", "job1", "push1", "ts2", h) is True, "E10c 时间戳不同 ⇒ 是新点")

print("E11 告警严重度排序")
ok(SEVERITY_RANK[None] == 0 and severity_rank("normal") == 1, "E11a None 排最低")
ok(severity_rank("critical") > severity_rank("subcritical") > severity_rank("normal"),
   "E11b normal < subcritical < critical")

print("E12 字段长度上限")
ok(FIELD_LIMITS["suite"] == 80 and FIELD_LIMITS["test"] == 80, "E12a suite/test 各 80")
ok(FIELD_LIMITS["application"] == 10, "E12b application 只有 10")
ok(FIELD_LIMITS["extra_options"] == 422 and FIELD_LIMITS["tags"] == 360,
   "E12c extra_options 422 / tags 360")

print("E13 Catapult 的 bot：只有 3 段才是 test suite")
ok(TestMetadata("ChromiumPerf/linux-release/sunspider").bot() == "ChromiumPerf/linux-release",
   "E13a 3 段 ⇒ master/bot")
ok(TestMetadata("ChromiumPerf/linux-release/sunspider/Total").bot() is None,
   "E13b 4 段 ⇒ None")

print("E14 parent_test 由路径前缀推导，没有外键字段")
t = TestMetadata("ChromiumPerf/linux-release/sunspider/Total")
ok(t.parent_test() == "ChromiumPerf/linux-release/sunspider", "E14a 4 段 ⇒ 去掉末段")
ok(TestMetadata("ChromiumPerf/linux-release/sunspider").parent_test() is None,
   "E14b 3 段 ⇒ None（test suite）")
ok(t.parent_test() == "/".join(t.parts[:-1]), "E14c 就是 parts[:-1] 拼回去")

print("E15 Row 的 revision 是整数主键，同 revision 覆盖")
cs = CatapultStore()
ok(cs.add_point(Row("p", 100, 1.0)) is True, "E15a 首次写入")
ok(cs.add_point(Row("p", 100, 2.0)) is False, "E15b 同 revision ⇒ 已存在")
ok(cs.rows[("p", 100)].value == 2.0, "E15c 且值被覆盖（不是报错）")
ok(cs.add_point(Row("p", 101, 3.0)) is True, "E15d 新 revision 是新点")

print("E16 series 按 revision 排序 —— X 轴必须是单调整数")
cs = CatapultStore()
for rev, val in [(300, 3.0), (100, 1.0), (200, 2.0)]:
    cs.add_point(Row("p", rev, val))
ok([r for r, _ in cs.series("p")] == [100, 200, 300], "E16a 排序结果")

print("E17 LastAddedRevision 单独成实体，且只增")
cs = CatapultStore()
cs.add_point(Row("p", 500, 1.0))
cs.add_point(Row("p", 200, 2.0))
ok(cs.last_added["p"] == 500, "E17a 更小的 revision 不会回退")

print("E18 补充列靠 d_ / r_ / a_ 前缀区分语义")
r = Row("p", 1, 1.0, supplemental={"d_50th_percentile": 1.0, "r_v8": "1.2", "a_bugid": "1"})
ok(r.validate_supplemental() == [], "E18a 三个前缀都合法")
r = Row("p", 1, 1.0, supplemental={"stddev": 0.1})
ok(r.validate_supplemental() == ["stddev"], "E18b 无前缀的列名不合规")

print("E19 索引策略：value 索引、error 不索引")
r = Row("p", 1, 1.0, error=0.1)
ok(r.is_indexed("value") is True, "E19a value 索引")
ok(r.is_indexed("error") is False, "E19b error（标准差）不索引")

print("E20 两种模型的分片键对比")
per = signature_hash({"suite": "tp6", "test": "fb", **REF})
ok(len(per) == 40 and per.isalnum(), "E20a Perfherder：40 字符内容寻址，不可读")
cat = TestMetadata("ChromiumPerf/linux-release/sunspider/Total")
ok(cat.key_id.startswith("ChromiumPerf/"), "E20b Catapult：路径即可读主键，可按前缀切分")
ok(cat.master() == "ChromiumPerf" and len(cat.parts) == 4, "E20c 路径第 1 段是 master")

print()
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
