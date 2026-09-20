# -*- coding: utf-8 -*-
"""Terraform lifecycle 元参数与依赖图变换模型。

口径全部来自实际读过的官方原文（见 README 参考资料）：
  - lifecycle reference：create_before_destroy 传播到依赖、prevent_destroy、ignore_changes、replace_triggered_by、precondition
  - 默认替换顺序是「先销毁再新建」，create_before_destroy 才反过来
"""
from __future__ import annotations

MANAGED_PREFIXES = ("data.", "var.", "local.", "each.", "count.")


class Resource:
    def __init__(self, name, attrs=None, requires_replace=(), lifecycle=None,
                 instances=None):
        self.name = name
        self.attrs = dict(attrs or {})
        self.requires_replace = set(requires_replace)
        self.lifecycle = dict(lifecycle or {})
        # instances: None 表示单实例；否则为实例键列表
        self.instances = instances

    @property
    def explicit_cbd(self):
        """配置里显式写的值，None 表示没写（官方：可被依赖方隐式置位）。"""
        return self.lifecycle.get("create_before_destroy")

    @property
    def prevent_destroy(self):
        return bool(self.lifecycle.get("prevent_destroy", False))

    @property
    def ignore_changes(self):
        return self.lifecycle.get("ignore_changes", [])


def _is_managed_ref(ref: str) -> bool:
    return not ref.startswith(MANAGED_PREFIXES)


def propagate_cbd(resources: dict, deps: dict):
    """create_before_destroy 会沿依赖边向**被依赖方**传播。

    官方：CBD 开在 A 上而 A 依赖 B，则 Terraform 隐式给 B 也开上并写进 state；
    此时 B 再显式写 false 会被拒绝，因为「那会隐含依赖图中的环」。
    返回 (effective_cbd, conflicts)
    """
    eff = {n: bool(r.explicit_cbd) for n, r in resources.items()}
    # 反复传播直到不动点：A 是 CBD ⇒ A 的所有（传递）依赖都是 CBD
    changed = True
    while changed:
        changed = False
        for n in eff:
            if not eff[n]:
                continue
            for d in deps.get(n, ()):
                if d in eff and not eff[d]:
                    eff[d] = True
                    changed = True
    conflicts = [n for n, r in resources.items()
                 if r.explicit_cbd is False and eff.get(n) is True]
    return eff, sorted(conflicts)


def _ignored(res: Resource, attr: str) -> bool:
    ic = res.ignore_changes
    return ic == "all" or attr in ic


def _diff(state_obj: dict, res: Resource):
    """返回 (update_diffs, replace_diffs)；ignore_changes 只作用于 update。"""
    upd, rep = {}, {}
    for k, new in res.attrs.items():
        old = state_obj.get(k)
        if old == new:
            continue
        if _ignored(res, k):
            continue
        (rep if k in res.requires_replace else upd)[k] = (old, new)
    return upd, rep


def _update_or_replace(act: str) -> bool:
    """官方列举的触发条件只有 update 与 replace。"""
    return act in ("update", "replace")


def plan(state: dict, config: dict, deps: dict):
    """返回 (actions, errors)。actions: name -> action；另有 order 说明替换次序。"""
    errors = []
    eff_cbd, conflicts = propagate_cbd(config, deps)
    for n in conflicts:
        errors.append("%s: 依赖方开启了 create_before_destroy，此处不能显式写 false"
                      "（会在依赖图中成环）" % n)

    # 1) 先算各资源的基础动作
    base = {}
    for name, res in config.items():
        for pre in res.lifecycle.get("precondition", []):
            if not pre["condition"]:
                errors.append("%s: precondition 失败: %s" % (name, pre.get("error_message", "")))
        if name in state:
            upd, rep = _diff(state[name], res)
            if rep:
                base[name] = "replace"
            elif upd:
                base[name] = "update"
            else:
                base[name] = "noop"
        else:
            base[name] = "create"

    # 2) replace_triggered_by：引用目标的计划动作会触发本资源替换
    triggers = {}
    for name, res in config.items():
        for ref in res.lifecycle.get("replace_triggered_by", ()):
            if not _is_managed_ref(ref):
                errors.append("%s: replace_triggered_by 只能引用托管资源: %s" % (name, ref))
                continue
            # 资源名本身可含点，故先整体匹配，再退化为「末段是属性名」
            if ref in config:
                target, attr = ref, None
            elif "." in ref:
                target, attr = ref.rsplit(".", 1)
            else:
                target, attr = ref, None
            if target not in config:
                errors.append("%s: replace_triggered_by 引用的资源不存在: %s" % (name, ref))
                continue
            if attr is None:
                fired = _update_or_replace(base.get(target, "noop"))
            else:
                fired = state.get(target, {}).get(attr) != config[target].attrs.get(attr)
            if fired:
                triggers.setdefault(name, []).append(ref)

    for name in triggers:
        if base.get(name) in (None, "noop", "update"):
            base[name] = "replace"

    # 3) prevent_destroy：阻止「仍在配置中」的销毁/替换；配置被整体移除时不阻止
    for name, res in config.items():
        if res.prevent_destroy and base.get(name) in ("replace", "destroy"):
            errors.append("%s: prevent_destroy=true，拒绝会销毁该对象的计划" % name)

    actions = dict(base)
    for name in state:
        if name not in config:
            actions[name] = "destroy"

    # 4) 替换次序：默认先销毁后新建，CBD 反过来
    order = {}
    for name, act in actions.items():
        if act == "replace":
            order[name] = ("create", "destroy") if eff_cbd.get(name) else ("destroy", "create")
    return actions, errors, eff_cbd, order, triggers


def destroy_graph(deps: dict, actions: dict, eff_cbd: dict):
    """销毁节点之间的先后约束：X 依赖 Y ⇒ 默认先销毁 X 再销毁 Y。

    X 开了 create_before_destroy 时，X 的旧对象要等新对象建好才销毁，
    而新对象又依赖 Y 的新对象，故约束翻转为「先销毁 Y 再销毁 X」。
    """
    edges = []
    for x, ys in deps.items():
        if actions.get(x) not in ("destroy", "replace"):
            continue
        for y in ys:
            if actions.get(y) not in ("destroy", "replace"):
                continue
            edges.append((x, y) if not eff_cbd.get(x) else (y, x))
    return edges


def has_cycle(edges, nodes):
    adj = {n: [] for n in nodes}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
    color = {}

    def dfs(u):
        color[u] = 1
        for v in adj.get(u, ()):
            if color.get(v, 0) == 1:
                return True
            if color.get(v, 0) == 0 and dfs(v):
                return True
        color[u] = 2
        return False

    return any(color.get(n, 0) == 0 and dfs(n) for n in list(adj))
