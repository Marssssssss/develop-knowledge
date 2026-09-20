# 中间件与责任链（洋葱模型）

「中间件」是所有后端框架的共同抽象，但各家在**短路语义**、**异常流向**、**身份识别**三个点上分歧很大——而这三点恰好是线上「响应头丢了」「异常没被捕获」「中间件没生效」的根因。本 demo 把 Django 式与 Express 式两套分派做成可执行模型，逐条对照官方文档。

## 一、Django：洋葱

官方文档的原话：

> You can think of it like an onion: each middleware class is a "layer" that wraps the view, which is in the core of the onion.

请求阶段按 `MIDDLEWARE` 的定义顺序 **top-down**，响应阶段按**逆序**回穿：

```text
A.in → B.in → C.in → view → C.out → B.out → A.out
```

**短路的代价**（文档原文）：

> If one of the layers decides to short-circuit and return a response without ever calling its `get_response`, none of the layers of the onion inside that layer (including the view) will see the request or the response. The response will only return through the same layers that the request passed in through.

实测轨迹（B 短路）：

```text
A.in → B.in → B.short → B.out → A.out      # view 从未执行，但 A.out 照常
```

这条直接解释了「为什么加了缓存中间件之后，跨域头 / 安全头没了」——缓存层短路在它们**内侧**，它们的响应阶段代码根本没机会跑。所以 `SecurityMiddleware`、`CsrfViewMiddleware` 这类必须**靠外**。

## 二、三个额外钩子的顺序

| 钩子 | 时机 | 顺序 | 返回非 None 的后果 |
| --- | --- | --- | --- |
| `process_view` | view 之前 | **正序** | 跳过 view，但响应中间件照常应用 |
| `process_exception` | view 抛异常时 | **逆序** | 「the process_exception methods of the middleware classes above that middleware won't be called at all」 |
| `process_template_response` | view 结束、响应有 `render()` 时 | **逆序** | 替换 response 对象 |

`process_exception` 的逆序 + 短路是最反直觉的：A、B、C 三层里 B 返回了响应，则 **A 的 `process_exception` 一次都不会被调用**（实测轨迹里 `A.process_exception` 完全缺席）。这意味着「最外层做统一异常转换」在 Django 里是**不可靠**的——内层的异常中间件会先把异常吃掉。

`process_view` 返回响应时，view 不执行，但**响应仍会回穿所有层**（文档：「it'll apply response middleware to that HttpResponse and return the result」）。

## 三、启动期摘除：`MiddlewareNotUsed`

`__init__` 里抛 `MiddlewareNotUsed`，Django 会把该中间件从链上移除。注意 `__init__` 只在 **web server 启动时调用一次**，而 `__call__` 每请求一次——所以「按配置开关某个中间件」必须放在 `__init__`，放在 `__call__` 里等于每请求都做一次判断，且中间件仍在链上。

## 四、Express：靠形参个数区分错误处理器

```js
app.use((err, req, res, next) => { ... });   // 4 个形参 = 错误处理器
app.use((req, res, next) => { ... });        // 3 个形参 = 普通中间件
```

文档的警告：

> Error-handling middleware always takes four arguments. You must provide four arguments to identify it as an error-handling middleware function. **Even if you don't need to use the `next` object, you must specify it to maintain the signature.** Otherwise, the `next` object will be interpreted as regular middleware and will fail to handle errors.

这是**按 arity 分派**，不是按名字。实测：定义了 `(err, req, res)` 三参函数，它在错误路径上被当成普通中间件**整体跳过**，错误一路无人处理 → 500 `unhandled`。少写一个 `next` 就静默失效，这在压缩/转译后尤其危险（某些压缩器会删掉未使用的尾部形参）。

`next(err)` 的语义：**跳过后面所有普通中间件**，直接找下一个四参处理器。

## 五、`next('route')` 只在 METHOD 栈里生效

> `next('route')` will work only in middleware functions that were loaded by using the `app.METHOD()` or `router.METHOD()` functions.

对照实测：

| 加载方式 | `next('route')` 的结果 |
| --- | --- |
| `app.get(path, m1, m2)` | 跳过栈内剩余的 `m2`，落到**下一个 route** |
| `app.use(path, m1, m2)` | **不生效**，仍在本栈内继续 |

这个差异是「我写了 `next('route')` 为什么没跳」的经典答案——栈是 `use` 挂的。

## 六、两种模型的本质差异

| | Django | Express |
| --- | --- | --- |
| 链的构造 | 配置里的字符串列表，启动期一次性包裹 | 运行时 `use`/`get` 逐个 push |
| 错误身份 | 靠方法名 `process_exception` | 靠**形参个数（4）** |
| 短路后响应是否回穿外层 | 是（回穿请求进来时经过的层） | 取决于有没有调 `next` |
| 跳过本栈剩余 | 无（只能短路返回响应） | `next('route')` |

## 七、运行方式

```bash
python selfcheck_middleware.py     # 28 项断言
```

## 八、关键代码

- `main.py` — `django_dispatch()`（`process_view` 正序 / `process_exception` 逆序 / 模板响应逆序）、`ExpressApp`（arity 分派 + `next('route')` 的 `loaded_by` 判定）
- `middleware.go` — 同构 Go 实现：用 `Handler`（3 参）与 `ErrHandler`（4 参）**两个类型**在编译期钉死「几参」，说明静态语言里不存在「靠命名识别错误处理器」这件事
- `selfcheck_middleware.py` — 断言调用轨迹的精确序列与三种跳过的差异集

## 九、注意事项与常见坑

1. **中间件顺序是语义的一部分，不是风格问题**。`AuthenticationMiddleware` 必须排在 `SessionMiddleware` 之后，因为它把用户存在 session 里。
2. **短路会让内层所有响应阶段代码失效**。做「响应头统一注入」的中间件一定要排在所有可能短路的层（缓存、鉴权、限流）**之外**。
3. **Django 的 `process_exception` 逆序**：外层做统一异常处理会被内层截胡。
4. **Express 错误处理器的第四个参数不能省**，否则静默降级成普通中间件。
5. **`next('route')` 在 `use` 栈里无效**，别指望它跳路由。
6. 在 Django 中间件里**不要在 view 之前读 `request.POST`**——文档明确警告这会锁死 upload handlers，让后续 view 无法修改上传处理器（`CsrfViewMiddleware` 是有意的例外）。
7. Go 的 `http.Handler` 链式包装没有「错误处理器」概念，错误只能在每一层里自己 `recover` 或返回，跨层错误传播要自建约定。

## 十、参考资料

- Django — Middleware — <https://docs.djangoproject.com/en/5.1/topics/http/middleware/>（onion 比喻、`process_view` / `process_exception` / `process_template_response`、`MiddlewareNotUsed`、`MIDDLEWARE` 顺序）
- Express — Using middleware — <https://expressjs.com/en/guide/using-middleware.html>（application/router-level、error-handling 四参数、`next('route')`、middleware sub-stack）
