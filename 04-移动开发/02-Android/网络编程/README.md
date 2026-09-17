# Android · 网络编程

研究 Android 上 HTTP 客户端的请求流水线:OkHttp 的拦截器链、连接池与路由选择,以及上层 Retrofit 的适配方式。

## 子领域

| 子目录 | 知识点 |
| --- | --- |
| [OkHttp拦截器与连接池/](./OkHttp拦截器与连接池/) | 应用/网络拦截器分界、7 层内置链的执行顺序与短路、连接池(5 空闲 / 5 分钟)、`Address`/`Route`/`Connection` 与 Happy Eyeballs 快速回退 |

## 请求流水线一览

```
Request
  → ApplicationInterceptor 链(可短路:缓存/重定向/重试)
    → RetryAndFollowUpInterceptor(重定向 20 次上限、`isRecoverable`)
      → BridgeInterceptor(补 Host/Content-Type/gzip、CookieJar)
        → CacheInterceptor(缓存命中即短路,不建连接)
          → ConnectInterceptor(取/建连接:连接池 → Route 竞选)
            → NetworkInterceptor 链(Tracing / 打点)
              → CallServerInterceptor(真正读写 socket)
```

## 待研究

- [ ] HTTP/2 多路复用(`Http2Connection` 的流控与 `SETTINGS` 帧协商)
- [ ] HTTP/3 与 QUIC(OkHttp 的 `quic` 实验模块 + Cronet 方案)
- [ ] DNS 层:`DnsOverHttps` 与自定义 `Dns` 接口
- [ ] TLS 握手细节(`ConnectionSpec` / 证书固定 / `HostnameVerifier`)
- [ ] 断点续传与 `Range` 请求(`ResponseBody` 的流式处理)
- [ ] 请求取消与 `Call.cancel()` 的传播路径(协程取消如何穿透到 socket)
- [ ] Retrofit 的动态代理与 `CallAdapter` / `Converter` 解析机制
- [ ] 弱网优化:超时矩阵(`connectTimeout` / `readTimeout` / `callTimeout`)的相互作用
