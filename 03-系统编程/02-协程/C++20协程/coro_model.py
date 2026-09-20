"""C++20 无栈协程的可执行模型。

依据（本轮实读 cppreference《Coroutines》）：
  * *"Coroutines are stackless: they suspend execution by returning to the caller, and the
    data that is required to resume execution is stored separately from the stack."*
  * 协程状态是*动态分配*的（除非分配被优化掉），里面含 promise、参数副本、
    当前挂起点表示、以及局部变量；按引用传入的参数*保持为引用*，可能悬垂。
  * 启动：operator new → 拷贝参数 → 构造 promise → `co_await promise.initial_suspend()`
    —— *"Typical Promise types either return std::suspend_always, for lazily-started
    coroutines, or std::suspend_never, for eagerly-started coroutines."*
  * `co_await`：`await_ready()` 为 false 时调 `await_suspend(handle)`：
      - 返回 void → 控制立刻回到调用者/resumer；
      - 返回 bool：true 同上；false 表示「已安排好」→ **立即恢复**；
      - 返回 coroutine_handle → 恢复那个协程（对称转移，可链式）；
      - 抛异常 → 被捕获、协程恢复、立即重抛。
    无论是否真的挂起，`await_resume()` 都会被调用，其结果就是整个表达式的值；
    恢复点就在 `await_resume()` 之前。
  * *"the coroutine is fully suspended before entering awaiter.await_suspend()"*，
    所以句柄可以在 await_suspend 里被交给别的线程并**在 await_suspend 返回前**就被恢复。
  * `co_return`：销毁局部变量（逆序）→ `co_await promise.final_suspend()`；
    *"It's undefined behavior to resume a coroutine from this point."*

模型口径：
  * 协程体用 Python 生成器表达，`yield` 出来的就是 awaiter；
  * 对称转移按「尾调用」实现（不增加调用深度），普通 `resume()` 按递归实现（增加深度）；
  * 协程状态用对象表示，显式 `destroy()` 才释放，未释放即泄漏（可计数断言）。
"""


class CoroutineError(Exception):
    pass


class CoroutineHandle:
    def __init__(self, coro):
        self.coro = coro

    def done(self):
        return self.coro.finished

    def resume(self):
        return self.coro.resume()

    def destroy(self):
        self.coro.destroy()


class Awaiter:
    """awaiter 三件套：await_ready / await_suspend / await_resume。"""

    def __init__(self, ready=False, suspend_result=None, value=None, name="awaiter",
                 suspend_cb=None):
        self.ready = ready
        self.suspend_result = suspend_result   # None=void / True / False / CoroutineHandle
        self.value = value
        self.name = name
        self.suspend_cb = suspend_cb           # 反例用：在 await_suspend 里直接 resume 别人
        self.suspend_calls = 0
        self.resume_calls = 0

    def await_ready(self):
        return self.ready

    def await_suspend(self, handle):
        self.suspend_calls += 1
        if self.suspend_cb is not None:
            return self.suspend_cb(handle)
        return self.suspend_result

    def await_resume(self):
        self.resume_calls += 1
        return self.value


class Scheduler:
    """只用来记账：分配/泄漏、调用深度、恢复次数。"""

    def __init__(self):
        self.allocations = 0
        self.live = []
        self.depth = 0
        self.max_depth = 0
        self.resumes = 0


class Coroutine:
    def __init__(self, body, sched, lazy=True, byref=None, final_suspend=True,
                 params=None):
        self.sched = sched
        self.lazy = lazy                 # initial_suspend: suspend_always
        self.final_suspend = final_suspend  # suspend_always → 需手工 destroy
        self.started = False
        self.finished = False
        self.suspended_at = None
        self.suspended_before_await_suspend = False
        self.exception = None
        self.result = None
        self.destroyed = False
        # 参数：按值的是副本，按引用的是**引用**（可能悬垂）
        self.params = dict(params or {})
        self.byref = byref
        self.handle = CoroutineHandle(self)
        self._gen = body(self)
        sched.allocations += 1
        sched.live.append(self)

    # -------- 生命周期 --------
    def initial_suspend(self):
        """suspend_always = 惰性启动：构造完不跑；suspend_never = 立即跑。"""
        if not self.lazy:
            self.resume()

    def destroy(self):
        if self.destroyed:
            raise CoroutineError("double destroy")
        self.destroyed = True
        if self in self.sched.live:
            self.sched.live.remove(self)

    def _finish(self, value=None):
        self.finished = True
        self.result = value
        if not self.final_suspend:
            self.destroy()               # suspend_never：状态自行销毁

    # -------- 恢复 --------
    def resume(self):
        if self.destroyed:
            raise CoroutineError("resume on destroyed coroutine (UB)")
        if self.finished:
            raise CoroutineError("resume past final_suspend (UB)")
        sched = self.sched
        sched.depth += 1
        sched.max_depth = max(sched.max_depth, sched.depth)
        sched.resumes += 1
        cur = self
        try:
            while True:
                if not cur.started:
                    cur.started = True
                try:
                    aw = next(cur._gen)
                except StopIteration as stop:
                    cur._finish(getattr(stop, "value", None))
                    break
                except Exception as exc:                     # unhandled_exception
                    cur.exception = exc
                    cur._finish()
                    break
                if aw.await_ready():                          # 短路：不挂起
                    val = aw.await_resume()
                    cur._send(val)
                    continue
                cur.suspended_at = aw.name
                cur.suspended_before_await_suspend = True
                r = aw.await_suspend(cur.handle)
                if isinstance(r, CoroutineHandle):            # 对称转移：尾调用，不加深度
                    cur = r.coro
                    continue
                if r is False:                                # 已安排好 → 立即恢复
                    val = aw.await_resume()
                    cur._send(val)
                    continue
                break                                          # void / True：挂起
        finally:
            sched.depth -= 1
        return cur

    def _send(self, val):
        """把 await_resume() 的结果送回协程体（对应「恢复点在 await_resume 之前」）。"""
        try:
            self._gen.send(val)
        except StopIteration as stop:
            self._finish(getattr(stop, "value", None))

    def byref_alive(self):
        return bool(self.byref and self.byref.get("alive", False))


def build_chain(sched, n, naive=False):
    """搭一条 n 节的协程链，返回第一个协程。

    - 对称转移：await_suspend 返回下一个协程的句柄 —— 由调用者/resumer 尾调用恢复，
      栈深恒为 1；
    - naive：在 await_suspend 里直接 resume 下一个 —— 恢复嵌套在当帧里，栈深线性增长。
    """
    def body_factory(i, nxt):
        def body(coro):
            if nxt is None:
                return i
            if naive:
                yield Awaiter(name="naive%d" % i, suspend_cb=lambda h: nxt.resume())
            else:
                yield Awaiter(name="hop%d" % i, suspend_result=nxt.handle)
            return i
        return body

    coros = [None] * n
    for i in range(n - 1, -1, -1):
        nxt = coros[i + 1] if i + 1 < n else None
        coros[i] = Coroutine(body_factory(i, nxt), sched, lazy=True)
    return coros
