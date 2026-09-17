package demo.okhttp;

import java.io.File;
import java.io.IOException;
import java.util.concurrent.TimeUnit;

import okhttp3.Cache;
import okhttp3.Connection;
import okhttp3.ConnectionPool;
import okhttp3.Interceptor;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;

/**
 * OkHttp 拦截器链与连接池(Java 用法,与 Kotlin 版一一对应)。
 *
 * 依赖:com.squareup.okhttp3:okhttp(仓库不随附第三方依赖)。
 * 关键结论:
 *   - 应用拦截器 addInterceptor() 每次 call 只调用一次(缓存命中/重定向亦然)
 *   - 网络拦截器 addNetworkInterceptor() 每个真实网络请求调用一次,链上 connection() 非空
 *   - ConnectionPool 默认 5 条空闲连接 / 5 分钟空闲淘汰,同 Address 复用连接
 */
public final class OkHttpChainDemo {

    private OkHttpChainDemo() {
    }

    /** 应用拦截器:统计「用户发起的请求次数」,不受重定向/缓存影响。 */
    static final class CountingAppInterceptor implements Interceptor {
        private final String name;

        CountingAppInterceptor(String name) {
            this.name = name;
        }

        @Override
        public Response intercept(Chain chain) throws IOException {
            Request request = chain.request();
            System.out.println("[" + name + "] app: " + request.method() + " " + request.url());

            Request decorated = request.newBuilder()
                    .header("X-Trace-Id", "trace-" + System.nanoTime())
                    .build();

            Response response = chain.proceed(decorated);
            if (response.code() == 401) {           // 短路并重试:先关掉旧响应体
                response.close();
                Request refreshed = decorated.newBuilder()
                        .header("Authorization", "Bearer refreshed")
                        .build();
                response = chain.proceed(refreshed);
            }
            System.out.println("[" + name + "] app done: " + response.code());
            return response;
        }
    }

    /** 网络拦截器:统计真正跑在链路上的请求,可读到 Connection 的 IP / 协议。 */
    static final class LinkProbeInterceptor implements Interceptor {
        @Override
        public Response intercept(Chain chain) throws IOException {
            Connection connection = chain.connection();
            if (connection == null) {
                throw new IllegalStateException("network interceptor 必须能看到 connection");
            }
            System.out.println("net: " + chain.request().url() + " protocol=" + connection.protocol()
                    + " route=" + connection.route().socketAddress());
            return chain.proceed(chain.request());
        }
    }

    public static OkHttpClient buildClient(File cacheDir) {
        return new OkHttpClient.Builder()
                .connectionPool(new ConnectionPool(5, 5, TimeUnit.MINUTES))
                .cache(new Cache(new File(cacheDir, "okhttp_cache"), 50L * 1024 * 1024))
                .addInterceptor(new CountingAppInterceptor("app"))
                .addNetworkInterceptor(new LinkProbeInterceptor())
                .retryOnConnectionFailure(true)
                .connectTimeout(10, TimeUnit.SECONDS)
                .readTimeout(15, TimeUnit.SECONDS)
                .build();
    }

    public static String get(OkHttpClient client, String url) throws IOException {
        Request request = new Request.Builder().url(url).build();
        try (Response response = client.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw new IOException("unexpected code " + response.code());
            }
            return response.body() == null ? "" : response.body().string();
        }
    }

    public static void main(String[] args) throws IOException {
        OkHttpClient client = buildClient(new File(System.getProperty("java.io.tmpdir")));
        System.out.println(get(client, "https://square.github.io/okhttp/"));
    }
}
