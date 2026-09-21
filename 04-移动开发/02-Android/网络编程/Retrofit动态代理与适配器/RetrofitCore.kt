// Retrofit 核心机制的 Kotlin 等价实现（与 main.py / request_factory.py 对偶）。
// 真实项目直接用 retrofit2；这里只保留可判定的判据。

object RetrofitCore {

    class ConfigException(message: String) : IllegalArgumentException(message)

    // ---- 1. Platform：按 java.vm.name 分支 ----
    data class Platform(
        val callbackExecutor: String?,
        val reflection: String,
        val builtInFactories: String,
    )

    fun resolvePlatform(vmName: String, sdkInt: Int = 24): Platform = when (vmName) {
        "Dalvik" -> Platform(
            callbackExecutor = "AndroidMainExecutor",
            reflection = if (sdkInt >= 24) "Android24" else "Legacy",
            builtInFactories = if (sdkInt >= 24) "Java8" else "Legacy",
        )
        "RoboVM" -> Platform(null, "Legacy", "Legacy")
        else -> Platform(null, "Java8", "Java8")
    }

    // ---- 2. HTTP 方法是否带请求体 ----
    private val BODY_METHODS = setOf("POST", "PUT", "PATCH")

    fun hasBody(httpMethod: String) = httpMethod in BODY_METHODS

    // ---- 3. RequestFactory 的注解校验 ----
    private val PATH_PARAM = Regex("[a-zA-Z][a-zA-Z0-9_-]*")

    data class Anno(val kind: String, val value: String?)
    data class Param(val annos: List<Anno>, val typeName: String = "String")
    data class Method(
        val name: String,
        val annos: List<Anno>,
        val params: List<Param> = emptyList(),
        val returnType: String = "Call<X>",
        val isDefault: Boolean = false,
        val isSuspend: Boolean = false,
    )

    class RequestFactory(method: Method) {
        val httpMethod: String
        val hasBody: Boolean
        val relativeUrl: String?

        init {
            val methods = method.annos.filter { it.kind in setOf("GET", "HEAD", "DELETE", "OPTIONS", "POST", "PUT", "PATCH") }
            if (methods.isEmpty()) {
                throw ConfigException("HTTP method annotation is required (e.g., @GET, @POST, etc.).")
            }
            require(methods.size == 1) {
                "Only one HTTP method is allowed. Found: ${methods[0].kind} and ${methods[1].kind}."
            }
            val m = methods[0]
            httpMethod = m.kind
            hasBody = hasBody(m.kind)

            val encodings = method.annos.filter { it.kind == "Multipart" || it.kind == "FormUrlEncoded" }
            require(encodings.size <= 1) { "Only one encoding annotation is allowed." }
            encodings.firstOrNull()?.let {
                if (!hasBody) {
                    throw ConfigException(
                        "${it.kind} can only be specified on HTTP methods with request body (e.g., @POST)."
                    )
                }
            }

            relativeUrl = m.value?.also { value ->
                val query = value.substringAfter("?", "")
                if (query.isNotEmpty()) {
                    require(!query.contains("{")) {
                        "URL query string \"$query\" must not have replace block. " +
                            "For dynamic query parameters use @Query."
                    }
                }
                Regex("\\{([^}]*)\\}").findAll(value.substringBefore("?")).forEach {
                    require(PATH_PARAM.matches(it.groupValues[1])) {
                        "@Path parameter name must match ${PATH_PARAM.pattern}. Found: ${it.groupValues[1]}"
                    }
                }
            }

            var seenQuery = false
            var hasUrlParam = false
            for (p in method.params) {
                require(p.annos.size <= 1) { "Multiple Retrofit annotations found, only one allowed." }
                require(p.annos.isNotEmpty()) { "No Retrofit annotation found." }
                val kind = p.annos.first().kind
                if (kind == "Body") {
                    require(hasBody) { "Non-body HTTP method cannot contain @Body." }
                }
                if (kind == "Url") {
                    require(!hasUrlParam) { "Multiple @Url method annotations found." }
                    require(m.value == null) { "@Url cannot be used with @$httpMethod URL" }
                    require(p.typeName in setOf("okhttp3.HttpUrl", "String", "java.net.URI", "android.net.Uri")) {
                        "@Url must be okhttp3.HttpUrl, String, java.net.URI, or android.net.Uri type."
                    }
                    hasUrlParam = true
                }
                if (kind == "Path") {
                    require(!hasUrlParam) { "@Path parameters may not be used with @Url." }
                    require(!seenQuery) { "A @Path parameter must not come after a @Query." }
                }
                if (kind == "Url" && seenQuery) {
                    throw ConfigException("A @Url parameter must not come after a @Query.")
                }
                if (kind in setOf("Query", "QueryName", "QueryMap")) seenQuery = true
            }
            require(relativeUrl != null || hasUrlParam) {
                "Missing either @$httpMethod URL or @Url parameter."
            }
        }
    }

    // ---- 4. CallAdapter 工厂链与 skipPast ----
    data class CallAdapter(val factoryName: String, val responseType: String)

    class CallAdapterFactory(
        val name: String,
        private val prefixes: Set<String>,
        private val needsSkip: Boolean = false,
    ) {
        fun get(returnType: String, annotations: List<String>): CallAdapter? {
            if (prefixes.none { returnType.startsWith(it) }) return null
            if (needsSkip && "SkipCallbackExecutor" !in annotations) return null
            val inner = returnType.substringAfter("<").substringBeforeLast(">")
            return CallAdapter(name, if (inner == returnType) returnType else inner)
        }
    }

    fun nextCallAdapter(
        factories: List<CallAdapterFactory>,
        skipPast: CallAdapterFactory?,
        returnType: String,
        annotations: List<String>,
    ): CallAdapter {
        val start = factories.indexOf(skipPast) + 1   // 找不到 = -1 ⇒ 从头
        for (i in start until factories.size) {
            factories[i].get(returnType, annotations)?.let { return it }
        }
        val skipped = if (start > 0) "\n  Skipped:" + factories.take(start).joinToString("") { "\n   * ${it.name}" } else ""
        val tried = "\n  Tried:" + factories.drop(start).joinToString("") { "\n   * ${it.name}" }
        throw ConfigException("Could not locate call adapter for $returnType.$skipped$tried")
    }

    // ---- 5. ServiceMethod：返回类型与 suspend 改写 ----
    fun parseServiceMethod(
        method: Method,
        factories: List<CallAdapterFactory>,
    ): String {
        RequestFactory(method)
        require(method.returnType != "void") { "Service methods cannot return void." }
        require(!method.returnType.contains("<T>")) {
            "Method return type must not include a type variable or wildcard: ${method.returnType}"
        }
        val isSuspend = method.isSuspend
        val annotations = method.annos.map { it.kind }.toMutableList()
        val adapterType: String
        var wantsResponse = false
        if (isSuspend) {
            wantsResponse = method.returnType.startsWith("Response<")
            val body = if (wantsResponse) method.returnType.removeSurrounding("Response<", ">")
            else method.returnType
            adapterType = "Call<$body>"
            annotations += "SkipCallbackExecutor"
        } else {
            adapterType = method.returnType
        }
        val adapter = nextCallAdapter(factories, null, adapterType, annotations)
        when (adapter.responseType) {
            "okhttp3.Response" -> throw ConfigException(
                "'okhttp3.Response' is not a valid response body type. Did you mean ResponseBody?"
            )
            "Response" -> throw ConfigException("Response must include generic type (e.g., Response<String>)")
        }
        val rf = RequestFactory(method)
        if (rf.httpMethod == "HEAD" && adapter.responseType !in setOf("Void", "Unit")) {
            throw ConfigException("HEAD method must use Void or Unit as response type.")
        }
        return when {
            !isSuspend -> "CallAdapted"
            wantsResponse -> "SuspendForResponse"
            else -> "SuspendForBody"
        }
    }
}
