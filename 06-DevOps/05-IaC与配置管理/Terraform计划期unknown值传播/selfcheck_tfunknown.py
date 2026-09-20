# -*- coding: utf-8 -*-
"""tfunknown 自检：口径来自实际读过的 Terraform v1.9.8 objchange.go 与官方文档。"""
from tfunknown import (UNKNOWN, is_unknown, is_null, Attr, Nested, empty_value,
                       proposed_new, planned_data_resource_object,
                       provider_fill_unknown, _non_computed_sig)

N = 0
FAIL = []


def check(label, cond, detail=""):
    global N
    N += 1
    if not cond:
        FAIL.append("%s  %s" % (label, detail))


# 一个典型 schema：id 是 computed，name 是普通属性，tags 是可选普通属性
SCHEMA = {
    "id": Attr(computed=True),
    "name": Attr(),
    "tags": Attr(optional=True),
}

# ---- 1. prior 为空（新建）时先把 prior 变成 EmptyValue ----
p = proposed_new(SCHEMA, None, {"id": None, "name": "web", "tags": None})
check("A1 新建时 prior 视为全 null", p == {"id": None, "name": "web", "tags": None}, p)
check("A2 EmptyValue 是全 null", empty_value(SCHEMA) == {"id": None, "name": None,
                                                         "tags": None})
# computed 且 config 为 null → 取 prior（此时 prior 是 null，还不是 unknown）
check("A3 ProposedNew 阶段 computed 取 prior 而非直接 unknown", p["id"] is None, p)
# provider 补 unknown 之后才是 known after apply
p2 = provider_fill_unknown(SCHEMA, p)
check("A4 provider 补 unknown 后 id 才是 UNKNOWN", is_unknown(p2["id"]), p2)
check("A5 非 computed 属性不受补 unknown 影响", p2["name"] == "web", p2)

# ---- 2. 已有资源：computed 属性保留 prior ----
prior = {"id": "i-123", "name": "web", "tags": "a"}
p = proposed_new(SCHEMA, prior, {"id": None, "name": "web", "tags": "a"})
check("B1 computed 保留 prior 值", p["id"] == "i-123", p)
p = proposed_new(SCHEMA, prior, {"id": None, "name": "web2", "tags": "a"})
check("B2 改普通属性不影响 computed", p["id"] == "i-123" and p["name"] == "web2", p)
# 非 computed 一律取 config，即使 config 是 null
p = proposed_new(SCHEMA, prior, {"id": None, "name": None, "tags": None})
check("B3 非 computed 即使 config 为 null 也取 config", p["name"] is None, p)

# ---- 3. config 整体 unknown → 返回 prior ----
p = proposed_new(SCHEMA, prior, UNKNOWN)
check("C1 config 整体 unknown 时返回 prior", p == prior, p)
p = proposed_new(SCHEMA, prior, None)
check("C2 config 为 null 时返回 prior", p == prior, p)
p = proposed_new(SCHEMA, None, None)
check("C3 两者都 null 时直接返回 prior", p is None, p)

# ---- 4. 嵌套块整体 unknown（dynamic + unknown for_each）----
NSCHEMA = {"inner": Attr(), "cid": Attr(computed=True)}
NS = Nested("single", NSCHEMA)
p = proposed_new({"blk": Attr(nested=NS)}, {"blk": {"inner": "x", "cid": "c"}},
                 {"blk": UNKNOWN})
check("D1 嵌套块整体 unknown 直接透传", is_unknown(p["blk"]), p)
p = proposed_new({"blk": Attr(nested=NS)}, {"blk": {"inner": "x", "cid": "c"}},
                 {"blk": {"inner": "y", "cid": None}})
check("D2 嵌套块内 computed 同样保留 prior", p["blk"] == {"inner": "y", "cid": "c"}, p)
p = proposed_new({"blk": Attr(nested=NS)}, {"blk": None}, {"blk": None})
check("D3 single 且 config 为 null 时取 config", p["blk"] is None, p)

# ---- 5. list 按下标关联 ----
L = Nested("list", NSCHEMA)
prior = {"b": [{"inner": "p0", "cid": "c0"}, {"inner": "p1", "cid": "c1"}]}
cfg = {"b": [{"inner": "n0", "cid": None}]}
p = proposed_new({"b": Attr(nested=L)}, prior, cfg)
check("E1 list 按位合并，多余的 prior 被丢弃",
      p["b"] == [{"inner": "n0", "cid": "c0"}], p)
cfg = {"b": [{"inner": "n0", "cid": None}, {"inner": "n1", "cid": None}]}
p = proposed_new({"b": Attr(nested=L)}, prior, cfg)
check("E2 两位都能对上", p["b"] == [{"inner": "n0", "cid": "c0"},
                                    {"inner": "n1", "cid": "c1"}], p)
cfg = {"b": [{"inner": "n0", "cid": None}, {"inner": "n1", "cid": None},
             {"inner": "n2", "cid": None}]}
p = proposed_new({"b": Attr(nested=L)}, prior, cfg)
check("E3 超出 prior 长度的元素原样取 config",
      p["b"][2] == {"inner": "n2", "cid": None}, p)
p = proposed_new({"b": Attr(nested=L)}, prior, {"b": []})
check("E4 空 config list 原样返回", p["b"] == [], p)

# ---- 6. map 按键关联 ----
M = Nested("map", NSCHEMA)
prior = {"b": {"x": {"inner": "p", "cid": "cx"}}}
p = proposed_new({"b": Attr(nested=M)}, prior,
                 {"b": {"x": {"inner": "n", "cid": None},
                        "y": {"inner": "q", "cid": None}}})
check("F1 map 存在的键合并 computed", p["b"]["x"] == {"inner": "n", "cid": "cx"}, p)
check("F2 map 不存在的键原样取 config", p["b"]["y"] == {"inner": "q", "cid": None}, p)

# ---- 7. set 关联是启发式：按非 computed 属性匹配 ----
S = Nested("set", NSCHEMA)
prior = {"b": [{"inner": "keep", "cid": "ck"}]}
p = proposed_new({"b": Attr(nested=S)}, prior, {"b": [{"inner": "keep", "cid": None}]})
check("G1 非 computed 值相同时能匹配上", p["b"] == [{"inner": "keep", "cid": "ck"}], p)
p = proposed_new({"b": Attr(nested=S)}, prior, {"b": [{"inner": "other", "cid": None}]})
check("G2 匹配不上时原样取 config", p["b"] == [{"inner": "other", "cid": None}], p)
check("G3 set 签名只含非 computed 属性",
      _non_computed_sig(NSCHEMA, {"inner": "a", "cid": "zzz"}) ==
      _non_computed_sig(NSCHEMA, {"inner": "a", "cid": "yyy"}))

# ---- 8. data source：用整体 unknown 的 prior ----
p = planned_data_resource_object(SCHEMA, {"id": None, "name": "web", "tags": None})
check("H1 data source 的 computed 全是 unknown（short-circuit 传播）",
      is_unknown(p["id"]), p)
check("H2 config 给的值不受影响", p["name"] == "web", p)
p = planned_data_resource_object(SCHEMA, UNKNOWN)
check("H3 config 也是 unknown 时返回 unknown", is_unknown(p), p)

# ---- 9. optional + computed + nested 的例外分支 ----
ONS = Nested("single", {"sub": Attr(), "subcid": Attr(computed=True)})
OSCHEMA = {"blk": Attr(computed=True, optional=True, nested=ONS)}
# prior 里含非 computed 值 → 推断配置以前非空 → 取 config（null）
p = proposed_new(OSCHEMA, {"blk": {"sub": "v", "subcid": "sc"}}, {"blk": None})
check("I1 prior 含非 computed 值时取 config(null)", p["blk"] is None, p)
# 普通 computed（无 nested）走不到该分支，仍保留 prior
p = proposed_new({"x": Attr(computed=True, optional=True)}, {"x": "prior"}, {"x": None})
check("I2 非嵌套的 optional+computed 仍保留 prior", p["x"] == "prior", p)

# ---- 10. unknown 短路 ----
check("J1 对 unknown 取属性仍是 unknown",
      (lambda: proposed_new(SCHEMA, UNKNOWN, {"id": None, "name": "n", "tags": None})["id"])()
      is UNKNOWN)
check("J2 prior 整体 unknown 时非 computed 仍取 config",
      proposed_new(SCHEMA, UNKNOWN, {"id": None, "name": "n", "tags": None})["name"] == "n")

print("checks=%d fail=%d" % (N, len(FAIL)))
for f in FAIL:
    print("  FAIL", f)
