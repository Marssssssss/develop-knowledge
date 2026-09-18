"""OTel Collector 配置语义模型:复合键、pipeline 装配、依赖图与环检测。

覆盖官方文档明确定义的结构规则:receivers/processors/exporters/connectors/
extensions/service 六段、`type[/name]` 复合键、按信号类型分 pipeline、
connector 同时出现在 receiver 位与 exporter 位。不追求覆盖全部组件参数。
资料来源见 ../README.md「参考资料」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 官方:type 与 name 均匹配 ^[a-zA-Z][0-9a-zA-Z_]*$;组件 ID 为 type 或 type/name
ID_RE = re.compile(r"^[a-zA-Z][0-9a-zA-Z_]*(?:/[a-zA-Z][0-9a-zA-Z_]*)?$")

COMPONENT_SECTIONS = ("receivers", "processors", "exporters", "connectors", "extensions")
SIGNALS = ("traces", "metrics", "logs", "profiles")
# connectors 是唯一可同时出现在 pipeline 两端列表里的组件
AS_RECEIVER = ("receivers", "connectors")
AS_EXPORTER = ("exporters", "connectors")


class ConfigError(Exception):
    """配置非法。真实 Collector 在启动 service 之前就拒绝加载。"""


def parse_component_id(cid):
    """`batch/traces` -> `("batch", "traces")`;`otlp` -> `("otlp", None)`。"""
    if not isinstance(cid, str) or not ID_RE.match(cid):
        raise ConfigError("非法组件 ID: %r" % (cid,))
    if "/" in cid:
        t, n = cid.split("/", 1)
        return t, n
    return cid, None


def component_type(cid):
    return parse_component_id(cid)[0]


def is_same_type(a, b):
    """同 type、不同 name 是同一组件的两个实例(`batch` 与 `batch/logs`)。"""
    return component_type(a) == component_type(b)


@dataclass
class Pipeline:
    signal: str
    name: object
    receivers: list
    processors: list
    exporters: list

    @property
    def key(self):
        return self.signal if self.name is None else "%s/%s" % (self.signal, self.name)


@dataclass
class Config:
    sections: dict = field(default_factory=dict)
    pipelines: dict = field(default_factory=dict)
    service_extensions: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw):
        cfg = cls(sections={s: set(raw.get(s) or {}) for s in COMPONENT_SECTIONS})
        svc = raw.get("service") or {}
        cfg.service_extensions = list(svc.get("extensions") or [])
        for key, spec in (svc.get("pipelines") or {}).items():
            sig, nm = parse_component_id(key)
            if sig not in SIGNALS:
                raise ConfigError("未知信号类型: %s" % key)
            if key in cfg.pipelines:
                raise ConfigError("重复的 pipeline: %s" % key)
            cfg.pipelines[key] = Pipeline(
                sig, nm,
                list(spec.get("receivers") or []),
                list(spec.get("processors") or []),
                list(spec.get("exporters") or []),
            )
        return cfg

    # ---- 校验 ----
    def defined(self, section):
        return self.sections.get(section, set())

    def validate(self):
        """非法结构抛 ConfigError;非致命问题以 warning 列表返回。"""
        if not self.pipelines:
            raise ConfigError("service.pipelines 不能为空")
        for section, ids in self.sections.items():
            for cid in ids:
                parse_component_id(cid)  # 只做合法性校验
        for p in self.pipelines.values():
            for cid in p.receivers:
                if cid not in self.defined("receivers") and cid not in self.defined("connectors"):
                    raise ConfigError("pipeline %s 引用了未定义的 receiver: %s" % (p.key, cid))
            for cid in p.processors:
                if cid not in self.defined("processors"):
                    raise ConfigError("pipeline %s 引用了未定义的 processor: %s" % (p.key, cid))
            for cid in p.exporters:
                if cid not in self.defined("exporters") and cid not in self.defined("connectors"):
                    raise ConfigError("pipeline %s 引用了未定义的 exporter: %s" % (p.key, cid))
        for cid in self.service_extensions:
            if cid not in self.defined("extensions"):
                raise ConfigError("service.extensions 引用了未定义的 extension: %s" % cid)
        return self._warnings()

    def _warnings(self):
        """未被任何 pipeline 引用的组件:真实 Collector 只打日志,不阻止启动。"""
        used_r, used_p, used_e = set(), set(), set()
        for p in self.pipelines.values():
            used_r.update(p.receivers)
            used_p.update(p.processors)
            used_e.update(p.exporters)
        warns = []
        for cid in sorted(self.defined("receivers") - used_r):
            warns.append("unused receiver: %s" % cid)
        for cid in sorted(self.defined("processors") - used_p):
            warns.append("unused processor: %s" % cid)
        for cid in sorted(self.defined("exporters") - used_e):
            warns.append("unused exporter: %s" % cid)
        for cid in sorted(self.defined("connectors")):
            if not (cid in used_r and cid in used_e):
                warns.append("connector %s 未同时出现在 receiver/exporter 位" % cid)
        return warns

    # ---- 装配视图 ----
    def receiver_fanout(self):
        """receiver -> 引用它的 pipeline(定义序);同一份入站数据被扇出到多路。"""
        out = {}
        for p in self.pipelines.values():
            for r in p.receivers:
                out.setdefault(r, []).append(p.key)
        return out

    def exporter_fanout(self):
        """pipeline -> 它广播到的 exporter 列表(消费侧扇出,同一份数据发 N 份)。"""
        return {p.key: list(p.exporters) for p in self.pipelines.values()}

    def connector_edges(self):
        """connector c:把 c 当 exporter 的 pipeline -> 把 c 当 receiver 的 pipeline。"""
        as_expr, as_recv = {}, {}
        for p in self.pipelines.values():
            for e in p.exporters:
                if e in self.defined("connectors"):
                    as_expr.setdefault(e, []).append(p.key)
            for r in p.receivers:
                if r in self.defined("connectors"):
                    as_recv.setdefault(r, []).append(p.key)
        edges = []
        for c in sorted(set(as_expr) | set(as_recv)):
            for a in as_expr.get(c, []):
                for b in as_recv.get(c, []):
                    edges.append((a, b))
        return edges

    def topo_order(self):
        """connector 依赖图的拓扑序;有环抛错(真实 Collector 启动即失败)。"""
        nodes = sorted(self.pipelines)
        indeg = dict.fromkeys(nodes, 0)
        adj = {n: [] for n in nodes}
        for a, b in self.connector_edges():
            adj[a].append(b)
            indeg[b] += 1
        queue = sorted(n for n in nodes if indeg[n] == 0)
        order = []
        while queue:
            n = queue.pop(0)
            order.append(n)
            for m in adj[n]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    queue.append(m)
        if len(order) != len(nodes):
            raise ConfigError("pipeline 依赖存在环,涉及: %s" % sorted(set(nodes) - set(order)))
        return order


def pipeline_order_of_processor(cfg, pipeline_key, cid):
    """processor 在 pipeline 中的下标;不在该 pipeline 返回 -1。

    顺序即语义:memory_limiter 必须首位,否则它之后的组件已经吃过内存。
    """
    p = cfg.pipelines[pipeline_key]
    for i, c in enumerate(p.processors):
        if c == cid:
            return i
    return -1


def memory_limiter_first(cfg, pipeline_key):
    """该 pipeline 是否把 memory_limiter 放在首位(官方最佳实践)。"""
    procs = cfg.pipelines[pipeline_key].processors
    if not procs:
        return False
    return component_type(procs[0]) == "memory_limiter"
