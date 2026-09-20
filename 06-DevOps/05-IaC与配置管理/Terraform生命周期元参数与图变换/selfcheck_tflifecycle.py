# -*- coding: utf-8 -*-
"""tflifecycle 自检：全部基于实际读过的官方原文口径。"""
from tflifecycle import (Resource, propagate_cbd, plan, destroy_graph, has_cycle,
                         _update_or_replace)

N = 0
FAIL = []


def check(label, cond, detail=""):
    global N
    N += 1
    if not cond:
        FAIL.append("%s  %s" % (label, detail))


def run(state, config, deps):
    a, e, cbd, order, trig = plan(state, config, deps)
    return a, e, cbd, order, trig


# ---- 1. 基础动作：无 lifecycle ----
cfg = {"aws_instance.web": Resource("aws_instance.web", {"ami": "ami-2"})}
a, e, cbd, order, _ = run({"aws_instance.web": {"ami": "ami-1"}}, cfg, {})
check("A1 普通属性变化是 update", a == {"aws_instance.web": "update"} and not e, a)
check("A2 默认非 CBD", cbd["aws_instance.web"] is False, cbd)

# 需要替换的属性
cfg = {"aws_instance.web": Resource("aws_instance.web", {"ami": "ami-2"},
                                    requires_replace=["ami"])}
a, e, cbd, order, _ = run({"aws_instance.web": {"ami": "ami-1"}}, cfg, {})
check("A3 不可原地改的属性→replace", a == {"aws_instance.web": "replace"}, a)
check("A4 默认替换次序是先销毁后新建",
      order["aws_instance.web"] == ("destroy", "create"), order)

# ---- 2. create_before_destroy ----
cfg = {"aws_instance.web": Resource("aws_instance.web", {"ami": "ami-2"},
                                    requires_replace=["ami"],
                                    lifecycle={"create_before_destroy": True})}
a, e, cbd, order, _ = run({"aws_instance.web": {"ami": "ami-1"}}, cfg, {})
check("B1 CBD 显式置位", cbd["aws_instance.web"] is True, cbd)
check("B2 CBD 替换次序反过来",
      order["aws_instance.web"] == ("create", "destroy"), order)

# ---- 3. CBD 沿依赖边传播给被依赖方 ----
cfg = {
    "aws_instance.web": Resource("aws_instance.web", {"ami": "ami-2"},
                                 requires_replace=["ami"],
                                 lifecycle={"create_before_destroy": True}),
    "aws_eip.ip": Resource("aws_eip.ip", {}),
}
deps = {"aws_instance.web": ["aws_eip.ip"]}
eff, conflicts = propagate_cbd(cfg, deps)
check("C1 依赖方被隐式置位", eff["aws_eip.ip"] is True, eff)
check("C2 未显式写 false 时无冲突", conflicts == [], conflicts)

# 传递性：A → B → C
cfg3 = {
    "a": Resource("a", {}, lifecycle={"create_before_destroy": True}),
    "b": Resource("b", {}),
    "c": Resource("c", {}),
}
eff, _ = propagate_cbd(cfg3, {"a": ["b"], "b": ["c"]})
check("C3 传播是传递的", eff["b"] and eff["c"], eff)
eff, _ = propagate_cbd(cfg3, {"b": ["a"]})  # 反向：b 依赖 a
check("C4 不会反向传播给依赖方", eff["a"] is True and eff["b"] is False, eff)

# 显式 false 与隐式 true 冲突（官方：会隐含依赖图中的环）
cfg4 = {
    "a": Resource("a", {}, lifecycle={"create_before_destroy": True}),
    "b": Resource("b", {}, lifecycle={"create_before_destroy": False}),
}
eff, conflicts = propagate_cbd(cfg4, {"a": ["b"]})
check("C5 显式 false 撞上隐式 true 记为冲突", conflicts == ["b"], conflicts)
a, e, cbd, order, _ = run({}, cfg4, {"a": ["b"]})
check("C6 冲突会进 errors", any("成环" in x for x in e), e)

# ---- 4. prevent_destroy ----
cfg = {"aws_db_instance.db": Resource("aws_db_instance.db", {"size": "large"},
                                      requires_replace=["size"],
                                      lifecycle={"prevent_destroy": True})}
a, e, cbd, order, _ = run({"aws_db_instance.db": {"size": "small"}}, cfg, {})
check("D1 prevent_destroy 拒绝替换", "aws_db_instance.db" in a and any("prevent_destroy" in x for x in e), e)
a, e, cbd, order, _ = run({"aws_db_instance.db": {"size": "small"}},
                          {"aws_db_instance.db": Resource("aws_db_instance.db",
                                                          {"size": "small"},
                                                          lifecycle={"prevent_destroy": True})}, {})
check("D2 无变化时放行", a == {"aws_db_instance.db": "noop"} and not e, (a, e))
# 官方：配置整体移除时不阻止
a, e, cbd, order, _ = run({"aws_db_instance.db": {"size": "small"}}, {}, {})
check("D3 配置移除后照常 destroy", a == {"aws_db_instance.db": "destroy"}, a)
cfg = {"aws_db_instance.db": Resource("aws_db_instance.db", {"size": "large"},
                                      requires_replace=["size"],
                                      lifecycle={"prevent_destroy": True})}
a, e, cbd, order, _ = run({}, cfg, {})
check("D4 仅创建时不触发 prevent_destroy", a == {"aws_db_instance.db": "create"} and not e, (a, e))

# ---- 5. ignore_changes ----
cfg = {"aws_instance.web": Resource("aws_instance.web", {"tags": "b"},
                                    lifecycle={"ignore_changes": ["tags"]})}
a, e, cbd, order, _ = run({"aws_instance.web": {"tags": "a"}}, cfg, {})
check("E1 被忽略的属性不产生 update", a == {"aws_instance.web": "noop"}, a)
cfg = {"aws_instance.web": Resource("aws_instance.web", {"tags": "b", "ami": "x"},
                                    lifecycle={"ignore_changes": ["tags"]})}
a, e, cbd, order, _ = run({"aws_instance.web": {"tags": "a", "ami": "y"}}, cfg, {})
check("E2 只忽略列出的属性", a == {"aws_instance.web": "update"}, a)
cfg = {"aws_instance.web": Resource("aws_instance.web", {"tags": "b"},
                                    lifecycle={"ignore_changes": "all"})}
a, e, cbd, order, _ = run({"aws_instance.web": {"tags": "a"}}, cfg, {})
check("E3 all 时永不 update", a == {"aws_instance.web": "noop"}, a)
# 官方：create 时 ignore_changes 不起作用
a, e, cbd, order, _ = run({}, cfg, {})
check("E4 ignore_changes 不影响 create", a == {"aws_instance.web": "create"}, a)
# 官方只写明「create 考虑、update 忽略」，未规定「需要替换」这一分支。
# 本模型口径：被忽略的属性不进入 diff，故既不 update 也不 replace（README 已标注口径）。
cfg = {"aws_instance.web": Resource("aws_instance.web", {"ami": "b"},
                                    requires_replace=["ami"],
                                    lifecycle={"ignore_changes": ["ami"]})}
a, e, cbd, order, _ = run({"aws_instance.web": {"ami": "a"}}, cfg, {})
check("E5 模型口径：忽略项不进 diff 故不触发 replace",
      a == {"aws_instance.web": "noop"}, a)

# ---- 6. replace_triggered_by ----
cfg = {
    "aws_ecs_service.svc": Resource("aws_ecs_service.svc", {"task": "2"}),
    "aws_appautoscaling_target.t": Resource("aws_appautoscaling_target.t", {},
                                            lifecycle={"replace_triggered_by":
                                                       ["aws_ecs_service.svc"]}),
}
st = {"aws_ecs_service.svc": {"task": "1"}, "aws_appautoscaling_target.t": {}}
a, e, cbd, order, trig = run(st, cfg, {})
check("F1 引用目标有 update 即触发替换",
      a["aws_appautoscaling_target.t"] == "replace", a)
check("F2 触发原因被记录", trig["aws_appautoscaling_target.t"] == ["aws_ecs_service.svc"], trig)

st2 = {"aws_ecs_service.svc": {"task": "2"}, "aws_appautoscaling_target.t": {}}
a, e, cbd, order, trig = run(st2, cfg, {})
check("F3 目标无变化则不触发", a["aws_appautoscaling_target.t"] == "noop", a)

# 属性级引用
cfg2 = {
    "aws_ecs_service.svc": Resource("aws_ecs_service.svc", {"task": "2"}),
    "t": Resource("t", {}, lifecycle={"replace_triggered_by": ["aws_ecs_service.svc.task"]}),
}
st3 = {"aws_ecs_service.svc": {"task": "1"}, "t": {}}
st4 = {"aws_ecs_service.svc": {"task": "2"}, "t": {}}
a, e, cbd, order, _ = run(st3, cfg2, {})
check("F4 属性级引用按值变否判定", a["t"] == "replace", a)
a, e, cbd, order, _ = run(st4, cfg2, {})
check("F5 属性值未变不触发", a["t"] == "noop", a)

# 官方：只能引用托管资源
cfg3 = {"t": Resource("t", {}, lifecycle={"replace_triggered_by": ["local.x"]})}
a, e, cbd, order, _ = run({"t": {}}, cfg3, {})
check("F6 不能引用 local 值", any("只能引用托管资源" in x for x in e), e)
check("F7 官方触发条件只有 update/replace",
      _update_or_replace("update") and _update_or_replace("replace")
      and not _update_or_replace("create") and not _update_or_replace("noop"))

# ---- 7. precondition 在配置参数求值之前 ----
cfg = {"t": Resource("t", {"a": 1}, lifecycle={"precondition": [
    {"condition": False, "error_message": "nope"}]})}
a, e, cbd, order, _ = run({}, cfg, {})
check("G1 precondition 失败即报错", any("precondition" in x for x in e), e)

# ---- 8. 销毁图与 CBD 翻转 ----
acts = {"a": "replace", "b": "replace"}
edges = destroy_graph({"a": ["b"]}, acts, {"a": False, "b": False})
check("H1 默认先销毁依赖方再销毁被依赖方", edges == [("a", "b")], edges)
edges = destroy_graph({"a": ["b"]}, acts, {"a": True, "b": True})
check("H2 CBD 时销毁边翻转", edges == [("b", "a")], edges)
check("H3 翻转后与另一条边成环",
      has_cycle([("b", "a"), ("a", "b")], ["a", "b"]))
check("H4 无环时不误报", not has_cycle([("a", "b"), ("b", "c")], ["a", "b", "c"]))
edges = destroy_graph({"a": ["b"]}, {"a": "replace", "b": "noop"}, {"a": True, "b": True})
check("H5 对方不销毁则不建边", edges == [], edges)

print("checks=%d fail=%d" % (N, len(FAIL)))
for f in FAIL:
    print("  FAIL", f)
