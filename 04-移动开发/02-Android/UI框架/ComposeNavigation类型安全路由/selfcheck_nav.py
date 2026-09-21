"""类型安全导航模型自检：全部断言基于 androidx.navigation 官方源码语义。"""

from main import (
    Field, Serializer, NavType, NavDestination, NavGraph,
    RouteBuilder, RouteEncoder, RouteDecoder,
    build_pattern, navigate_to_graph,
    IllegalArg, IllegalState, PATH, QUERY,
)

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, label
    PASS += 1


def raises(exc, fn, label):
    global PASS
    try:
        fn()
    except exc:
        PASS += 1
        return
    except Exception as e:  # noqa: BLE001
        raise AssertionError("%s: 期望 %s，实得 %s" % (label, exc.__name__, type(e).__name__))
    raise AssertionError("%s: 期望抛 %s，但没有抛" % (label, exc.__name__))


INT = NavType("int")
STR = NavType("string")
LIST_INT = NavType("int", is_collection=True)
NULLABLE_STR = NavType("string", nullable=True)

# ---- 1. computeParamType：CollectionNavType / optional → QUERY，否则 PATH ----
s = Serializer("com.example.Profile", [Field("id"), Field("q", optional=True), Field("tags")])
tm = {"id": INT, "q": STR, "tags": LIST_INT}
b = RouteBuilder(s)
ok(b.compute_param_type(0, INT) == PATH, "普通标量字段走 PATH")
ok(b.compute_param_type(1, STR) == QUERY, "optional 字段走 QUERY")
ok(b.compute_param_type(2, LIST_INT) == QUERY, "CollectionNavType 走 QUERY")
ok(b.compute_param_type(2, INT) == PATH, "同一位置换成标量 NavType 就回到 PATH（判定只看 NavType+optional）")

# ---- 2. 模式串：path 用 /{name}，query 首个 ? 后续 & ----
ok(build_pattern(s, tm) == "com.example.Profile/{id}?q={q}&tags={tags}",
   "path 占位符 + query 串接（首个 ? 后续 &）")

s_only = Serializer("com.example.Home", [])
ok(build_pattern(s_only, {}) == "com.example.Home", "无参数时 route 就是 serialName")

raises(IllegalState, lambda: build_pattern(s, {"id": INT, "q": STR}),
       "缺少某个字段的 NavType → IllegalState(Cannot find NavType)")

# ---- 3. RouteBuilder.append_arg：PATH 只接受单值 ----
b2 = RouteBuilder(s)
b2.append_arg(0, "id", INT, ["42"])
ok(b2.build() == "com.example.Profile/42", "PATH 参数单值拼接")
raises(IllegalArg, lambda: b2.append_arg(0, "id", INT, ["1", "2"]),
       "PATH 参数多值 → IllegalArg(Expected one value)")
b3 = RouteBuilder(s)
b3.append_arg(1, "q", STR, ["a", "b"])
ok(b3.build() == "com.example.Profile?q=a&q=b", "QUERY 多值重复同一 key")

# ---- 4. RouteEncoder：值永远是 List<String>，集合类型展开成多值 ----
enc = RouteEncoder(s, tm)
amap = enc.encode_to_arg_map({"id": 7, "q": "hi", "tags": [1, 2, 3]})
ok(amap == {"id": ["7"], "q": ["hi"], "tags": ["1", "2", "3"]},
   "标量包成单元素列表、集合展开成多值")
ok(all(isinstance(v, list) for v in amap.values()), "argMap 的值类型恒为 List<String>")
ok(all(isinstance(x, str) for v in amap.values() for x in v), "argMap 的元素恒为字符串")
raises(IllegalState,
       lambda: RouteEncoder(s, {"id": INT}).encode_to_arg_map({"id": 1, "q": "x"}),
       "Encoder 缺 NavType → IllegalState")
ok(RouteEncoder(s, tm).encode_to_arg_map({"id": 1}) == {"id": ["1"]},
   "未提供的字段不进 argMap（encoder 只写出现过的元素）")

# ---- 5. RouteDecoder：跳过缺失元素；非可空取到 null → IllegalState ----
dec = RouteDecoder({"id": ["9"], "tags": ["1", "2"]}, tm, s)
ok(dec.decode() == {"id": 9, "tags": [1, 2]}, "缺失的可选参数被跳过而不是填 null")
ok(RouteDecoder({}, tm, s).decode() == {}, "空 store 解码出空字典（全部跳过）")
raises(IllegalState,
       lambda: RouteDecoder({"id": [None]}, {"id": NULLABLE_STR}, Serializer("x", [Field("id")])).decode(),
       "非可空参数取到 null → IllegalState(Unexpected null value)")
dec2 = RouteDecoder({"id": ["3"]}, {"id": NULLABLE_STR}, Serializer("x", [Field("id", nullable=True)]))
ok(dec2.decode() == {"id": "3"}, "可空参数正常解码")
raises(IllegalState, lambda: RouteDecoder({"id": ["1"]}, {}, Serializer("x", [Field("id")])).decode(),
       "store 里有值但 typeMap 查不到 → IllegalState(Failed to find type)")

# ---- 6. 嵌套图：startDestination 必须是直接子节点 ----
child = NavDestination("com.example.Profile/{id}", required={"id"})
grand = NavDestination("com.example.Detail/{id}", required={"id"})
sub = NavGraph("com.example.Sub", start_destination_route="com.example.Detail/{id}")
sub.add_destination(grand)
root = NavGraph("com.example.Root", start_destination_route="com.example.Sub")
root.add_destination(sub)
root.add_destination(child)
ok(sub.find_node("com.example.Detail/{id}", search_parents=False) is grand, "直接子节点可查到")
ok(sub.find_node("com.example.Profile/{id}", search_parents=True) is child,
   "searchParents=True 时向上找到父图的节点")

bad = NavGraph("com.example.Bad", start_destination_route="com.example.Other")
root.add_destination(bad)
raises(IllegalArg, lambda: navigate_to_graph(bad, back_stack=[]),
       "startDestination 不在直接子节点 → IllegalArg(not a direct child)")

raises(IllegalState,
       lambda: navigate_to_graph(NavGraph("com.example.NoStart"), back_stack=[]),
       "未设 startDestination → IllegalState(no start destination defined)")

# ---- 7. 导航到嵌套图：压入的是 startDestination，图自身不入栈 ----
stack = []
node, args = navigate_to_graph(sub, args={"id": 5}, back_stack=stack)
ok(stack == [grand], "导航到 NavGraph 只把 startDestination 压栈")
ok(node is grand, "返回实际落地的 destination")
ok(args["id"] == 5, "显式传入的 startDestinationArgs 生效")

# ---- 8. startRoute 带占位符时的参数合并：已有 args 优先 ----
sub2 = NavGraph("com.example.Sub2", start_destination_route="com.example.Detail/{id}")
d2 = NavDestination("com.example.Detail/{id}", required={"id"})
sub2.add_destination(d2)
_, a2 = navigate_to_graph(sub2, args={"id": 11})
ok(a2["id"] == 11, "startRoute 里的占位符参数不被解析值覆盖（args 后写入）")

sub3 = NavGraph("com.example.Sub3", start_destination_route="com.example.Detail/77")
d3 = NavDestination("com.example.Detail/{id}", required={"id"})
d3 = NavDestination("com.example.Detail/{id}", required={"id"}, arg_types={"id": INT})
sub3.add_destination(d3)
_, a3 = navigate_to_graph(sub3)
ok(a3["id"] == 77, "startRoute 含字面值 ⇒ 解析后填入参数")

# ---- 9. 缺失必填参数 ----
sub4 = NavGraph("com.example.Sub4", start_destination_route="com.example.Detail/{id}")
sub4.add_destination(NavDestination("com.example.Detail/{id}", required={"id"}))
raises(IllegalArg, lambda: navigate_to_graph(sub4, args={}),
       "必填参数缺失 → IllegalArg(Missing required arguments)")

# ---- 10. 默认值补全 ----
sub5 = NavGraph("com.example.Sub5", start_destination_route="com.example.Home")
sub5.add_destination(NavDestination("com.example.Home", required={"page"}, arguments={"page": 0}))
_, a5 = navigate_to_graph(sub5)
ok(a5["page"] == 0, "默认值在 addInDefaultArgs 阶段补上，不算 missing")

print("PASS %d" % PASS)
