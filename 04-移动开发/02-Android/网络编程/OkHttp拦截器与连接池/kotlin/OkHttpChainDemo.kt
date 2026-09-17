package demo.okhttp

import okhttp3.Cache
import okhttp3.Call
import okhttp3.Connection
import okhttp3.ConnectionPool
import okhttp3.EventListener
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * OkHttp 拦截器链与连接池(官方 API 用法)。
 *
 * 依赖:com.squareup.okhttp3:okhttp(本仓库不随附第三方依赖,本文件用于展示 API 与链路顺序)。
 *
 * 链路顺序(官方 getResponseWithInterceptorChain() 的装配顺序):
 *   Application Interceptors → RetryAndFollowUp → Bridge → Cache → Connect
 *   → Network Interceptors → CallServer
 * 响应沿相反方向回溯。
 */
object OkHttpDemo {

    /** 应用拦截器:每次 call **恰好一次**,缓存命中也会执行;可短路、可多次 proceed()。 */
    class TracingInterceptor(private val name: String) : Interceptor {
        override fun intercept(chain: Interceptor.Chain): Response {
            val request = chain.request()
            val startNs = System.nanoTime()
            println("[$name] app-interceptor -> ${request.method} ${request.url}")

            // 允许改写请求(应用拦截器看到的是「应用的原始意图」,不含 OkHttp 注入的 header)
            val decorated = request.newBuilder()
                .header("X-Trace-Id", "trace-$startNs")
                .build()

            // 允许调用多次:这里演示「本地失败重试」;真实项目要自行控制次数
            var response = chain.proceed(decorated)
            if (response.code >= 500) {
                response.close()                     // 重复 proceed() 之前必须关闭上一个响应体
                response = chain.proceed(decorated)
            }

            val costMs = (System.nanoTime() - startNs) / 1e6
            println("[$name] app-interceptor <- ${response.code} in $costMs ms")
            return response
        }

        /** 应用拦截器可用 with*Timeout 覆盖本次 call 的超时。 */
        fun withTimeouts(chain: Interceptor.Chain): Response =
            chain.withConnectTimeout(2, TimeUnit.SECONDS)
                .withReadTimeout(5, TimeUnit.SECONDS)
                .proceed(chain.request())
    }

    /** 网络拦截器:只对**真实发出的网络请求**调用;重定向会再触发一次,缓存短路则不调用。 */
    class NetworkProbeInterceptor : Interceptor {
        override fun intercept(chain: Interceptor.Chain): Response {
            val connection: Connection = chain.connection()
                ?: error("network interceptor must observe a live connection")
            println("net-interceptor -> ${chain.request().url} via $connection")
            val response = chain.proceed(chain.request())
            println("net-interceptor <- ${response.code}, gzip=${response.header("Content-Encoding")}")
            return response
        }
    }

    /** 事件监听器:按阶段量化耗时(官方 EventListener 的用途)。 */
    class MetricsListener : EventListener() {
        private var callStart = 0L
        private var dnsStart = 0L
        private var connectStart = 0L

        override fun callStart(call: Call) {
            callStart = System.nanoTime()
        }

        override fun dnsStart(call: Call, domainName: String) {
            dnsStart = System.nanoTime()
        }

        override fun dnsEnd(call: Call, domainName: String, inetAddressList: List<java.net.InetAddress>) {
            println("dns ${domainName}: ${(System.nanoTime() - dnsStart) / 1e6} ms")
        }

        override fun connectStart(call: Call, inetSocketAddress: java.net.InetSocketAddress, proxy: java.net.Proxy) {
            connectStart = System.nanoTime()
        }

        override fun callEnd(call: Call) {
            println("call total: ${(System.nanoTime() - callStart) / 1e6} ms")
        }
    }

    /**
     * 连接池:同 Address(scheme+host+port)复用同一条 TCP 连接;
     * 默认最多保留 5 条空闲连接、空闲 5 分钟后淘汰(ConnectionPool 源码的默认构造)。
     */
    fun buildClient(cacheDir: File): OkHttpClient = OkHttpClient.Builder()
        .connectionPool(ConnectionPool(maxIdleConnections = 5, keepAliveDuration = 5, timeUnit = TimeUnit.MINUTES))
        .cache(Cache(File(cacheDir, "okhttp_cache"), 50L * 1024 * 1024))
        .addInterceptor(TracingInterceptor("app"))            // 位置在前
        .addNetworkInterceptor(NetworkProbeInterceptor())     // 位置在 Connect 之后
        .eventListenerFactory { MetricsListener() }
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()

    /** 共享的客户端实例:每个 OkHttpClient 各自持有连接池与线程池,重复创建会浪费资源。 */
    @Volatile
    private var shared: OkHttpClient? = null

    fun sharedClient(cacheDir: File): OkHttpClient =
        shared ?: synchronized(this) { shared ?: buildClient(cacheDir).also { shared = it } }

    fun get(client: OkHttpClient, url: String): String = client.newCall(
        Request.Builder().url(url).build()
    ).execute().use { response ->
        if (!response.isSuccessful) throw IOException("unexpected code ${response.code}")
        response.body?.string() ?: ""
    }
}

fun main() {
    val client = OkHttpDemo.sharedClient(File(System.getProperty("java.io.tmpdir")))
    println(OkHttpDemo.get(client, "https://square.github.io/okhttp/"))
}
