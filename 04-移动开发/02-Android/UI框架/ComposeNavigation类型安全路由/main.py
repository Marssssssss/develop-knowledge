"""类型安全导航（Navigation 2.8+）的可执行模型。

对应 androidx.navigation 官方源码的三段机制：
  1. `serialization/RouteBuilder.kt`  —— 由 KSerializer 描述子生成 route 模式串
  2. `serialization/RouteEncoder.kt`  —— 把对象编成 Map<String, List<String>>
  3. `serialization/RouteDecoder.kt`  —— 把 SavedState/SavedStateHandle 还原成对象
  4. `NavGraphNavigator.kt` / `NavGraph.kt` —— 嵌套图导航与 startDestination 语义

只保留可判定的语义，去掉 Compose 渲染与协程调度。
"""

PATH = "PATH"
QUERY = "QUERY"


class NavError(Exception):
    """官方抛的是 IllegalArgumentException / IllegalStateException 两种，这里统一基类。"""


class IllegalArg(NavError):
    pass


class IllegalState(NavError):
    pass


class NavType:
    """对应 androidx.navigation.NavType / CollectionNavType。"""

    def __init__(self, name, is_collection=False, nullable=False, single_style=True):
        self.name = name
        self.is_collection = is_collection
        self.nullable = nullable
        self.single_style = single_style

    def serialize_as_value(self, value):
        if value is None:
            return "null"
        if self.name == "int":
            return str(int(value))
        if self.name == "bool":
            return "true" if value else "false"
        return str(value)

    def serialize_as_values(self, value):
        return [self.serialize_as_value(v) for v in (value or [])]

    def parse(self, raw):
        if raw is None:
            return None
        if self.name == "int":
            return int(raw)
        if self.name == "bool":
            return raw == "true"
        return raw

    def parse_many(self, raws):
        return [self.parse(r) for r in raws]


class Field:
    """SerialDescriptor 的一个元素。"""

    def __init__(self, name, optional=False, has_default=False, default=None, nullable=False):
        self.name = name
        self.optional = optional
        self.has_default = has_default
        self.default = default
        self.nullable = nullable


class RouteBuilder:
    """等价 androidx.navigation.serialization.RouteBuilder。"""

    def __init__(self, serializer):
        self.serializer = serializer
        self.path_args = ""
        self.query_args = ""

    @property
    def path(self):
        return self.serializer.serial_name

    def build(self):
        return self.path + self.path_args + self.query_args

    def _add_path(self, text):
        self.path_args += "/" + text

    def _add_query(self, name, value):
        self.query_args += ("?" if not self.query_args else "&") + "%s=%s" % (name, value)

    def compute_param_type(self, index, nav_type):
        """CollectionNavType 或可选元素走 QUERY，其余走 PATH（源码唯一判据）。"""
        field = self.serializer.fields[index]
        if nav_type.is_collection or field.optional:
            return QUERY
        return PATH

    def append_pattern(self, index, name, nav_type):
        if self.compute_param_type(index, nav_type) == PATH:
            self._add_path("{%s}" % name)
        else:
            self._add_query(name, "{%s}" % name)

    def append_arg(self, index, name, nav_type, values):
        if self.compute_param_type(index, nav_type) == PATH:
            if len(values) != 1:
                raise IllegalArg(
                    "Expected one value for argument %s, found %d values instead." % (name, len(values))
                )
            self._add_path(values[0])
        else:
            for v in values:
                self._add_query(name, v)


def build_pattern(serializer, type_map):
    """生成 route 模式串：既有占位符形态，也是编译期登记到 NavGraph 的 route。"""
    b = RouteBuilder(serializer)
    for i, f in enumerate(serializer.fields):
        nt = type_map.get(f.name)
        if nt is None:
            raise IllegalState(
                "Cannot find NavType for argument %s. Please provide NavType through typeMap." % f.name
            )
        b.append_pattern(i, f.name, nt)
    return b.build()


class RouteEncoder:
    """等价 RouteEncoder：产出 Map<String, List<String>>，值永远是字符串列表。"""

    def __init__(self, serializer, type_map):
        self.serializer = serializer
        self.type_map = type_map
        self.map = {}

    def encode_to_arg_map(self, value):
        for i, f in enumerate(self.serializer.fields):
            if f.name not in value:
                continue
            nt = self.type_map.get(f.name)
            if nt is None:
                raise IllegalState(
                    "Cannot find NavType for argument %s. Please provide NavType through typeMap." % f.name
                )
            raw = value[f.name]
            if nt.is_collection:
                self.map[f.name] = nt.serialize_as_values(raw)
            else:
                self.map[f.name] = [nt.serialize_as_value(raw)]
        return dict(self.map)


class RouteDecoder:
    """等价 RouteDecoder：只 decode store 里存在的元素，缺失元素直接跳过。"""

    def __init__(self, store, type_map, serializer):
        self.store = store
        self.type_map = type_map
        self.serializer = serializer

    def decode(self):
        out = {}
        for f in self.serializer.fields:
            if f.name not in self.store:
                continue  # decodeElementIndex 的 skip 分支
            raws = self.store[f.name]
            nt = self.type_map.get(f.name)
            if nt is None:
                raise IllegalState("Failed to find type for %s when decoding %s" % (f.name, self.store))
            value = raws[0] if not nt.is_collection else raws
            parsed = nt.parse(value) if not nt.is_collection else nt.parse_many(value)
            if parsed is None and not f.nullable:
                raise IllegalState("Unexpected null value for non-nullable argument %s" % f.name)
            out[f.name] = parsed
        return out


class Serializer:
    """Stand-in for KSerializer：serialName + 字段列表。"""

    def __init__(self, serial_name, fields):
        self.serial_name = serial_name
        self.fields = fields


class NavDestination:
    def __init__(self, route, required=(), arguments=None, arg_types=None):
        self.route = route
        self.required = set(required)
        self.arguments = dict(arguments or {})
        # matchRoute 的 matchingArgs 由 NavType.parseAndPut 写入，是解析后的对象而非字符串
        self.arg_types = dict(arg_types or {})

    def add_in_default_args(self, args):
        merged = dict(self.arguments)
        if args:
            merged.update(args)
        return merged


class NavGraph(NavDestination):
    """嵌套图：本身是『虚拟 destination』，不会进入 back stack。"""

    def __init__(self, route, start_destination_route=None, start_destination_id=0):
        super().__init__(route)
        self.start_destination_route = start_destination_route
        self.start_destination_id = start_destination_id
        self.nodes = {}
        self.parent = None

    def add_destination(self, node):
        node.parent = self
        self.nodes[node.route] = node

    def find_node(self, route, search_parents=True):
        """精确命中优先；未命中再按占位符模式匹配（官方 startRoute 可带字面参数值）。"""
        if route in self.nodes:
            return self.nodes[route]
        node, _ = self.match_route(route)
        if node is not None:
            return node
        if not search_parents:
            return None
        p = self.parent
        while p is not None:
            if route in p.nodes:
                return p.nodes[route]
            node, _ = p.match_route(route)
            if node is not None:
                return node
            p = p.parent
        return None

    def match_route(self, route):
        """在直接子节点里按模式匹配（模式片段数 == route 片段数）。"""
        segs = route.split("/")
        for node in self.nodes.values():
            pat = node.route.split("/")
            if len(pat) != len(segs):
                continue
            args = {}
            ok = True
            for p, s in zip(pat, segs):
                if p.startswith("{") and p.endswith("}"):
                    args[p[1:-1]] = s
                elif p != s:
                    ok = False
                    break
            if ok:
                return node, args
        return None, None


def navigate_to_graph(graph, args=None, back_stack=None):
    """等价 NavGraphNavigator.navigate(entry, navOptions, extras)。

    返回 (压栈的 destination, 合并后的 args)。
    """
    if graph.start_destination_id == 0 and graph.start_destination_route is None:
        raise IllegalState(
            "no start destination defined via app:startDestination for %s" % graph.route
        )
    start_route = graph.start_destination_route
    node = graph.find_node(start_route, search_parents=False)
    if node is None:
        raise IllegalArg(
            "navigation destination %s is not a direct child of this NavGraph" % start_route
        )

    effective = args
    if start_route != node.route:
        _, matching = graph.match_route(start_route)
        if matching:
            matching = {
                k: node.arg_types[k].parse(v) if k in node.arg_types else v
                for k, v in matching.items()
            }
            merged = dict(matching)
            if args:
                merged.update(args)  # 源码：args 后写入 ⇒ 已有 args 优先
            effective = merged

    final_args = node.add_in_default_args(effective)
    missing = sorted(k for k in node.required if k not in final_args)
    if missing:
        raise IllegalArg(
            "Cannot navigate to startDestination %s. Missing required arguments [%s]"
            % (node.route, ", ".join(missing))
        )

    if back_stack is not None:
        back_stack.append(node)  # graph 自身不入栈
    return node, final_args
