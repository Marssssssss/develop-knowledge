"""Retrofit 模型自检：判据来自 square/retrofit 主干源码原文。"""

from request_factory import (
    Annotation, MethodSpec, Param, RequestFactory, RetrofitError,
    BODY_METHODS, NON_BODY_METHODS,
)
from main import (
    Retrofit, ServiceInterface, ServiceMethod, CallAdapterFactory, ConverterFactory,
    resolve_platform, PLATFORM_DALVIK, PLATFORM_JVM, PLATFORM_ROBOVM, SUSPEND_ADAPTER,
)

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, label
    PASS += 1


def raises(fn, label, needle=None):
    global PASS
    try:
        fn()
    except RetrofitError as e:
        if needle is not None:
            assert needle in str(e), "%s: 消息里没有 %r，实得 %r" % (label, needle, str(e))
        PASS += 1
    except Exception as e:  # noqa: BLE001
        raise AssertionError("%s: 期望 RetrofitError，实得 %s(%s)" % (label, type(e).__name__, e))
    else:
        raise AssertionError("%s: 期望抛 RetrofitError，但没有抛" % label)


def get(annos=(), params=(), **kw):
    return MethodSpec("m", list(annos), list(params), **kw)


def msg(fn):
    try:
        fn()
    except RetrofitError as e:
        return str(e)
    return "<no error>"


# ---- 1. HTTP method 必填与唯一 ----
raises(lambda: RequestFactory(get()), "无 HTTP method 注解", "HTTP method annotation is required")
raises(lambda: RequestFactory(get([Annotation("GET", "/a"), Annotation("POST", "/b")])),
       "两个 HTTP method 注解", "Only one HTTP method is allowed")
ok(set(BODY_METHODS) == {"POST", "PUT", "PATCH"}, "带 body 的方法是 POST/PUT/PATCH（源码 hasBody 实参）")
ok(set(NON_BODY_METHODS) == {"GET", "HEAD", "DELETE", "OPTIONS"}, "其余四种无 body")

# ---- 2. 编码注解只用于带 body 的方法 ----
raises(lambda: RequestFactory(get([Annotation("GET", "/a"), Annotation("Multipart")])),
       "GET 上用 @Multipart", "Multipart can only be specified on HTTP methods with request body")
raises(lambda: RequestFactory(get([Annotation("GET", "/a"), Annotation("FormUrlEncoded")])),
       "GET 上用 @FormUrlEncoded", "FormUrlEncoded can only be specified on HTTP methods with")
raises(lambda: RequestFactory(get([Annotation("POST", "/a"),
                                   Annotation("Multipart"), Annotation("FormUrlEncoded")])),
       "两种编码注解并存", "Only one encoding annotation is allowed")

# ---- 3. @Body 与编码注解的内容要求 ----
raises(lambda: RequestFactory(get([Annotation("GET", "/a")], [Param(0, [Annotation("Body")])])),
       "无 body 方法带 @Body", "Non-body HTTP method cannot contain @Body")
raises(lambda: RequestFactory(get([Annotation("POST", "/a"), Annotation("FormUrlEncoded")])),
       "FormUrlEncoded 无 @Field", "Form-encoded method must contain at least one @Field")
raises(lambda: RequestFactory(get([Annotation("POST", "/a"), Annotation("Multipart")])),
       "Multipart 无 @Part", "Multipart method must contain at least one @Part")

# ---- 4. URL 与 @Url 参数 ----
raises(lambda: RequestFactory(get([Annotation("GET", "/a"), Annotation("POST", "/b")])),
       "重复 method", "Only one HTTP method is allowed")
raises(lambda: RequestFactory(get([Annotation("GET", "")])),
       "空 URL 且无 @Url 参数", "Missing either @GET URL or @Url parameter")
ok(RequestFactory(get([Annotation("GET", None)],
                      [Param(0, [Annotation("Url")], type_name="String")])).has_url_param,
   "无 URL 但给了 @Url 参数 → 合法")
raises(lambda: RequestFactory(get([Annotation("GET", "/a")],
                                  [Param(0, [Annotation("Url")], type_name="String")])),
       "@Url 与已给 URL 冲突", "@Url cannot be used with @GET URL")
raises(lambda: RequestFactory(get([Annotation("GET", None)],
                                  [Param(0, [Annotation("Url")], type_name="Integer")])),
       "@Url 类型不合法", "@Url must be okhttp3.HttpUrl, String, java.net.URI, or android.net.Uri type")
raises(lambda: RequestFactory(get([Annotation("GET", None)],
                                  [Param(0, [Annotation("Url")], type_name="String"),
                                   Param(1, [Annotation("Url")], type_name="String")])),
       "多个 @Url", "Multiple @Url method annotations found")

# ---- 5. 参数顺序约束 ----
raises(lambda: RequestFactory(get([Annotation("GET", "/a")],
                                  [Param(0, [Annotation("Query", "q")]),
                                   Param(1, [Annotation("Path", "id")])])),
       "@Path 出现在 @Query 之后", "A @Path parameter must not come after a @Query")
raises(lambda: RequestFactory(get([Annotation("GET", None)],
                                  [Param(0, [Annotation("Query", "q")]),
                                   Param(1, [Annotation("Url")], type_name="String")])),
       "@Url 出现在 @Query 之后", "A @Url parameter must not come after a @Query")
raises(lambda: RequestFactory(get([Annotation("GET", None)],
                                  [Param(0, [Annotation("Url")], type_name="String"),
                                   Param(1, [Annotation("Path", "id")])])),
       "@Path 与 @Url 混用", "@Path parameters may not be used with @Url")

# ---- 6. path 占位符与 query 段 ----
raises(lambda: RequestFactory(get([Annotation("GET", "/a/{1bad}")])),
       "占位符名不符合正则", "@Path parameter name must match")
raises(lambda: RequestFactory(get([Annotation("GET", "/a?q={v}")])),
       "URL 的 query 段含 replace block", "must not have replace block")
ok(RequestFactory(get([Annotation("GET", "/a/{id}")])).relative_url == "/a/{id}",
   "合法占位符原样保留")
raises(lambda: RequestFactory(get([Annotation("GET", "/a/{id}")],
                                  [Param(0, [Annotation("Path", "other")])])),
       "@Path 名字必须出现在 URL 里", "does not contain")

# ---- 7. @Headers ----
raises(lambda: RequestFactory(get([Annotation("GET", "/a"), Annotation("Headers", [])])),
       "@Headers 为空", "@Headers annotation is empty")
raises(lambda: RequestFactory(get([Annotation("GET", "/a"),
                                   Annotation("Headers", ["NoColon"])])),
       "@Headers 缺冒号", 'must be in the form "Name: Value"')
ok(RequestFactory(get([Annotation("GET", "/a"), Annotation("Headers", ["A: b"])])).http_method == "GET",
   "合法 @Headers 通过")

# ---- 8. 参数注解数量 ----
raises(lambda: RequestFactory(get([Annotation("GET", "/a")],
                                  [Param(0, [Annotation("Query", "q"), Annotation("Path", "id")])])),
       "一个参数多个注解", "Multiple Retrofit annotations found, only one allowed")
raises(lambda: RequestFactory(get([Annotation("GET", "/a")], [Param(0, [])])),
       "参数无注解", "No Retrofit annotation found")

# ---- 9. create() 的接口校验 ----
r = Retrofit([CallAdapterFactory("Default", ("Call",))], [ConverterFactory("Gson", {"X"})])
raises(lambda: r.create(ServiceInterface("S", [], is_interface=False)),
       "非接口", "API declarations must be interfaces.")
raises(lambda: r.create(ServiceInterface("S", [], type_parameters=("T",))),
       "接口带类型参数", "Type parameters are unsupported on S")
parent = ServiceInterface("P", [], type_parameters=("T",))
raises(lambda: r.create(ServiceInterface("S", [], super_interfaces=(parent,))),
       "父接口带类型参数", "which is an interface of S")

# ---- 10. 动态代理分发 ----
default_m = MethodSpec("dm", [Annotation("GET", "/a")], [], is_default=True, return_type="Call<X>")
api_m = MethodSpec("list", [Annotation("GET", "/a")], [], return_type="Call<X>")
svc = ServiceInterface("Api", [default_m, api_m])
proxy = r.create(svc)
ok(proxy.invoke("dm") == ("default", "dm", None), "default 方法走 invokeDefaultMethod 而不是建 Call")
kind, k, factory, conv = proxy.invoke("list")
ok(kind == "call" and k == "CallAdapted", "普通方法走 CallAdapted")
ok(factory == "Default" and conv == "Gson", "适配器与转换器被正确选中")
ok(len(r.service_method_cache) == 1, "只有非 default 方法进 serviceMethodCache")
obj_m = MethodSpec("toString", [], [], declaring_class="java.lang.Object")
svc.methods["toString"] = obj_m
ok(proxy.invoke("toString") == ("object", "toString"), "Object 上的方法直接走自身实现，不建 ServiceMethod")
ok(len(r.service_method_cache) == 1, "Object 方法不进 serviceMethodCache")

# ---- 11. nextCallAdapter 的 skipPast 语义 ----
f1 = CallAdapterFactory("Rx", ("Observable",))
f2 = CallAdapterFactory("Default", ("Call",))
r2 = Retrofit([f1, f2], [ConverterFactory("Gson", {"X"})])
ok(r2.call_adapter("Call<X>", []).factory_name == "Default", "顺序查找，第一个非 null 胜出")
ok(r2.next_call_adapter(f1, "Call<X>", []).factory_name == "Default", "skipPast=Rx ⇒ 从 Rx 之后开始")
raises(lambda: r2.next_call_adapter(f2, "Call<X>", []), "skipPast 是最后一个 ⇒ 找不到")
ok("Skipped" in msg(lambda: r2.next_call_adapter(f2, "Call<X>", [])),
   "异常消息里带 Skipped 列表")
ok("Tried" in msg(lambda: r2.next_call_adapter(None, "Flowable<X>", [])),
   "异常消息里带 Tried 列表")
ok(r2.next_call_adapter(object(), "Call<X>", []).factory_name == "Default",
   "skipPast 不在列表里 ⇒ indexOf=-1 ⇒ 从头找（不会吞掉全部工厂）")

# ---- 12. ServiceMethod 返回类型前置校验 ----
raises(lambda: ServiceMethod.parse_annotations(r2, svc,
        MethodSpec("w", [Annotation("GET", "/a")], [], return_type="void")),
       "返回 void", "Service methods cannot return void.")
raises(lambda: ServiceMethod.parse_annotations(r2, svc,
        MethodSpec("t", [Annotation("GET", "/a")], [], return_type="Call<T>")),
       "返回类型含类型变量", "must not include a type variable or wildcard")

# ---- 13. responseType 校验与 HEAD ----
class FakeAdapter(CallAdapterFactory):
    def __init__(self, name, response_type):
        super().__init__(name, ("Call",))
        self._rt = response_type

    def get(self, return_type, annotations, retrofit):
        from main import CallAdapter
        return CallAdapter(self.name, self._rt)


r3 = Retrofit([FakeAdapter("A", "okhttp3.Response")], [ConverterFactory("G", {"X"})])
raises(lambda: ServiceMethod.parse_annotations(r3, svc,
        MethodSpec("m", [Annotation("GET", "/a")], [], return_type="Call<X>")),
       "responseType 是 okhttp3.Response", "Did you mean ResponseBody?")

r4 = Retrofit([FakeAdapter("A", "Response")], [ConverterFactory("G", {"X"})])
raises(lambda: ServiceMethod.parse_annotations(r4, svc,
        MethodSpec("m", [Annotation("GET", "/a")], [], return_type="Call<X>")),
       "responseType 是裸 Response", "Response must include generic type")

r5 = Retrofit([CallAdapterFactory("D", ("Call",))], [ConverterFactory("G", {"X", "Void"})])
raises(lambda: ServiceMethod.parse_annotations(r5, svc,
        MethodSpec("m", [Annotation("HEAD", "/a")], [], return_type="Call<X>")),
       "HEAD 返回非 Void", "HEAD method must use Void or Unit as response type")
ok(ServiceMethod.parse_annotations(r5, svc,
    MethodSpec("m", [Annotation("HEAD", "/a")], [], return_type="Call<Void>")).kind == "CallAdapted",
   "HEAD + Void 合法")

# ---- 14. suspend 改写 ----
susp = MethodSpec("s", [Annotation("GET", "/a")], [], return_type="X", is_suspend=True)
sm = ServiceMethod.parse_annotations(r5, svc, susp)
ok(sm.kind == "SuspendForBody", "普通 suspend 走 SuspendForBody")
ok(sm.adapter.response_type == "X", "suspend 的返回类型被包成 Call<X> 再取 responseType")
susp2 = MethodSpec("s", [Annotation("GET", "/a")], [], return_type="Response<X>", is_suspend=True)
ok(ServiceMethod.parse_annotations(r5, svc, susp2).kind == "SuspendForResponse",
   "Response<T> 的 suspend 走 SuspendForResponse")
skip_only = CallAdapterFactory("SkipOnly", ("Call",), skip_callback_executor=True)
r6 = Retrofit([skip_only], [ConverterFactory("G", {"X"})])
ok(r6.call_adapter("Call<X>", [SUSPEND_ADAPTER]).factory_name == "SkipOnly",
   "suspend 会补 SkipCallbackExecutor 注解，只匹配感知它的工厂")
raises(lambda: r6.call_adapter("Call<X>", []), "没有该注解时不匹配")

# ---- 15. Platform 分支 ----
ok(resolve_platform(PLATFORM_DALVIK, 24).callback_executor == "AndroidMainExecutor",
   "Dalvik ⇒ 回调投递到 Android 主线程")
ok(resolve_platform(PLATFORM_DALVIK, 21).reflection == "Legacy", "SDK < 24 用旧反射")
ok(resolve_platform(PLATFORM_DALVIK, 24).built_in_factories == "Java8", "SDK ≥ 24 用 Java8 内置工厂")
ok(resolve_platform(PLATFORM_JVM).callback_executor is None, "JVM 上没有默认回调执行器")
ok(resolve_platform(PLATFORM_ROBOVM).callback_executor is None, "RoboVM 上同样没有")

print("PASS %d" % PASS)
