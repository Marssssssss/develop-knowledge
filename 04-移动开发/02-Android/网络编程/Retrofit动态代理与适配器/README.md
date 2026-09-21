# Retrofit 动态代理与 CallAdapter / Converter

Retrofit 的「接口即 API」不是注解处理器生成实现类，而是 **JDK 动态代理 + 运行期解析方法注解**。
本 demo 把 `Retrofit.create()`、`RequestFactory`、`ServiceMethod`/`HttpServiceMethod`、
`nextCallAdapter`、`Platform` 五段官方源码的判据抽出来做成可执行模型。

对应 square/retrofit 主干源码：`retrofit2/Retrofit.java`、`ServiceMethod.java`、
`HttpServiceMethod.java`、`RequestFactory.java`、`Platform.java`。

## 一、原理详解

### 1.1 create()：先校验接口，再包代理

```java
public <T> T create(final Class<T> service) {
    validateServiceInterface(service);
    return (T) Proxy.newProxyInstance(service.getClassLoader(), new Class<?>[] { service }, ...);
}
```

`validateServiceInterface` 的三条硬校验：

1. `!service.isInterface()` → `"API declarations must be interfaces."`
2. **泛型参数不被支持**——而且不只是当前接口，源码用 `ArrayDeque` **递归检查所有父接口**：

   ```java
   Collections.addAll(check, candidate.getInterfaces());
   ```
   报错信息还会区分是不是父接口：`" which is an interface of " + service.getName()`。
3. `validateEagerly` 时把**非 default、非 static、非 synthetic** 的方法全部预解析一遍——
   这是把「第一次调用才炸」提前到「`create()` 就炸」的开关。

### 1.2 InvocationHandler 的三条分支

```java
if (method.getDeclaringClass() == Object.class) {
    return method.invoke(this, args);                      // ① Object 方法：走代理自身
}
args = args != null ? args : emptyArgs;
return reflection.isDefaultMethod(method)
    ? reflection.invokeDefaultMethod(method, service, proxy, args)  // ② default 方法
    : loadServiceMethod(service, method).invoke(proxy, args);       // ③ 真正的 API 方法
```

注意 ①：`toString()` / `equals()` / `hashCode()` **不会**被当成 API 方法解析，
所以它们既不需要注解，也不会进 `serviceMethodCache`。

`loadServiceMethod` 带缓存（源码注释写着 *"Once we are minSdk 24 this whole method can be
replaced by computeIfAbsent"*），所以**注解解析只发生一次**。

### 1.3 RequestFactory：一套纯判据的注解检查

`RequestFactory.parseAnnotations` 抛出的每一条错误消息都是一个可测规则（本 demo 全部覆盖）：

| 规则 | 错误消息（源码原文） |
| --- | --- |
| 必须有 HTTP 方法注解 | `HTTP method annotation is required (e.g., @GET, @POST, etc.).` |
| 只能有一个 | `Only one HTTP method is allowed. Found: %s and %s.` |
| 编码注解只能一个 | `Only one encoding annotation is allowed.` |
| Multipart/FormUrlEncoded 需带 body 的方法 | `... can only be specified on HTTP methods with request body (e.g., @POST).` |
| 无 body 方法不能有 @Body | `Non-body HTTP method cannot contain @Body.` |
| 表单至少一个 @Field | `Form-encoded method must contain at least one @Field.` |
| multipart 至少一个 @Part | `Multipart method must contain at least one @Part.` |
| URL 与 @Url 二选一 | `Missing either @%s URL or @Url parameter.` |
| @Url 与已给 URL 互斥 | `@Url cannot be used with @%s URL` |
| @Url 类型受限 | `@Url must be okhttp3.HttpUrl, String, java.net.URI, or android.net.Uri type.` |
| 多个 @Url | `Multiple @Url method annotations found.` |
| @Path 与 @Url 互斥 | `@Path parameters may not be used with @Url.` |
| **顺序**：@Url 不能晚于 @Query | `A @Url parameter must not come after a @Query.` |
| **顺序**：@Path 不能晚于 @Query | `A @Path parameter must not come after a @Query.` |
| URL 的 query 段不能有 `{}` | `URL query string ... must not have replace block. For dynamic query parameters use @Query.` |
| @Headers 不能空 / 必须有冒号 | `@Headers annotation is empty.` / `@Headers value must be in the form ...` |
| 参数注解数量 | `Multiple Retrofit annotations found, only one allowed.` / `No Retrofit annotation found.` |

**哪些方法带 body** 由 `parseHttpMethodAndPath(method, value, hasBody)` 的第三个实参决定：

```java
} else if (annotation instanceof GET)  { parseHttpMethodAndPath("GET", ..., false); }
} else if (annotation instanceof POST) { parseHttpMethodAndPath("POST", ..., true);  }
```

即 **POST / PUT / PATCH 带 body；GET / HEAD / DELETE / OPTIONS 不带**——这条决定了
`@Body`、`@Multipart`、`@FormUrlEncoded` 各自能用在哪些方法上。

占位符名必须匹配正则 `[a-zA-Z][a-zA-Z0-9_-]*`。

### 1.4 ServiceMethod：返回类型的前置校验

```java
if (Utils.hasUnresolvableType(returnType)) {
    throw methodError(method, "Method return type must not include a type variable or wildcard: %s", returnType);
}
if (returnType == void.class) {
    throw methodError(method, "Service methods cannot return void.");
}
```

### 1.5 HttpServiceMethod：suspend 的三种形态

suspend 函数没有 `Call` 返回值，源码的做法是**先解包再包装**：

```java
Type responseType = Utils.getParameterLowerBound(0, (ParameterizedType) parameterTypes[last]);
if (getRawType(responseType) == Response.class && responseType instanceof ParameterizedType) {
    responseType = Utils.getParameterUpperBound(0, (ParameterizedType) responseType); // 解包 Response<T>
    continuationWantsResponse = true;
}
adapterType = new Utils.ParameterizedTypeImpl(null, Call.class, responseType);       // 再包成 Call<T>
annotations = SkipCallbackExecutorImpl.ensurePresent(annotations);                    // 强制补注解
```

所以官方还有一条明确禁止：

> `Suspend functions should not return Call, as they already execute asynchronously.`

最终按三选一落地：`CallAdapted` / `SuspendForResponse` / `SuspendForBody`。

### 1.6 responseType 的三条校验

```java
if (responseType == okhttp3.Response.class)  → "'...' is not a valid response body type. Did you mean ResponseBody?"
if (responseType == Response.class)          → "Response must include generic type (e.g., Response<String>)"
if (httpMethod.equals("HEAD") && !Void.class.equals(responseType) && !Utils.isUnit(responseType))
                                             → "HEAD method must use Void or Unit as response type."
```

### 1.7 nextCallAdapter：skipPast 的起点

```java
int start = callAdapterFactories.indexOf(skipPast) + 1;
for (int i = start, count = callAdapterFactories.size(); i < count; i++) { ... }
```

- 顺序遍历，**第一个返回非 null 的工厂胜出**（所以工厂顺序 = 优先级）；
- `skipPast == null` 或不在列表中时 `indexOf` 返回 `-1` ⇒ `start = 0`，**从头找**（不会退化成空集）；
- 全找不到时抛 `IllegalArgumentException`，消息里同时列出 `Skipped:`（被跳过的）与 `Tried:`（试过的）——
  这条消息本身就是排查「为什么我的 Observable 没被识别」的第一手材料。

Converter 的查找（`nextResponseBodyConverter`）结构完全对称。

### 1.8 Platform：按 java.vm.name 分支

```java
switch (System.getProperty("java.vm.name")) {
  case "Dalvik":  callbackExecutor = new AndroidMainExecutor();
                  reflection = SDK_INT >= 24 ? new Reflection.Android24() : new Reflection();
                  builtInFactories = SDK_INT >= 24 ? new BuiltInFactories.Java8() : new BuiltInFactories();
                  break;
  case "RoboVM":  callbackExecutor = null; ... break;
  default:        callbackExecutor = null; reflection = new Reflection.Java8(); ...
}
```

即：**Android 上 `enqueue()` 的回调默认投递到主线程**；JVM/RoboVM 上没有默认 executor，
`suspend` 之外还要在后台线程执行就必须自己配。

## 二、对比：三种声明式 HTTP 客户端

| 维度 | Retrofit | Feign | Ktor Client |
| --- | --- | --- | --- |
| 机制 | JDK 动态代理 | JDK 动态代理 + Contract | Kotlin DSL，无代理 |
| 返回类型扩展 / suspend | CallAdapter 工厂链 + SuspendForBody/Response | Decoder/Encoder | 插件 |

## 三、环境要求

- Python 3.8+（模型无依赖）
- 真机需 `com.squareup.retrofit2:retrofit` 与 `converter-gson`

## 四、运行方式

```bash
cd 04-移动开发/02-Android/网络编程/Retrofit动态代理与适配器
python selfcheck_retrofit.py      # 期望输出 PASS 59
```

## 五、关键代码

- `request_factory.py`：`RequestFactory` 的全部注解校验规则
- `main.py`：`Retrofit.create()` / `nextCallAdapter` / `ServiceMethod.parse_annotations` / `PlatformInfo`
- `selfcheck_retrofit.py`：59 条断言
- `RetrofitCore.kt`：Kotlin 侧等价实现

## 六、性能边界与注意事项

- **注解解析只在第一次调用时发生**（`serviceMethodCache`），代价主要是反射；
  `validateEagerly(true)` 适合 debug，不适合 release 冷启动。
- `CallAdapter.Factory` 顺序敏感：自定义 Rx 工厂必须加在 Default 之前。
- `OkHttpCall` 每次 `invoke` 都新建（源码：`new OkHttpCall<>(...)`），
  **同一个 `Call` 不能 `execute()` 两次**，需要重试请 `clone()`。
- **常见坑**：`@Path` 写在 `@Query` 之后会直接抛；用 okhttp3 的 `Response` 当返回类型应为 `Response<T>`。

## 七、参考资料

实际读取的 square/retrofit 主干源码（raw.githubusercontent.com/square/retrofit/trunk）：

- `retrofit/src/main/java/retrofit2/Retrofit.java`
- `retrofit/src/main/java/retrofit2/ServiceMethod.java`、`HttpServiceMethod.java`
- `retrofit/src/main/java/retrofit2/RequestFactory.java`、`Platform.java`

> 口径说明：所有错误消息与常量均抄自上述源码原文，未采用二手博客转述。
