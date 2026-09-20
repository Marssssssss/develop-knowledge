"""中间件与责任链 —— 自检。

断言策略：断言**调用轨迹的精确序列**（而不是「某个中间件被调用了」这种恒真式），
以及**短路 / 异常 / next('route') 三种跳过的差异集**。
"""

from main import (DjangoMiddleware, ExpressApp, MiddlewareNotUsed, Request,
                  ViewException, _arity, django_dispatch)

PASS = [0]


def ok(name: str, cond: bool, extra: str = "") -> None:
    if cond:
        PASS[0] += 1
        print(f"  PASS  {name}{(' -> ' + extra) if extra else ''}")
    else:
        raise AssertionError(f"FAIL  {name}{(' -> ' + extra) if extra else ''}")


class M(DjangoMiddleware):
    """可配置的 Django 中间件。"""

    def __init__(self, name, trace, short_circuit=False, unused=False,
                 pv=None, pe=None, templ=False):
        super().__init__(name, trace, short_circuit=short_circuit, unused=unused)
        self.pv, self.pe, self.templ = pv, pe, templ

    def process_view(self, request, view):
        if self.pv is None:
            return None                      # 未配置的钩子不产生轨迹
        self.trace.append(f"{self.name}.process_view")
        return self.pv

    def process_exception(self, request, exc):
        self.trace.append(f"{self.name}.process_exception")
        return self.pe

    def process_template_response(self, request, response):
        if not self.templ:
            return response
        self.trace.append(f"{self.name}.process_template_response")
        response.setdefault("templ_by", []).append(self.name)
        return response


def make(trace, names=("A", "B"), **kw):
    return [M(n, trace, **kw) for n in names]


print("== 1. 正常洋葱：请求正序、响应逆序 ==")
tr = []


def view(_):
    tr.append("view")
    return {"by": "view"}


resp = django_dispatch(make(tr), view)
ok("轨迹精确为 A.in B.in view B.out A.out",
   tr == ["A.in", "B.in", "view", "B.out", "A.out"], str(tr))
ok("响应来自 view", resp["by"] == "view")

print("== 2. 短路：内层与 view 都看不到这个请求 ==")
tr = []
mws = make(tr)
mws[1].short_circuit = True
resp = django_dispatch(mws, view)
ok("短路后轨迹不含 view",
   tr == ["A.in", "B.in", "B.short", "B.out", "A.out"], str(tr))
ok("响应由短路层产生", resp["by"] == "B")
ok("响应只回穿请求进来时经过的层（A 仍在）", "A.out" in tr)

print("== 3. process_view 返回响应：跳过 view，但响应中间件照常 ==")
tr = []
mws = make(tr)
mws[0].pv = {"by": "pv"}
resp = django_dispatch(mws, view)
ok("view 未执行", "view" not in tr, str(tr))
ok("A.process_view 被调用且 B.process_view 未调用（A 返回了值）",
   "A.process_view" in tr and "B.process_view" not in tr, str(tr))
ok("响应仍回穿 B 与 A", tr[-2:] == ["B.out", "A.out"], str(tr[-2:]))
ok("响应来自 process_view", resp["by"] == "pv")

print("== 4. process_exception：逆序调用，命中后上层不再被调用 ==")
tr = []


def bad_view(_):
    raise ViewException("boom")


mws = make(tr, names=("A", "B", "C"))
mws[1].pe = {"by": "B"}
resp = django_dispatch(mws, bad_view)
ok("逆序：先 C 再 B",
   tr.index("C.process_exception") < tr.index("B.process_exception"), str(tr))
ok("B 返回响应后 A 的 process_exception 完全不被调用",
   "A.process_exception" not in tr, str(tr))
ok("响应来自 B", resp["by"] == "B")

print("== 5. 无人处理异常时继续上抛 ==")
tr = []
raised = False
try:
    django_dispatch(make(tr), bad_view)
except ViewException:
    raised = True
ok("异常未被任何中间件吞掉则向外抛出", raised)
ok("抛出前 A、B 的 process_exception 都被逆序问过",
   tr == ["A.in", "B.in", "B.process_exception", "A.process_exception"], str(tr))

print("== 6. process_template_response 在响应阶段逆序 ==")
tr = []


def tpl_view(_):
    tr.append("view")
    return {"template": True, "by": "view"}


mws = make(tr)
mws[0].templ = mws[1].templ = True
resp = django_dispatch(mws, tpl_view)
ok("模板响应钩子逆序执行（B 先于 A）",
   tr == ["A.in", "B.in", "view", "B.process_template_response",
          "A.process_template_response", "B.out", "A.out"], str(tr))

print("== 7. MiddlewareNotUsed：启动期摘除 ==")
tr = []
unused_raised = False
try:
    M("X", tr, unused=True)
except MiddlewareNotUsed:
    unused_raised = True
ok("__init__ 抛 MiddlewareNotUsed", unused_raised)
ok("被摘除的中间件不出现在链上（构造即失败，无法登记）",
   all(m.name != "X" for m in make(tr)))

print("== 8. Express：happy path 与 next(err) 的差异集 ==")


def mk(trace, name, err=None, send=None, route=False):
    def fn(req, res, nxt=None):
        trace.append(name)
        if err is not None:
            return nxt(err)
        if route:
            return nxt("route")
        if send is not None:
            res.send(send)
            return
        if nxt is not None:
            return nxt()
    return fn


trace = []
app = ExpressApp()
app.get("/x", mk(trace, "a"), mk(trace, "b", send="b"))
r = app.handle(Request("/x"))
ok("happy path 顺序 a b", trace == ["a", "b"], str(trace))
ok("响应体为 b", r.body == "b" and r.status == 200)

trace = []


def err_handler(err, req, res, nxt):       # arity 4 → 错误处理器
    trace.append("err")
    res.status = 500
    res.send("handled")


app = ExpressApp()
app.get("/x", mk(trace, "a", err=ValueError("x")), mk(trace, "b", send="b"),
        err_handler)
r = app.handle(Request("/x"))
ok("next(err) 后普通中间件 b 被整体跳过", "b" not in trace, str(trace))
ok("错误落到四参处理器", trace == ["a", "err"], str(trace))
ok("错误处理器给出 500", r.status == 500 and r.body == "handled")

print("== 9. arity 决定身份：三参「错误处理」不生效 ==")
trace = []


def eh3(err, req, res):                     # 只有 3 个形参
    trace.append("eh3")
    res.send("nope")


app = ExpressApp()
app.get("/x", mk(trace, "a", err=ValueError("x")), mk(trace, "b", send="b"), eh3)
r = app.handle(Request("/x"))
ok("_arity(eh3) == 3", _arity(eh3) == 3, str(_arity(eh3)))
ok("三参函数被当普通中间件，在错误路径上被跳过", "eh3" not in trace, str(trace))
ok("错误无人处理 → 500 unhandled", r.status == 500 and r.body == "unhandled",
   f"{r.status} {r.body}")

print("== 10. next('route')：METHOD 栈生效，USE 栈无效 ==")
trace = []
app = ExpressApp()
app.get("/x", mk(trace, "m1", route=True), mk(trace, "m2", send="m2"))
app.get("/x", mk(trace, "m3", send="m3"))
r = app.handle(Request("/x"))
ok("METHOD 栈里 next('route') 跳过栈内剩余的 m2", "m2" not in trace, str(trace))
ok("并命中下一个 route 的 m3", r.body == "m3" and trace == ["m1", "m3"], str(trace))

trace = []
app = ExpressApp()
app.use("/x", mk(trace, "u1", route=True), mk(trace, "u2", send="u2"))
app.get("/x", mk(trace, "u3", send="u3"))
r = app.handle(Request("/x"))
ok("USE 栈里 next('route') 不跳路由（响应仍由本栈 u1 侧给出）",
   r.body == "u2" and "u3" not in trace, f"{r.body} {trace}")

print(f"\n全部 {PASS[0]} 项断言通过")
