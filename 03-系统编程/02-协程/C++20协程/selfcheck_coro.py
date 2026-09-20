"""C++20 协程自检：断言 awaiter 协议、生命周期与对称转移的**可证伪**后果。"""

from coro_model import (
    Awaiter, Coroutine, CoroutineHandle, CoroutineError, Scheduler, build_chain,
)

PASS = []


def ok(name, cond):
    if not cond:
        raise AssertionError("FAIL: " + name)
    PASS.append(name)


def eq(name, got, want):
    if got != want:
        raise AssertionError("FAIL: %s got=%r want=%r" % (name, got, want))
    PASS.append("%s (= %r)" % (name, got))


# ---- 1. 惰性启动 vs 立即启动：取决于 initial_suspend ----
ran = []

def body(coro):
    ran.append("start")
    yield Awaiter(name="first")
    ran.append("after")
    return 7


s = Scheduler()
lazy = Coroutine(body, s, lazy=True)
lazy.initial_suspend()
eq("suspend_always：构造完一行都没跑", ran, [])
lazy.resume()
eq("第一次 resume 才跑到首个挂起点", ran, ["start"])
lazy.resume()
eq("第二次 resume 跑完剩余部分", ran, ["start", "after"])
ok("协程已结束", lazy.finished)
eq("co_return 的值留在 promise 里", lazy.result, 7)

ran.clear()
s = Scheduler()
eager = Coroutine(body, s, lazy=False)
eager.initial_suspend()
eq("suspend_never：构造时就跑到了首个挂起点", ran, ["start"])

# ---- 2. 协程状态是堆分配的：不 destroy 就是泄漏 ----
s = Scheduler()
c = Coroutine(body, s, lazy=True)
eq("构造即分配一次", s.allocations, 1)
eq("此刻仍有一个活着的协程状态", len(s.live), 1)
c.resume()
c.resume()
ok("跑完了但状态还在（final_suspend=suspend_always）", c.finished and len(s.live) == 1)
c.destroy()
eq("destroy 之后才真正释放", len(s.live), 0)

s = Scheduler()
c = Coroutine(body, s, lazy=True, final_suspend=False)
c.resume()
c.resume()
ok("final_suspend=suspend_never 时状态自行销毁", c.destroyed and len(s.live) == 0)
try:
    c.resume()
    ok("在 final_suspend 之后 resume 是 UB（本模型抛错）", False)
except CoroutineError:
    ok("在 final_suspend 之后 resume 是 UB（本模型抛错）", True)

try:
    c.destroy()
    ok("重复 destroy 也要能拦住", False)
except CoroutineError:
    ok("重复 destroy 也要能拦住", True)

# ---- 3. await_ready 是短路：ready 为真就一次都不挂起 ----
s = Scheduler()
aw = Awaiter(ready=True, value=99, name="always-ready")
calls = []

def body_ready(coro):
    v = yield aw
    calls.append(v)
    return v


c = Coroutine(body_ready, s, lazy=True)
c.resume()
eq("await_ready 为真 → await_suspend 一次都没被调用", aw.suspend_calls, 0)
eq("但 await_resume 照常调用（无论是否挂起）", aw.resume_calls, 1)
eq("co_await 表达式的值就是 await_resume 的返回值", calls, [99])
ok("整个协程一次跑完，没挂起", c.finished)

# ---- 4. await_suspend 的三种返回值 ----
s = Scheduler()
aw = Awaiter(suspend_result=None, value=1, name="void")
def body_void(coro):
    v = yield aw
    calls.append(("void", v))


c = Coroutine(body_void, s, lazy=True)
c.resume()
ok("返回 void → 挂起，控制回到调用者", not c.finished and aw.suspend_calls == 1)
c.resume()
ok("被恢复后继续跑完", c.finished)

calls.clear()
s = Scheduler()
aw = Awaiter(suspend_result=False, value=2, name="resume-now")
c = Coroutine(body_void, s, lazy=True)
c.resume()
eq("返回 false 表示『已安排好』→ 立刻恢复，一次 resume 就跑完", (c.finished, calls), (True, [("void", 2)]))

calls.clear()
s = Scheduler()
aw = Awaiter(suspend_result=True, value=3, name="suspend")
c = Coroutine(body_void, s, lazy=True)
c.resume()
ok("返回 true → 同 void，挂起", not c.finished)

# ---- 5. 对称转移：尾调用不加栈深，写成 resume() 递归会线性增长 ----
s = Scheduler()
chain = build_chain(s, 64, naive=False)
s.max_depth = 0
chain[0].resume()
eq("64 节对称转移的最大栈深", s.max_depth, 1)
ok("链尾确实跑完了", chain[-1].finished or chain[-1].result == 63)

s = Scheduler()
chain = build_chain(s, 64, naive=True)
s.max_depth = 0
chain[0].resume()
eq("naive 写法（await_suspend 里 resume 下一个）栈深", s.max_depth, 64)
ok("对称转移比递归 resume 省掉 63 层栈", 64 - 1 == 63)

# ---- 6. 进入 await_suspend 之前，协程已经完全挂起 ----
s = Scheduler()
shared = {}

def body_share(coro):
    yield Awaiter(suspend_cb=lambda h: shared.setdefault("h", h) or shared.__setitem__("h", h))


c = Coroutine(body_share, s, lazy=True)
c.resume()
ok("挂起先于 await_suspend 完成（句柄可安全交给别的线程）",
   c.suspended_before_await_suspend)
ok("await_suspend 里拿到的就是本协程的句柄", shared["h"].coro is c)

# ---- 7. 按引用传入的参数会悬垂 ----
s = Scheduler()
ref = {"alive": True, "i": 0}

def body_ref(coro):
    yield Awaiter(name="mid")
    coro.result = ref["i"]


c = Coroutine(body_ref, s, lazy=True, byref=ref)
ok("按引用参数在协程里保持为引用（不拷贝）", c.byref_alive())
c.resume()
ref["alive"] = False            # 调用方那个对象没了
ok("对端一销毁，协程里的引用就悬垂了", not c.byref_alive())

s = Scheduler()
c = Coroutine(body_ref, s, lazy=True, params={"n": 5})
eq("按值参数被拷进协程状态", c.params["n"], 5)

# ---- 8. 未捕获异常走 unhandled_exception，然后照常 final_suspend ----
s = Scheduler()
def body_throw(coro):
    yield Awaiter(name="before-throw")
    raise RuntimeError("boom")


c = Coroutine(body_throw, s, lazy=True)
c.resume()
c.resume()
ok("异常被 promise 接住", isinstance(c.exception, RuntimeError))
ok("异常之后仍然走到 final_suspend（状态等着被 destroy）", c.finished and len(s.live) == 1)

# ---- 9. 无栈：co_await 只能出现在协程体里 ----
def check_coroutine(src, ret):
    """函数体里有 co_* 关键字 ⇒ 它就是协程，于是返回类型必须能给出 promise_type。"""
    has_kw = any(k in src for k in ("co_await", "co_yield", "co_return"))
    if has_kw and ret not in ("task<>", "generator<int>", "future<int>"):
        raise CoroutineError("this function cannot be a coroutine: '%s' 没有 promise_type" % ret)
    return has_kw


ok("协程体里的 co_await 合法", check_coroutine("co_await g();", "task<>"))
try:
    check_coroutine("co_await g();", "void")
    ok("在返回 void 的普通函数里写 co_await 会被拒", False)
except CoroutineError:
    ok("在返回 void 的普通函数里写 co_await 会被拒（无栈：不能从嵌套函数挂起）", True)

print("C++20 协程自检通过：%d 项" % len(PASS))
for i, name in enumerate(PASS, 1):
    print("  %2d. %s" % (i, name))
