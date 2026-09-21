#!/usr/bin/env python3
"""基准结果的**长期趋势存储与分片**：Mozilla Perfherder vs Chrome Perf (Catapult)。

两套数据模型的口径全部来自逐行实读的源码：

``mozilla/treeherder`` — ``treeherder/etl/perf.py`` 与 ``treeherder/perf/models.py``
  1. ``_get_signature_hash(props)`` = ``sha1("".join(sorted(keys + str_values))).hexdigest()``
     —— **key 与 value 被放进同一个列表一起排序**，非字符串 value 走
     ``json.dumps(v, sort_keys=True)``；长度 ``SIGNATURE_HASH_LENGTH = 40``；
  2. summary 系列只在 ``suite.get("value") is not None`` 时才建，属性是
     ``{"suite": name} + reference_data + extra_properties``；
  3. subtest 的属性里带 ``"parent_signature": summary_signature_hash``，signature 上另有
     ``has_subtests`` 布尔；
  4. tags 与 extraOptions 都先 ``sorted`` 再拼：``_order_and_concat = " ".join(sorted(words))``，
     extraOptions 同时以 ``{"test_options": sorted(...)}`` 进 hash；
  5. ``lower_is_better`` 默认 **True**（``suite.get("lowerIsBetter", True)``）；
  6. 唯一约束：(repository, framework, application, signature_hash) 与
     (repository, job, push, push_timestamp, signature)；复合索引
     (repository, signature, push_timestamp)；
  7. ``_create_or_update_signature`` 里 ``last_updated`` **只增不减**；
  8. 告警严重度排序 ``SEVERITY_RANK = {None: 0, NORMAL: 1, SUBCRITICAL: 2, CRITICAL: 3}``。

``catapult-project/catapult`` — ``dashboard/dashboard/models/graph_data.py``
  1. ``TestMetadata`` 的主键就是**完整路径** ``master/bot/test/metric/page``；
  2. ``bot`` 是 ComputedProperty：**只有路径恰好 3 段**才返回 ``Master/Bot`` 键，否则 None；
  3. ``parent_test`` 是 ComputedProperty：路径 **< 4 段返回 None**（test suite），
     否则是 ``"/".join(parts[:-1])`` 的键 —— **父子关系由路径前缀推导，没有外键字段**；
  4. ``Row`` 是 ``ndb.Expando``：``parent_test`` 由父键推导、``revision = key.integer_id()``、
     ``value`` 是索引过的 FloatProperty、``error``（标准差）**不索引**；
  5. 补充列靠**前缀约定**区分语义：``d_`` 数据点、``r_`` 修订号、``a_`` 标注；
  6. ``LastAddedRevision`` 被**单独拆成一个实体**，源码注释写明是为了避免 datastore 写热点
     （"avoid contention issues"）。

无第三方依赖；自检完全确定性。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

SIGNATURE_HASH_LENGTH = 40
SEVERITY_RANK = {None: 0, "normal": 1, "subcritical": 2, "critical": 3}

# models.py 里各字段的 max_length
FIELD_LIMITS = {"suite": 80, "test": 80, "application": 10,
                "tags": 360, "extra_options": 422, "measurement_unit": 50}


# --------------------------------------------------------------------------
# Perfherder：内容寻址的 40 字符签名
# --------------------------------------------------------------------------
def _order_and_concat(words: List[str]) -> str:
    return " ".join(sorted(words))


def signature_hash(props: Dict[str, object]) -> str:
    """复刻 treeherder/etl/perf.py 的 _get_signature_hash。"""
    signature_prop_values = list(props.keys())
    str_values = []
    for value in props.values():
        if not isinstance(value, str):
            str_values.append(json.dumps(value, sort_keys=True))
        else:
            str_values.append(value)
    signature_prop_values.extend(str_values)

    sha = hashlib.sha1()
    sha.update("".join(map(str, sorted(signature_prop_values))).encode("utf-8"))
    return sha.hexdigest()


def suite_ingest(suite: dict, reference_data: dict, extra_properties: dict) -> dict:
    """复刻 _load_perf_datum 里 summary / subtest 两侧的属性构造。"""
    extra = dict(extra_properties)
    extra_options = ""
    if suite.get("extraOptions"):
        extra = {"test_options": sorted(suite["extraOptions"])}
        extra_options = _order_and_concat(suite["extraOptions"])

    out = {
        "tags": _order_and_concat(suite.get("tags", [])),
        "extra_options": extra_options,
        "lower_is_better": suite.get("lowerIsBetter", True),
        "has_subtests": bool(suite.get("subtests")),
    }

    summary_hash = None
    if suite.get("value") is not None:
        summary_props = {"suite": suite["name"]}
        summary_props.update(reference_data)
        summary_props.update(extra)
        summary_hash = signature_hash(summary_props)
        out["summary_signature_hash"] = summary_hash

    sub_hashes = {}
    for sub in suite.get("subtests", []):
        props = {"suite": suite["name"], "test": sub["name"]}
        props.update(reference_data)
        if summary_hash is not None:
            props["parent_signature"] = summary_hash
        sub_hashes[sub["name"]] = signature_hash(props)
    out["subtest_hashes"] = sub_hashes
    return out


@dataclass
class PerfherderSignature:
    repository: str
    framework: str
    application: str
    hash: str
    suite: str
    test: str = ""
    last_updated: str = ""
    has_subtests: bool = False
    parent_signature: Optional[str] = None


class PerfherderStore:
    """只建模两条唯一约束与 last_updated 的单调性。"""

    def __init__(self) -> None:
        self.signatures: Dict[Tuple[str, str, str, str], PerfherderSignature] = {}
        self.data: set = set()

    def upsert(self, sig: PerfherderSignature, defaults_last_updated: str) -> PerfherderSignature:
        key = (sig.repository, sig.framework, sig.application, sig.hash)
        if key in self.signatures:
            old = self.signatures[key]
            if old.last_updated > defaults_last_updated:
                defaults_last_updated = old.last_updated   # 只增不减
            old.last_updated = defaults_last_updated
            return old
        sig.last_updated = defaults_last_updated
        self.signatures[key] = sig
        return sig

    def add_datum(self, repository: str, job: str, push: str, ts: str, sig_hash: str) -> bool:
        key = (repository, job, push, ts, sig_hash)
        if key in self.data:
            return False
        self.data.add(key)
        return True


# --------------------------------------------------------------------------
# Chrome Perf (Catapult)：路径即主键
# --------------------------------------------------------------------------
@dataclass
class TestMetadata:
    """主键 id 就是 master/bot/test/metric/page。"""

    key_id: str
    units: Optional[str] = None
    has_rows: bool = False
    deprecated: bool = False

    @property
    def parts(self) -> List[str]:
        return self.key_id.split("/")

    def bot(self) -> Optional[str]:
        """ComputedProperty：只有 3 段才是 test suite。"""
        parts = self.parts
        return "%s/%s" % (parts[0], parts[1]) if len(parts) == 3 else None

    def parent_test(self) -> Optional[str]:
        """ComputedProperty：< 4 段 ⇒ None；否则取去掉最后一段的路径。"""
        parts = self.parts
        if len(parts) < 4:
            return None
        return "/".join(parts[:-1])

    def master(self) -> str:
        return self.parts[0]

    def test_name(self) -> str:
        return self.parts[-1]


@dataclass
class Row:
    """Row 的主键是 (test path, revision)；revision = key.integer_id()。"""

    parent_path: str
    revision: int
    value: float
    error: Optional[float] = None
    supplemental: Dict[str, object] = field(default_factory=dict)

    SUPPLEMENT_PREFIX = {"d_", "r_", "a_"}

    def validate_supplemental(self) -> List[str]:
        """返回不合规的列名（补充列必须带 d_ / r_ / a_ 前缀）。"""
        return [k for k in self.supplemental if k[:2] not in self.SUPPLEMENT_PREFIX]

    def is_indexed(self, name: str) -> bool:
        """只有显式声明的属性才索引：value 索引、error 不索引。"""
        return name in ("value", "revision", "timestamp")


class CatapultStore:
    def __init__(self) -> None:
        self.rows: Dict[Tuple[str, int], Row] = {}
        self.last_added: Dict[str, int] = {}     # LastAddedRevision 独立实体

    def add_point(self, row: Row) -> bool:
        key = (row.parent_path, row.revision)
        existed = key in self.rows
        self.rows[key] = row                     # 同一 revision 重复上报 ⇒ 覆盖
        cur = self.last_added.get(row.parent_path, -1)
        if row.revision > cur:
            self.last_added[row.parent_path] = row.revision
        return not existed

    def series(self, path: str) -> List[Tuple[int, float]]:
        return sorted((rev, r.value) for (p, rev), r in self.rows.items() if p == path)


def severity_rank(severity: Optional[str]) -> int:
    return SEVERITY_RANK.get(severity, 0)


if __name__ == "__main__":
    print("Perfherder signature_hash:")
    props = {"suite": "tp6", "test": "facebook", "option_collection": "opt"}
    print(" ", props, "->", signature_hash(props))
    print("  键值互换得到同一个 hash:",
          signature_hash({"suite": "facebook", "test": "tp6", "option_collection": "opt"}))
    print("  键与值互换:", signature_hash({"a": "1"}), "==", signature_hash({"1": "a"}))

    print("Catapult test path:")
    t = TestMetadata("ChromiumPerf/linux-release/sunspider/Total")
    print("  id=%s master=%s bot=%s parent_test=%s" % (t.key_id, t.master(), t.bot(), t.parent_test()))
    suite = TestMetadata("ChromiumPerf/linux-release/sunspider")
    print("  id=%s bot=%s parent_test=%s" % (suite.key_id, suite.bot(), suite.parent_test()))
