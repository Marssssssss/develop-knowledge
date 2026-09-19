# -*- coding: utf-8 -*-
"""helm_render 自检。从 helm_render.py 拆出,内容逐字节搬运。"""
from helm_render import *

# ---------------------------------------------------------------- 自检
def selfcheck() -> int:
    n = 0

    def ck(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    # 1. 值优先级:chart values < parent < -f < --set
    chart = {"image": {"repo": "nginx", "tag": "1.0"}, "replicas": 1, "extra": "keep"}
    parent = {"image": {"tag": "1.1"}}
    userfile = {"replicas": 3}
    v = build_values(chart, parent, [userfile], ["image.tag=2.0"])
    ck(v["image"]["repo"] == "nginx", "未覆盖的键应保留 chart 默认值")
    ck(v["image"]["tag"] == "2.0", f"--set 优先级最高, 实得 {v['image']['tag']}")
    ck(v["replicas"] == 3, f"-f 覆盖 chart 默认值, 实得 {v['replicas']}")
    ck(v["extra"] == "keep", "无关键不受影响")
    # 父 chart 覆盖子 chart
    v2 = build_values({"image": {"tag": "1.0"}}, {"image": {"tag": "1.1"}})
    ck(v2["image"]["tag"] == "1.1", "父 chart 的 values 覆盖子 chart")
    # 只有 chart values 时用默认值
    v3 = build_values({"drink": "coffee"})
    ck(v3["drink"] == "coffee", "无覆盖时用 chart 默认值")

    # 2. null 删除默认键(文档里的 livenessProbe.httpGet 例子)
    base = {"livenessProbe": {"httpGet": {"path": "/login", "port": "http"},
                              "initialDelaySeconds": 120}}
    merged = coalesce(base, {"livenessProbe": {"httpGet": None}})
    ck("httpGet" not in merged["livenessProbe"], "置 null 应删除该键")
    ck(merged["livenessProbe"]["initialDelaySeconds"] == 120, "同层其他键应保留")
    merged2 = coalesce(base, {"livenessProbe": {"httpGet": {"path": "/health"}}})
    ck(merged2["livenessProbe"]["httpGet"]["path"] == "/health", "非 null 覆盖应合并")
    ck(merged2["livenessProbe"]["httpGet"]["port"] == "http", "未指定的子键应保留(深合并)")

    # 3. --set 语法
    ck(parse_set("a.b=1") == {"a": {"b": 1}}, "点号路径")
    ck(parse_set("a=null") == {"a": None}, "null 值")
    ck(parse_set("a=true") == {"a": True}, "bool 值")
    ck(parse_set("a=hello") == {"a": "hello"}, "字符串值")
    ck(parse_set("a[0]=x")["a"][0] == "x", "数组下标")
    ck(parse_set("a[1]=y")["a"][1] == "y", "数组下标 1 前面补空位")
    ck(parse_set("a[0].b=1")["a"][0]["b"] == 1, "数组元素里的字段")
    ck(build_values({"replicas": 1}, sets=["replicas=5"])["replicas"] == 5, "--set 覆盖")
    ck("replicas" not in build_values({"replicas": 1}, sets=["replicas=null"]),
       "--set k=null 删除键")

    # 4. 模板函数
    ctx = {"Values": {"favorite": {"drink": "coffee", "food": "pizza",
                                   "drinks": ["coffee", "tea", "water"]},
                      "missing": None},
           "Release": {"Name": "trendsetting-p"}, "Chart": {"Name": "mychart"}}
    ck(eval_action(".Values.favorite.drink | quote", ctx) == '"coffee"', "quote")
    ck(eval_action("quote .Values.favorite.drink", ctx) == '"coffee"', "函数式调用等价")
    ck(eval_action(".Values.favorite.food | upper | quote", ctx) == '"PIZZA"', "管道链式")
    ck(eval_action(".Values.favorite.drink | repeat 5 | quote", ctx) == '"coffeecoffeecoffeecoffeecoffee"',
       "repeat:管道值作为最后一个参数")
    ck(eval_action(".Values.favorite.drinks | join \", \" | quote", ctx) == '"coffee, tea, water"',
       "join")
    ck(eval_action(".Values.missing | default \"tea\" | quote", ctx) == '"tea"',
       "default:值为空时取默认")
    ck(eval_action(".Values.favorite.drink | default \"tea\" | quote", ctx) == '"coffee"',
       "default:值非空时不生效")
    ck(eval_action("default \"tea\" .Values.missing", ctx) == "tea", "default 函数式调用")
    ck(eval_action(".Release.Name", ctx) == "trendsetting-p", "内置对象 Release")
    ck(eval_action(".Chart.Name", ctx) == "mychart", "内置对象 Chart")
    ck(eval_action("\"literal\"", ctx) == "literal", "字符串字面量")

    # 5. 整段渲染
    tmpl = "name: {{ .Release.Name }}-configmap\ndrink: {{ .Values.favorite.drink | quote }}\n"
    out = render(tmpl, ctx)
    ck("name: trendsetting-p-configmap" in out, "整段渲染应替换 Release.Name")
    ck('drink: "coffee"' in out, "整段渲染应替换并 quote")
    ck("{{" not in out, "渲染后不应残留 action")
    ck(render("plain text", ctx) == "plain text", "无 action 的模板原样输出")
    ck(render("a{{/* comment */}}b", ctx) == "ab", "注释 action 输出空")

    # 6. InstallOrder
    ck(INSTALL_ORDER[0] == "PriorityClass", "PriorityClass 排第一")
    ck(INSTALL_ORDER[-1] == "APIService", "APIService 排最后")
    ck(INSTALL_ORDER.index("Namespace") < INSTALL_ORDER.index("ConfigMap"), "Namespace 早于 ConfigMap")
    ck(INSTALL_ORDER.index("Service") < INSTALL_ORDER.index("Deployment"), "Service 早于 Deployment")
    ck(INSTALL_ORDER.index("Secret") < INSTALL_ORDER.index("ConfigMap"), "Secret 早于 ConfigMap")
    ck(INSTALL_ORDER.index("SecretList") < INSTALL_ORDER.index("ConfigMap"),
       "SecretList 也早于 ConfigMap(照抄源码顺序)")
    ck(INSTALL_ORDER.index("Deployment") < INSTALL_ORDER.index("StatefulSet"),
       "Deployment 早于 StatefulSet")
    ck(INSTALL_ORDER.index("CronJob") < INSTALL_ORDER.index("Ingress"), "CronJob 早于 Ingress")

    # 7. lessByKind 的三个分支
    ck(less_by_kind("ConfigMap", "Deployment"), "已知 kind 按 InstallOrder 比较")
    ck(not less_by_kind("Deployment", "ConfigMap"), "反向应为 False")
    ck(not less_by_kind("ConfigMap", "ConfigMap"), "相同 kind 保序")
    ck(less_by_kind("Deployment", "Widget"), "已知 kind 应排在未知 kind 之前")
    ck(not less_by_kind("Widget", "Deployment"), "未知 kind 应排最后")
    ck(less_by_kind("Alpha", "Zeta"), "双未知按字母序")
    ck(not less_by_kind("Zeta", "Alpha"), "双未知字母序反向")

    # 8. 排序:稳定 + 未知最后
    mans = [{"kind": "Deployment", "name": "d1"},
            {"kind": "Widget", "name": "w1"},
            {"kind": "ConfigMap", "name": "c1"},
            {"kind": "Deployment", "name": "d2"},
            {"kind": "Namespace", "name": "n1"},
            {"kind": "Unknown2", "name": "u2"}]
    ordered = sort_manifests_by_kind(mans)
    kinds = [m["kind"] for m in ordered]
    ck(kinds[0] == "Namespace", f"Namespace 应第一, 实得 {kinds}")
    ck(kinds.index("ConfigMap") < kinds.index("Deployment"), "ConfigMap 早于 Deployment")
    ck(kinds[-1] == "Widget", f"未知 kind 应最后, 实得 {kinds}")
    names_d = [m["name"] for m in ordered if m["kind"] == "Deployment"]
    ck(names_d == ["d1", "d2"], f"同 kind 应保持原顺序, 实得 {names_d}")
    ck(len(ordered) == len(mans), "排序不应丢元素")

    print(f"helm_render: {n} assertions passed")
    return n




if __name__ == "__main__":
    selfcheck()
