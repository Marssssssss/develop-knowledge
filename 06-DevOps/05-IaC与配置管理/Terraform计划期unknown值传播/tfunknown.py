# -*- coding: utf-8 -*-
"""Terraform 计划期 unknown 值传播：objchange.ProposedNew 的合并语义。

口径来自实际读过的 Terraform v1.9.8 源码 internal/plans/objchange/objchange.go
与官方《Types and Values》文档（见 README 参考资料）。
"""
from __future__ import annotations


class _Unknown:
    def __repr__(self):
        return "UNKNOWN"

    def __bool__(self):
        return False


UNKNOWN = _Unknown()


def is_unknown(v):
    return v is UNKNOWN


def is_null(v):
    return v is None


def _walk_short(v):
    """cty 的 unknown 短路：对 unknown 做任何操作都还是 unknown。"""
    return UNKNOWN if is_unknown(v) else v


class Attr:
    def __init__(self, computed=False, optional=False, nested=None):
        self.computed = computed
        self.optional = optional
        self.nested = nested  # Nested 或 None


class Nested:
    def __init__(self, nesting, schema):
        assert nesting in ("single", "list", "map", "set")
        self.nesting = nesting
        self.schema = schema  # dict[str, Attr]


def empty_value(schema):
    """schema.EmptyValue()：所有属性均为 null。"""
    return {name: None for name in schema}


def _get(value, name):
    """prior.GetAttr(name)；prior 整体 unknown 时按 cty 短路返回 unknown。"""
    if is_unknown(value):
        return UNKNOWN
    if is_null(value):
        return None
    return value.get(name)


def _has_index(value, idx):
    if is_unknown(value) or is_null(value):
        return False
    return 0 <= idx < len(value)


def optional_value_not_computable(attr: Attr, prior):
    """源码 optionalValueNotComputable：Optional + 有 NestedType，且 prior 里能走到
    一个**非 computed** 的属性（非 null）→ True。此时推断配置以前非空，故取 config。
    """
    if not attr.optional or attr.nested is None:
        return False
    if is_null(prior) or is_unknown(prior):
        return False
    return any(_contains_non_computed(attr.nested.schema, prior))


def _contains_non_computed(schema, value):
    stack = [(schema, value)]
    while stack:
        sch, val = stack.pop()
        if is_null(val) or is_unknown(val):
            continue
        if not isinstance(val, dict):
            continue
        for name, spec in sch.items():
            v = val.get(name)
            if is_null(v) or is_unknown(v):
                continue
            if spec.nested is not None:
                stack.append((spec.nested.schema, v))
            elif not spec.computed:
                return [True]
    return []


def proposed_new(schema, prior, config):
    """ProposedNew：把 prior 的 computed 值与 config 的托管值合成 proposed new state。"""
    if is_null(config) and is_null(prior):
        return prior
    if is_null(prior):
        prior = empty_value(schema)
    if is_null(config) or is_unknown(config):
        return prior
    out = {}
    for name, spec in schema.items():
        out[name] = _proposed_new_attr(spec, _get(prior, name), _get(config, name))
    return out


def _proposed_new_attr(spec: Attr, prior_v, config_v):
    # required 在构造计划时不参与判定：属性实质上只有 computed / 非 computed 两类
    if spec.computed and is_null(config_v):
        if optional_value_not_computable(spec, prior_v):
            return config_v
        return prior_v
    if spec.nested is not None:
        return _proposed_new_nested(spec.nested, prior_v, config_v)
    return config_v


def _proposed_new_nested(ns: Nested, prior_v, config_v):
    # 整体 unknown 只会出现在 dynamic + unknown for_each 的情况
    if is_unknown(config_v):
        return config_v
    if ns.nesting == "single":
        if is_null(config_v):
            return config_v
        return proposed_new(ns.schema, prior_v, config_v)
    if ns.nesting == "list":
        return _proposed_new_list(ns, prior_v, config_v)
    if ns.nesting == "map":
        return _proposed_new_map(ns, prior_v, config_v)
    return _proposed_new_set(ns, prior_v, config_v)


def _proposed_new_list(ns, prior_v, config_v):
    """Nested blocks are correlated by index."""
    if is_null(config_v) or is_unknown(config_v) or len(config_v) == 0:
        return config_v
    out = []
    for idx, cfg_ev in enumerate(config_v):
        if not _has_index(prior_v, idx):
            out.append(cfg_ev)          # 没有对应的 prior 元素 → 原样取 config
            continue
        out.append(proposed_new(ns.schema, prior_v[idx], cfg_ev))
    return out


def _proposed_new_map(ns, prior_v, config_v):
    if is_null(config_v) or is_unknown(config_v) or len(config_v) == 0:
        return config_v
    prior_map = {} if is_null(prior_v) or is_unknown(prior_v) else dict(prior_v)
    out = {}
    for key, cfg_ev in config_v.items():
        if key not in prior_map:
            out[key] = cfg_ev
            continue
        out[key] = proposed_new(ns.schema, prior_map[key], cfg_ev)
    return out


def _non_computed_sig(schema, value):
    """set 关联用的签名：只比非 computed 属性（源码：matching non-computed attribute values）。"""
    if not isinstance(value, dict):
        return None
    return tuple(sorted((n, repr(value.get(n))) for n, s in schema.items()
                        if not s.computed))


def _proposed_new_set(ns, prior_v, config_v):
    """set 关联是**启发式**：按非 computed 属性值匹配，取到的第一个未用过的 prior 元素。"""
    if is_null(config_v) or is_unknown(config_v) or len(config_v) == 0:
        return config_v
    priors = [] if is_null(prior_v) or is_unknown(prior_v) else list(prior_v)
    used = [False] * len(priors)
    out = []
    for cfg_ev in config_v:
        sig = _non_computed_sig(ns.schema, cfg_ev)
        hit = -1
        for i, p in enumerate(priors):
            if used[i]:
                continue
            if _non_computed_sig(ns.schema, p) == sig:
                hit = i
                break
        if hit < 0:
            out.append(cfg_ev)
        else:
            used[hit] = True
            out.append(proposed_new(ns.schema, priors[hit], cfg_ev))
    return out


def planned_data_resource_object(schema, config):
    """PlannedDataResourceObject：用一个**整体 unknown** 的 prior 跑同一套逻辑。

    源码注释："Because of cty's unknown short-circuit behavior, any operation on
    prior returns another unknown, and so unknown values propagate into all of the
    parts of the resulting value that would normally be filled in by preserving
    the prior state."
    """
    return proposed_new(schema, UNKNOWN, config)


def provider_fill_unknown(schema, proposed):
    """provider 的 PlanResourceChange「按需补 unknown」后的结果（源码注释口径）。

    computed 属性在 ProposedNew 之后仍是 null 时，由 provider 换成 unknown，
    也就是控制台上看到的 "known after apply"。
    """
    if is_unknown(proposed) or is_null(proposed) or not isinstance(proposed, dict):
        return proposed
    out = {}
    for name, spec in schema.items():
        v = proposed.get(name)
        if spec.computed and is_null(v):
            out[name] = UNKNOWN
        else:
            out[name] = v
    return out
