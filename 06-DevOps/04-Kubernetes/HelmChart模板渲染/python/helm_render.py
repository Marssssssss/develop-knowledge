"""
Helm Chart 模板渲染三件套:值合并 → 模板求值 → 按 Kind 排序。

权威来源(实际读过):
  1. https://helm.sh/docs/chart_template_guide/values_files/
  2. https://helm.sh/docs/chart_template_guide/functions_and_pipelines/
  3. https://cdn.jsdelivr.net/gh/helm/helm@v3.19.0/pkg/releaseutil/kind_sorter.go

从文档抄下来的事实:
  - 值的来源与优先级(原文 "The list above is in order of specificity"):
      chart 的 values.yaml  <  父 chart 的 values.yaml  <  `-f` 用户文件  <  `--set`
  - 删除默认键:把键覆盖成 null,Helm 会从合并结果里移除它
  - 管道:`{{ .Values.x | quote }}` 等价于 `quote .Values.x`,**管道值作为函数的最后一个参数**
    (原文:"the result of the first evaluation is sent as the last argument to the function")
  - `default DEFAULT_VALUE GIVEN_VALUE`:给定值为空时取默认值
  - 运算符(eq/ne/lt/gt/and/or)都是函数

从 kind_sorter.go 抄下来的事实:
  - InstallOrder 是一个 Kind 序列(下表完整照抄 v3.19.0)
  - lessByKind:两边都未知 → 按 kind 字母序;**未知的 kind 永远排在最后**;
    相同 kind 保持原顺序(SliceStable)
"""
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------- InstallOrder
INSTALL_ORDER: List[str] = [
    "PriorityClass", "Namespace", "NetworkPolicy", "ResourceQuota", "LimitRange",
    "PodSecurityPolicy", "PodDisruptionBudget", "ServiceAccount", "Secret",
    "SecretList", "ConfigMap", "StorageClass", "PersistentVolume",
    "PersistentVolumeClaim", "CustomResourceDefinition", "ClusterRole",
    "ClusterRoleList", "ClusterRoleBinding", "ClusterRoleBindingList", "Role",
    "RoleList", "RoleBinding", "RoleBindingList", "Service", "DaemonSet", "Pod",
    "ReplicationController", "ReplicaSet", "Deployment", "HorizontalPodAutoscaler",
    "StatefulSet", "Job", "CronJob", "IngressClass", "Ingress", "APIService",
]


def less_by_kind(kind_a: str, kind_b: str, ordering: Optional[List[str]] = None) -> bool:
    """照抄 lessByKind的语义:未知 kind 最后,双未知按字母序,同 kind 保序(false)。"""
    o = ordering or INSTALL_ORDER
    idx = {k: i for i, k in enumerate(o)}
    aok, bok = kind_a in idx, kind_b in idx
    if not aok and not bok:
        if kind_a != kind_b:
            return kind_a < kind_b
        return False                      # 相同 kind 保持原顺序
    if not aok:
        return False                      # 未知 kind 排最后
    if not bok:
        return True
    return idx[kind_a] < idx[kind_b]


def sort_manifests_by_kind(manifests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """sortManifestsByKind:稳定排序,相同 kind 保持原顺序。"""
    out = list(manifests)
    n = len(out)
    for i in range(1, n):                 # 插入排序 = 稳定
        cur = out[i]
        j = i - 1
        while j >= 0 and less_by_kind(cur["kind"], out[j]["kind"]):
            out[j + 1] = out[j]
            j -= 1
        out[j + 1] = cur
    return out


# ---------------------------------------------------------------- 值合并
def coalesce(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Helm 的 values 合并:override 覆盖 base;override 里值为 None 表示删除该键。"""
    out = dict(base)
    for k, v in override.items():
        if v is None:
            out.pop(k, None)              # 置 null = 删除默认键
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = coalesce(out[k], v)
        else:
            out[k] = v
    return out


def _parse_scalar(raw: str) -> Any:
    if raw == "null":
        return None
    if raw.startswith("[") and raw.endswith("]"):
        return [x for x in raw[1:-1].split(",") if x != ""]
    if raw in ("true", "false"):
        return raw == "true"
    try:
        return int(raw)
    except ValueError:
        return raw


def parse_set(expr: str) -> Dict[str, Any]:
    """解析一条 --set:`a.b=1` / `a.b=null` / `a[0]=x`。"""
    import re

    key, _, raw = expr.partition("=")
    value = _parse_scalar(raw)
    tokens = [(m.group(1), m.group(2))
              for m in re.finditer(r"([^.\[\]]+)|\[(\d+)\]", key)]
    root: Dict[str, Any] = {}

    def build(i: int, container: Any) -> None:
        name, idx = tokens[i]
        last = i == len(tokens) - 1
        if name is not None:
            if last:
                container[name] = value
            else:
                nxt: Any = [] if tokens[i + 1][1] is not None else {}
                container[name] = nxt
                build(i + 1, nxt)
        else:
            k = int(idx)
            while len(container) <= k:
                container.append(None)
            if last:
                container[k] = value
            else:
                if not isinstance(container[k], (dict, list)):
                    container[k] = {} if tokens[i + 1][0] is not None else []
                build(i + 1, container[k])

    build(0, root)
    return root


def build_values(chart_values: Dict[str, Any], parent_values: Optional[Dict] = None,
                 user_files: Optional[List[Dict]] = None,
                 sets: Optional[List[str]] = None) -> Dict[str, Any]:
    """按优先级链合并:chart values < parent < -f ... < --set ..."""
    v = dict(chart_values)
    if parent_values:
        v = coalesce(v, parent_values)
    for f in (user_files or []):
        v = coalesce(v, f)
    for s in (sets or []):
        v = coalesce(v, parse_set(s))
    return v


# ---------------------------------------------------------------- 模板函数
def _empty(v: Any) -> bool:
    return v is None or v == "" or v == 0 or v == [] or v == {}


FUNCS = {
    "quote": lambda v: '"%s"' % v,
    "squote": lambda v: "'%s'" % v,
    "upper": lambda v: str(v).upper(),
    "lower": lambda v: str(v).lower(),
    "trim": lambda v: str(v).strip(),
    "default": lambda d, v: d if _empty(v) else v,
    "repeat": lambda n, v: str(v) * int(n),
    "join": lambda sep, v: sep.join(str(x) for x in v),
}


def _tokenize(s: str) -> List[str]:
    """按空白切分,但保留引号内的空格(join \", \" 这类参数必须整体保留)。"""
    toks: List[str] = []
    cur = ""
    quote: Optional[str] = None
    for ch in s:
        if quote is not None:
            cur += ch
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
            cur += ch
        elif ch in (" ", "\t"):
            if cur:
                toks.append(cur)
                cur = ""
        else:
            cur += ch
    if cur:
        toks.append(cur)
    return toks


def _parse_arg(tok: str) -> Any:
    tok = tok.strip()
    if tok.startswith('"') and tok.endswith('"'):
        return tok[1:-1]
    if tok.startswith("'") and tok.endswith("'"):
        return tok[1:-1]
    try:
        return int(tok)
    except ValueError:
        return None                        # 不支持的路径参数,交由调用方处理


def lookup(path: str, ctx: Dict[str, Any]) -> Any:
    cur: Any = ctx
    for part in path.lstrip(".").split("."):
        if part == "":
            continue
        if isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        else:
            return None
    return cur


def eval_action(body: str, ctx: Dict[str, Any]) -> str:
    body = body.strip()
    if body.startswith("/*"):
        return ""
    parts = [p for p in body.split("|")]
    def resolve(tok: str) -> Any:
        tok = tok.strip()
        if tok[:1] in ('"', "'"):
            return _parse_arg(tok)
        if tok.lstrip("-").isdigit():
            return int(tok)
        return lookup(tok, ctx)

    head = parts[0].strip()
    head_toks = _tokenize(head)
    if head_toks and head_toks[0] in FUNCS:
        # 函数式调用:`quote .Values.x`、`default "tea" .Values.y`
        # 等价于管道写法,参数按书写顺序,管道值补在最后
        name, args = head_toks[0], head_toks[1:]
        val = FUNCS[name](*[resolve(a) for a in args])
    else:
        val = resolve(head)
    for seg in parts[1:]:
        seg = seg.strip()
        toks = _tokenize(seg)
        name, args = toks[0], toks[1:]
        parsed = [_parse_arg(a) for a in args]
        val = FUNCS[name](*parsed, val)    # 管道值作为最后一个参数
    return "" if val is None else str(val)


def render(tmpl: str, ctx: Dict[str, Any]) -> str:
    out: List[str] = []
    i = 0
    while True:
        s = tmpl.find("{{", i)
        if s < 0:
            out.append(tmpl[i:])
            break
        out.append(tmpl[i:s])
        e = tmpl.find("}}", s)
        if e < 0:
            raise ValueError("unclosed action")
        out.append(eval_action(tmpl[s + 2:e], ctx))
        i = e + 2
    return "".join(out)


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
