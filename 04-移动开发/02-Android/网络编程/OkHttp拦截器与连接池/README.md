# OkHttp 拦截器链与连接池

## 简介

OkHttp 的一次请求不是「函数调用链」而是**责任链**(Chain of Responsibility):框架把重试、协议补全、
缓存、建连、网络收发拆成 7 层拦截器,每层只做一件事,并可选择把请求交给下一层或直接短路。

关键概念:

| 概念 | 一句话解释 |
| --- | --- |
| Application Interceptor | 用户用 `addInterceptor()` 注册的拦截器,位于链首,每次 call **恰好调用一次** |
| Network Interceptor | 用户用 `addNetworkInterceptor()` 注册,位于 `ConnectInterceptor` 与 `CallServer` 之间,**每个真实网络请求**调用一次 |
| Address | 服务器的**静态**配置:scheme + host + port + 端口/TLS 设置/协议偏好,是连接复用的键 |
| Route | 要真正连上的**动态**信息:具体 IP(DNS 结果)、代理、TLS 版本 |
| ConnectionPool | 空闲连接池,默认保留 5 条、空闲 5 分钟后淘汰,HTTP/1.x 复用 / HTTP/2 多路复用 |

历史背景:OkHttp 3(2016)引入现在这套拦截器架构,把原先散落在 `HttpEngine` 里的逻辑拆成可组合的层,
因此「加日志」「加 Token」「加离线缓存」都不需要改框架代码,官方也把自己的 `HttpLoggingInterceptor`
建在同一套扩展机制之上。

## 原理详解

### 1. 链的装配顺序(来自官方源码 `getResponseWithInterceptorChain()`)

```text
Application Interceptors            ← addInterceptor()
  ↓
RetryAndFollowUpInterceptor         ← 重试 / 重定向 / 认证挑战
  ↓
BridgeInterceptor                   ← 补全 Host / Content-Length / Cookie,处理 gzip
  ↓
CacheInterceptor                    ← 缓存读写;命中即短路,不再向下走
  ↓
ConnectInterceptor                  ← 从连接池取连接,没有就按 Route 新建
  ↓
Network Interceptors                ← addNetworkInterceptor(),此时 connection() 非空
  ↓
CallServerInterceptor               ← 真正写请求、读响应
```

响应沿**相反方向**逐层回溯,所以每个 `intercept()` 都同时拥有「请求前」和「响应后」两个时机。

### 2. `proceed()` 是递归的入口

```text
chain(index=i).proceed(request)
   → 新建 chain(index=i+1)
   → 调用 interceptors[i].intercept(新 chain)
```

因此:**不调用 `proceed()` = 短路**(CacheInterceptor 命中缓存就是这么做的);
**调用两次 `proceed()` = 下游整体执行两次**(可用于本地重试,但必须先把上一个响应体 close 掉)。

### 3. 两种拦截器的能力差异(官方文档原表)

| 能力 | Application | Network |
| --- | --- | --- |
| 需要关心重定向/重试等中间响应 | 不需要 | 可以操作 |
| 调用次数 | 每次 call 一次(缓存命中也会调) | 每个网络请求一次;缓存短路则不调 |
| 能看到应用原始意图(不含 OkHttp 注入的 header) | 是 | 否(看到的是链路上的数据) |
| 能拿到 `Connection`(IP/TLS 信息) | 否 | 是 |
| 可以短路 / 多次 `proceed()` | 是 | 仅一次(连接已就绪) |
| 可调 `withConnectTimeout` 等 | 是 | 否 |

### 4. 连接复用与建连步骤(官方《Connections》)

1. 用 URL + 配置好的 OkHttpClient 构造 **Address**;
2. 用 Address 去 **ConnectionPool** 找可用连接;
3. 找不到就选一条 **Route**(通常是一次 DNS 查询),再选 TLS 版本/代理;
4. 新建连接:直连 socket / HTTP 代理下的 TLS 隧道 / 直接 TLS,按需做 TLS 握手;
5. 发送请求、读取响应;结束后连接**归还连接池**供后续复用,长期空闲则被淘汰。

## 对比 / 选型

| 维度 | 应用拦截器 | 网络拦截器 |
| --- | --- | --- |
| 典型用途 | 统一 header、Token、日志、统计「用户发起的请求数」 | 监控真实流量、改压缩/Chunk、统计实际网络往返 |
| 统计口径风险 | 无(与用户意图一一对应) | 重定向会重复计数、缓存命中不计数 |

## 环境准备

- 语言:**Kotlin**(`kotlin/`)与 **Java**(`java/`),依赖 `com.squareup.okhttp3:okhttp`
- 自检模型:**Python 3.10+**,零第三方依赖
- 说明:本仓库不随附第三方依赖,`kotlin/`、`java/` 用于展示官方 API 用法与链路顺序

## 运行方式

### Python(模型 + 自检,可直接跑)

```bash
cd python && python3 okhttp_chain_check.py
```

### Kotlin / Java(Android 或 JVM 工程内)

```bash
kotlinc OkHttpChainDemo.kt -cp okhttp-5.x.jar -include-runtime -d demo.jar && java -jar demo.jar
javac -cp okhttp-5.x.jar OkHttpChainDemoJava.java && java -cp .:okhttp-5.x.jar demo.okhttp.OkHttpChainDemoJava
```

## 关键代码片段

应用拦截器(链首,每次 call 一次,可短路、可重试):

```kotlin
class TracingInterceptor : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val decorated = chain.request().newBuilder()
            .header("X-Trace-Id", "trace-${System.nanoTime()}")
            .build()
        var response = chain.proceed(decorated)
        if (response.code >= 500) {          // 重试前必须关闭上一个响应体
            response.close()
            response = chain.proceed(decorated)
        }
        return response
    }
}
```

网络拦截器(链尾附近,`connection()` 非空):

```kotlin
class NetworkProbeInterceptor : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val connection = chain.connection()
            ?: error("network interceptor must observe a live connection")
        println("${chain.request().url} via ${connection.protocol()}")
        return chain.proceed(chain.request())
    }
}
```

连接池(默认值直接写在源码的默认构造参数里):

```kotlin
OkHttpClient.Builder()
    .connectionPool(ConnectionPool(5, 5, TimeUnit.MINUTES))   // ConnectionPool.kt 的默认值
    .cache(Cache(File(cacheDir, "okhttp_cache"), 50L * 1024 * 1024))
    .addInterceptor(TracingInterceptor())
    .addNetworkInterceptor(NetworkProbeInterceptor())
    .build()
```

## 性能与边界

- 连接复用的收益来自省掉 TCP 三次握手与 TLS 握手,并避开 TCP 慢启动;`connections.md` 明确把
  「更低延迟、更高吞吐、更省电」列为复用的动机。
- 池容量是**策略**而非硬限制:默认 5 条空闲连接 / 5 分钟;超出时按「最久空闲」淘汰。
- 同一 `OkHttpClient` 持有自己的连接池与线程池,**应全局共享**;重复 new 会造成空闲池资源浪费。
- OkHttp 5.0 起支持 fast fallback(Happy Eyeballs):并发尝试多条 Route,先连上的留下、
  其余取消;规则是先 IPv6 后 IPv4 交替、距上次尝试不足 250 ms 不发起新尝试、只赛 TCP、
  TLS 握手只在胜出的 TCP 上做。文档注明其对应 RFC 6555。

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| 用网络拦截器统计「用户请求数」偏多或偏少 | 重定向会多次触发、缓存命中不触发 | 计数放应用拦截器,网络层统计只用于流量口径 |
| 重复调用 `proceed()` 后连接/内存异常 | 上一个响应体未关闭 | 二次 `proceed()` 前先 `response.close()` |
| 配了缓存却不生效 | 没设置 `Cache` 目录时 `CacheInterceptor` 形同虚设 | 显式 `Builder.cache(Cache(dir, size))` |
| 以为网络拦截器一定能看到 header | 它看到的是 OkHttp 注入后的数据,`Accept-Encoding: gzip` 等都在 | 要「原始意图」就用应用拦截器 |
| 每处都 new 一个 OkHttpClient | 每个 Client 各带连接池与线程池 | 单例共享 |

## 参考资料(实际阅读过的权威来源)

- [OkHttp 官方文档 `docs/features/interceptors.md`](https://github.com/square/okhttp/blob/master/docs/features/interceptors.md)
  —— 责任链顺序、应用/网络拦截器对照表、重定向下两者调用次数的实测输出
- [OkHttp 官方文档 `docs/features/connections.md`](https://github.com/square/okhttp/blob/master/docs/features/connections.md)
  —— URL/Address/Route/Connection 四层模型、建连 5 步、fast fallback 规则(引用 RFC 6555)
- [OkHttp `okhttp3.ConnectionPool` 源码](https://github.com/square/okhttp/blob/master/okhttp/src/commonJvmAndroid/kotlin/okhttp3/ConnectionPool.kt)
  —— 默认 `maxIdleConnections = 5`、`keepAliveDuration = 5 MINUTES`,`evictAll()` 语义
- 本目录 `python/okhttp_chain_model.py` 的自检输出(22 项断言,实跑通过)
