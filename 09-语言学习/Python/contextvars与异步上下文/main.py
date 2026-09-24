# -*- coding: utf-8 -*-
"""
Python · contextvars 与异步上下文传播(PEP 567)

ContextVar/Token/Context 三个原语逐项对拍:get 三级回退、token 一次性、
copy_context 快照隔离、Context 的 Mapping 接口与重入规则、线程边界,
以及 asyncio 两个快照时机(Task 创建时 / call_soon 调度时,对照本机源码)。

参考(实读):
  - https://peps.python.org/pep-0567/                      (设计动机与语义)
  - https://docs.python.org/3/library/contextvars.html     (ContextVar/Token/Context)
  - 本机 CPython 3.12 Lib/asyncio/tasks.py:122             (Task.__init__ 里 copy_context)
  - 本机 CPython 3.12 Lib/asyncio/events.py:34             (Handle.__init__ 里 copy_context)
"""

import asyncio
import contextvars
import threading

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


def demo_get_fallback():
    print("1. get 的三级回退")
    v = contextvars.ContextVar("v")
    try:
        v.get()
    except LookupError:
        ok("无方法参也无默认 → LookupError")
    assert v.get("method-default") == "method-default"
    ok("方法参数优先")
    d = contextvars.ContextVar("d", default="var-default")
    assert d.get("method-default") == "method-default"
    assert d.get() == "var-default"
    ok("方法参 > 声明默认 > LookupError,严格按此顺序")


def demo_token():
    print("2. Token 一次性与归属校验")
    v = contextvars.ContextVar("v")
    t = v.set(1)
    assert t.old_value is contextvars.Token.MISSING
    assert t.var is v
    v.reset(t)
    try:
        v.get()
    except LookupError:
        ok("reset 后回到『未设置』,old_value 哨兵是 Token.MISSING")
    try:
        v.reset(t)
    except RuntimeError as e:
        assert "already been used once" in str(e)
        ok("同一 token 第二次 reset → RuntimeError")
    other = contextvars.ContextVar("other")
    ot = other.set(9)
    try:
        v.reset(ot)
    except ValueError as e:
        assert "different ContextVar" in str(e)
        ok("拿别的变量的 token 来 reset → ValueError")
    assert not hasattr(t, "__enter__")
    ok("3.12 的 token 还不能 with(当上下文管理器是 3.14 才加的)——升级代码注意版本")


def demo_snapshot():
    print("3. copy_context 快照与隔离")
    v = contextvars.ContextVar("v")
    v.set("outer-0")
    ctx = contextvars.copy_context()
    v.set("outer-1")                       # 快照之后外部再改
    assert ctx.run(v.get) == "outer-0"
    ok("快照之后外部的修改进不了快照(shallow copy 语义)")
    v.set("outer-2")
    ctx.run(v.set, "inside")               # 在快照里改
    assert ctx[v] == "inside" and v.get() == "outer-2"
    ok("ctx.run 里的 set 记在快照上,出来即被『弹出』——这就是异步上下文的隔离边界")


def demo_mapping_and_reentry():
    print("4. Context 是 Mapping + 重入规则")
    a = contextvars.ContextVar("a")
    b = contextvars.ContextVar("b")
    ctx = contextvars.Context()          # 直接造一个空上下文,不受前面 demo 影响
    ctx.run(a.set, 1)
    ctx.run(b.set, 2)
    assert len(ctx) == 2 and a in ctx and ctx[a] == 1
    assert {v.name for v in ctx} == {"a", "b"}
    assert ctx.get(a) == 1 and ctx.get(a, 99) == 1
    ok("len/in/[]/迭代/keys 全齐:Context 实现 Mapping,值挂在上下文而非函数作用域")
    missing = contextvars.ContextVar("missing")
    try:
        ctx[missing]
    except KeyError:
        ok("未设置的变量取下标 → KeyError(get 可带默认则返回默认)")
    ctx.run(lambda: None); ctx.run(lambda: None)
    ok("顺序多次 run 合法(退出后可重入)")

    def nested():
        try:
            ctx.run(lambda: None)
        except RuntimeError as e:
            assert "already entered" in str(e)
            ok("自己内部再 run 自己 → RuntimeError(同一 Context 不能并发嵌套进入)")

    ctx.run(nested)


def demo_threads():
    print("5. 线程边界:不继承、不串扰")
    v = contextvars.ContextVar("v")
    v.set("main")
    seen = {}

    def th():
        seen["get"] = v.get("<fresh>")

    t = threading.Thread(target=th)
    t.start(); t.join()
    assert seen["get"] == "<fresh>"
    ok("新线程拿到的是全新空上下文,主线程的值不继承(每线程独立上下文栈)")

    def th2():
        v.set("from-thread")

    t2 = threading.Thread(target=th2)
    t2.start(); t2.join()
    assert v.get() == "main"
    ok("子线程里的 set 也不回传主线程")


def demo_vs_threading_local():
    print("6. 对照 threading.local:逻辑上下文 vs 物理线程")
    v = contextvars.ContextVar("v", default="init")
    tl = threading.local()

    def logical(tag, ctx):
        def body():
            v.set(tag)
            tl.val = tag
            return (v.get(), tl.val)
        return ctx.run(body) if ctx else body()

    c1 = contextvars.copy_context()
    c2 = contextvars.copy_context()
    logical("req-A", c1)
    logical("req-B", c2)
    a = logical("req-A", c1)          # 再次进入 c1
    assert a == ("req-A", "req-A")
    ok("Context 可反复进入:逻辑上下文像『可保存的执行环境』,跨挂起/恢复不丢")

    # 同一物理线程顺序跑两个逻辑请求:threading.local 的旧值会泄漏
    tl.val = "req-A"

    def fake_handler():
        return tl.val if hasattr(tl, "val") else "<clean>"

    r1 = contextvars.copy_context().run(fake_handler)
    assert r1 == "req-A"
    ok("threading.local 绑定物理线程:上一请求留下的值会被下一请求读到(池化场景经典串扰)")
    vv = contextvars.ContextVar("vv", default="<clean>")
    assert vv.get() == "<clean>"
    ok("ContextVar 的值挂在上下文快照上:新请求新快照,天然干净")


async def _async_probe():
    cv = contextvars.ContextVar("cv", default="init")

    async def child():
        cv.set("in-task")
        await asyncio.sleep(0)
        return cv.get()

    cv.set("before-task")
    inside = await asyncio.create_task(child())
    assert inside == "in-task" and cv.get() == "before-task"
    ok("Task 在创建时 copy_context:任务里看到的=创建瞬间,任务里的 set 全被含在任务上下文")

    seen = []
    loop = asyncio.get_running_loop()
    loop.call_soon(lambda: seen.append(cv.get()))
    cv.set("changed-after-schedule")
    await asyncio.sleep(0)
    assert seen == ["before-task"]
    ok("call_soon 在调度时 copy_context(Handle.__init__):调度之后再改值,回调看到的仍是旧值")


def demo_asyncio():
    print("7. asyncio 的两个快照时机")
    asyncio.run(_async_probe())


def main():
    demo_get_fallback()
    demo_token()
    demo_snapshot()
    demo_mapping_and_reentry()
    demo_threads()
    demo_vs_threading_local()
    demo_asyncio()
    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
