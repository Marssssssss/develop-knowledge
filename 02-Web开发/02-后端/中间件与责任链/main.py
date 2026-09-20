"""中间件与责任链（洋葱模型）的可执行模型：Django 式与 Express 式两套分派。

对照的官方文档：

Django（docs.djangoproject.com "Middleware"）：
* 「You can think of it like an onion: each middleware class is a layer that wraps the
  view... 请求阶段按 MIDDLEWARE 的定义顺序 top-down，响应阶段按**逆序**回穿。」
* 「If one of the layers decides to short-circuit and return a response without ever
  calling its get_response, none of the layers of the onion inside that layer
  (including the view) will see the request or the response. The response will only
  return through the same layers that the request passed in through.」
* ``process_view``：view 之前**正序**调用，返回 None 继续；返回 HttpResponse 则**不调 view**，
  但 response 中间件照常应用。
* ``process_exception``：view 抛异常时调用；**响应阶段一律逆序，process_exception 也包括在内**；
  「If an exception middleware returns a response, the process_exception methods of the
  middleware classes above that middleware won't be called at all.」
* ``__init__`` 里抛 ``MiddlewareNotUsed`` → 该中间件从链中被移除。

Express（expressjs.com "Using middleware"）：
* 错误处理中间件「always takes four arguments... Even if you don't need to use the next
  object, you must specify it to maintain the signature. Otherwise... will fail to handle
  errors.」—— **靠形参个数（arity 4）识别，不靠命名**。
* ``next('route')`` 「skip the remaining middleware functions in a router middleware stack
  and pass control to the next route」，且「will work only in middleware functions that were
  loaded by using the app.METHOD() or router.METHOD() functions」。
* ``app.use(path, m1, m2)`` 会在挂载点形成 middleware sub-stack。
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Django 式：__call__(request, get_response) + 三个可选钩子
# ---------------------------------------------------------------------------


class MiddlewareNotUsed(Exception):
    """__init__ 里抛出即表示该中间件被摘除（Django 语义）。"""


class DjangoMiddleware:
    """基类：子类按需实现 process_view / process_exception / process_template_response。"""

    def __init__(self, name: str, trace: List[str], short_circuit: bool = False,
                 unused: bool = False):
        if unused:
            raise MiddlewareNotUsed(name)
        self.name = name
        self.trace = trace
        self.short_circuit = short_circuit
        self.response = None

    # --- 三个可选钩子，默认不实现（对应 Django 的 hasattr 检查）---
    def process_view(self, request, view):            # noqa: D102
        return None

    def process_exception(self, request, exc):        # noqa: D102
        return None

    def process_template_response(self, request, response):  # noqa: D102
        return response

    def __call__(self, request, get_response):
        self.trace.append(f"{self.name}.in")
        if self.short_circuit:
            # 短路：不调用 get_response，内层与 view 都看不到这个请求
            self.trace.append(f"{self.name}.short")
            resp = {"by": self.name}
            return self._finish(request, resp)
        resp = get_response(request)
        return self._finish(request, resp)

    def _finish(self, request, resp):
        self.trace.append(f"{self.name}.out")
        return resp


class ViewException(Exception):
    pass


def django_dispatch(middlewares: Sequence[DjangoMiddleware],
                    view: Callable[[Any], Any],
                    request: Any = None,
                    trace: Optional[List[str]] = None) -> Any:
    """按 Django 的分派规则跑一遍，返回响应对象。"""
    trace = trace if trace is not None else []

    def call_layer(i: int):
        if i == len(middlewares):
            # --- process_view 阶段：正序 ---
            for m in middlewares:
                r = m.process_view(request, view)
                if r is not None:
                    trace.append(f"{m.name}.process_view.value")
                    return r
            # --- view ---
            try:
                resp = view(request)
            except ViewException as exc:
                # --- process_exception 阶段：逆序 ---
                for m in reversed(middlewares):
                    r = m.process_exception(request, exc)
                    if r is not None:
                        trace.append(f"{m.name}.process_exception.value")
                        return r
                raise
            # --- process_template_response：响应阶段逆序，仅当响应是「模板响应」时 ---
            if isinstance(resp, dict) and resp.get("template"):
                for m in reversed(middlewares):
                    resp = m.process_template_response(request, resp)
            return resp
        return middlewares[i](request, lambda req: call_layer(i + 1))

    return call_layer(0)


# ---------------------------------------------------------------------------
# Express 式：靠形参个数区分普通中间件与错误处理中间件
# ---------------------------------------------------------------------------
class RouteDone(Exception):
    """next('route') 的内部信号。"""

    def __init__(self):
        super().__init__("route")


class Request:
    def __init__(self, path: str = "/"):
        self.path = path
        self.bag: dict = {}


class Response:
    def __init__(self):
        self.status = 200
        self.body = None
        self.sent = False

    def send(self, body):
        self.body = body
        self.sent = True


def _arity(fn: Callable) -> int:
    """返回位置形参数量（用于识别 4 参错误处理中间件）。"""
    return fn.__code__.co_argcount


class ExpressApp:
    """极简 Express：一组 (method, path, stack) 组成的 layer。"""

    def __init__(self):
        self.layers: List[dict] = []   # {"method":..., "path":..., "stack":[...]}
        self.trace: List[str] = []

    def _add(self, method: str, path: str, fns: Sequence[Callable]):
        self.layers.append({"method": method, "path": path, "stack": list(fns)})
        return self

    def use(self, path: str, *fns: Callable):
        return self._add("USE", path, fns)

    def get(self, path: str, *fns: Callable):
        return self._add("GET", path, fns)

    def handle(self, req: Request) -> Response:
        res = Response()
        for layer in self.layers:
            if layer["method"] != "USE" and layer["path"] != req.path:
                continue
            if layer["method"] == "USE" and not req.path.startswith(layer["path"]):
                continue
            # next('route') 只在 METHOD 加载的栈里有效，USE 栈里的调用要被忽略
            loaded_by = "USE" if layer["method"] == "USE" else "METHOD"
            try:
                self._run_stack(layer["stack"], req, res, None, loaded_by)
            except RouteDone:
                continue          # next('route') → 交给下一个 route
            if res.sent:
                return res
        if not res.sent:
            res.status = 404
        return res

    def _run_stack(self, stack: Sequence[Callable], req: Request, res: Response,
                   err: Optional[Exception], loaded_by: str) -> None:
        # 用 enumerate 取下标：同一个函数对象可能在栈里出现多次，index() 会找错
        for idx, fn in enumerate(stack):
            n = _arity(fn)
            if err is not None:
                # 只在错误处理中间件（arity == 4）上停；普通中间件被整体跳过
                if n == 4:
                    fn(err, req, res, self._next(stack, idx, req, res, loaded_by))
                    return
                continue
            if n == 4:
                continue          # 错误处理器不会在 happy path 上被调用
            if n == 3:
                fn(req, res, self._next(stack, idx, req, res, loaded_by))
            else:
                fn(req, res)
            if res.sent:
                return
        if err is not None:
            res.status = 500
            res.send("unhandled")

    def _next(self, stack: Sequence[Callable], idx: int, req: Request, res: Response,
              loaded_by: str) -> Callable:
        def nxt(arg=None):
            if arg == "route":
                if loaded_by != "METHOD":
                    # next('route') 只在 app.METHOD/router.METHOD 加载的栈里有效
                    return
                raise RouteDone()
            rest = stack[idx + 1:]
            if arg is None:
                self._run_stack(rest, req, res, None, loaded_by)
            else:
                self._run_stack(rest, req, res, arg, loaded_by)
        return nxt
