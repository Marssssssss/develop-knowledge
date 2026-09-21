// 类型安全导航机制的 Kotlin 侧等价实现（脱离 Compose 渲染，只保留判据）。
// 与 main.py 一一对应；真机上使用 androidx.navigation 的 RouteBuilder/RouteEncoder/RouteDecoder。

object TypeSafeNavigation {

    enum class ParamType { PATH, QUERY }

    interface NavType<T> {
        val isCollection: Boolean get() = false
        fun serializeAsValue(value: T): String
        fun serializeAsValues(value: T): List<String> = listOf(serializeAsValue(value))
        fun parse(raw: String): T
    }

    object IntType : NavType<Int> {
        override fun serializeAsValue(value: Int) = value.toString()
        override fun parse(raw: String) = raw.toInt()
    }

    object StringType : NavType<String> {
        override fun serializeAsValue(value: String) = value
        override fun parse(raw: String) = value
    }

    class ListType<E>(private val inner: NavType<E>) : NavType<List<E>> {
        override val isCollection = true
        override fun serializeAsValue(value: List<E>) = value.joinToString(",")
        override fun serializeAsValues(value: List<E>) = value.map(inner::serializeAsValue)
        override fun parse(raw: String) = raw.split(",").map(inner::parse)
    }

    data class Element(val name: String, val optional: Boolean, val nullable: Boolean = false)

    data class Descriptor(val serialName: String, val elements: List<Element>)

    class RouteBuilder(private val descriptor: Descriptor) {
        private var pathArgs = ""
        private var queryArgs = ""
        private val path = descriptor.serialName

        fun build() = path + pathArgs + queryArgs

        // 官方唯一判据：集合类型或可选元素 → QUERY，否则 PATH
        fun computeParamType(index: Int, type: NavType<*>): ParamType =
            if (type.isCollection || descriptor.elements[index].optional) ParamType.QUERY
            else ParamType.PATH

        fun appendPattern(index: Int, name: String, type: NavType<*>) {
            when (computeParamType(index, type)) {
                ParamType.PATH -> pathArgs += "/{$name}"
                ParamType.QUERY -> queryArgs += (if (queryArgs.isEmpty()) "?" else "&") + "$name={$name}"
            }
        }

        fun appendArg(index: Int, name: String, type: NavType<*>, values: List<String>) {
            when (computeParamType(index, type)) {
                ParamType.PATH -> {
                    require(values.size == 1) {
                        "Expected one value for argument $name, found ${values.size} values instead."
                    }
                    pathArgs += "/${values.first()}"
                }
                ParamType.QUERY -> values.forEach { v ->
                    queryArgs += (if (queryArgs.isEmpty()) "?" else "&") + "$name=$v"
                }
            }
        }
    }

    class RouteEncoder<T>(private val descriptor: Descriptor, private val typeMap: Map<String, NavType<*>>) {
        private val map = mutableMapOf<String, List<String>>()

        fun encodeToArgMap(values: Map<String, Any?>): Map<String, List<String>> {
            for (i in descriptor.elements.indices) {
                val el = descriptor.elements[i]
                val raw = values[el.name] ?: continue
                @Suppress("UNCHECKED_CAST")
                val navType = checkNotNull(typeMap[el.name]) {
                    "Cannot find NavType for argument ${el.name}. Please provide NavType through typeMap."
                } as NavType<Any?>
                map[el.name] = if (navType.isCollection) navType.serializeAsValues(raw)
                else listOf(navType.serializeAsValue(raw))
            }
            return map.toMap()
        }
    }

    class RouteDecoder(
        private val store: Map<String, List<String?>>,
        private val typeMap: Map<String, NavType<*>>,
        private val descriptor: Descriptor,
    ) {
        fun decode(): Map<String, Any?> {
            val out = mutableMapOf<String, Any?>()
            for (el in descriptor.elements) {
                // decodeElementIndex：store 里没有就直接跳过，不会产出 null
                val raws = store[el.name] ?: continue
                @Suppress("UNCHECKED_CAST")
                val navType = checkNotNull(typeMap[el.name]) {
                    "Failed to find type for ${el.name} when decoding $store"
                } as NavType<Any?>
                val value: Any? = if (navType.isCollection) {
                    navType.parse(raws.joinToString(","))
                } else {
                    raws.firstOrNull()?.let(navType::parse)
                }
                check(!(value == null && !el.nullable)) {
                    "Unexpected null value for non-nullable argument ${el.name}"
                }
                out[el.name] = value
            }
            return out
        }
    }

    open class NavDestination(val route: String, val required: Set<String> = emptySet()) {
        open val arguments: Map<String, Any?> = emptyMap()
    }

    class NavGraph(
        route: String,
        val startDestinationRoute: String?,
    ) : NavDestination(route) {
        private val nodes = LinkedHashMap<String, NavDestination>()
        var parent: NavGraph? = null

        fun addDestination(node: NavDestination) {
            if (node is NavGraph) node.parent = this
            nodes[node.route] = node
        }

        fun findNode(route: String, searchParents: Boolean = true): NavDestination? {
            nodes[route]?.let { return it }
            matchRoute(route)?.first?.let { return it }
            if (!searchParents) return null
            var p = parent
            while (p != null) {
                p.nodes[route]?.let { return it }
                p.matchRoute(route)?.first?.let { return it }
                p = p.parent
            }
            return null
        }

        fun matchRoute(route: String): Pair<NavDestination, Map<String, String>>? {
            val segs = route.split("/")
            for (node in nodes.values) {
                val pat = node.route.split("/")
                if (pat.size != segs.size) continue
                val args = mutableMapOf<String, String>()
                var ok = true
                for ((p, s) in pat.zip(segs)) {
                    when {
                        p.startsWith("{") && p.endsWith("}") -> args[p.removeSurrounding("{", "}")] = s
                        p != s -> { ok = false; break }
                    }
                }
                if (ok) return node to args
            }
            return null
        }
    }

    fun navigateToGraph(
        graph: NavGraph,
        args: Map<String, Any?>? = null,
        backStack: MutableList<NavDestination> = mutableListOf(),
    ): Pair<NavDestination, Map<String, Any?>> {
        val startRoute = graph.startDestinationRoute
        check(startRoute != null) { "no start destination defined via app:startDestination for ${graph.route}" }
        val startDestination = requireNotNull(graph.findNode(startRoute, searchParents = false)) {
            IllegalArgumentException(
                "navigation destination $startRoute is not a direct child of this NavGraph"
            )
        }
        var effective = args
        if (startRoute != startDestination.route) {
            val matching = graph.matchRoute(startRoute)?.second
            if (!matching.isNullOrEmpty()) {
                val merged = matching.toMutableMap<String, Any?>()
                args?.let { merged.putAll(it) } // 外部传入优先
                effective = merged
            }
        }
        val finalArgs = HashMap(startDestination.arguments).apply { effective?.let { putAll(it) } }
        val missing = startDestination.required.filter { it !in finalArgs }
        require(missing.isEmpty()) {
            "Cannot navigate to startDestination ${startDestination.route}. Missing required arguments $missing"
        }
        backStack.add(startDestination) // graph 自身不入栈
        return startDestination to finalArgs
    }
}
