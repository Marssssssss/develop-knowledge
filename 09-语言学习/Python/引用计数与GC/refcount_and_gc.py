#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CPython 引用计数 + 分代循环 GC + weakref 教学 demo。

  §1 引用计数:sys.getrefcount 的 "+1" 与 immortal 对象
  §2 归零即释放:不需要 GC 参与,__del__ 立即调用
  §3 引用循环:引用计数失效的地方,循环 GC 登场(用 weakref 观测)
  §4 分代与阈值:gc.get_threshold / get_count / get_stats / callbacks
  §5 PEP 442 安全终结:循环中的终结器、复活、恰好调用一次
  §6 weakref 家族:ref / proxy / WeakValueDictionary / WeakSet / finalize / __slots__

来源:docs.python.org/3/library/gc.html、/library/weakref.html、/library/sys.html、
PEP 442、PEP 683。输出中的数值为本机 Python 3.13.14 实测,不同版本可能不同。
运行:python refcount_and_gc.py
"""

import gc
import sys
import weakref

SEP = "=" * 72


def header(title):
    print(f"\n{SEP}\n{title}\n{SEP}")


# ---------------------------------------------------------------------------
# §1 引用计数
# ---------------------------------------------------------------------------
def demo_refcount():
    header("[1] 引用计数:getrefcount 的 +1、小整数/驻留字符串的 immortal 计数")
    obj = []
    print(f"  只有局部名 obj 持有时 getrefcount(obj) = {sys.getrefcount(obj)}"
          f"(实际 1 个 + 传参临时 1 个)")
    alias = obj
    print(f"  再加一个别名 alias -> {sys.getrefcount(obj)}")
    del alias
    print(f"  删除别名 -> {sys.getrefcount(obj)}")
    print("  sys 文档:'The count returned is generally one higher than you might expect,"
          " because it includes the (temporary) reference as an argument to getrefcount()'")
    print(f"  getrefcount(1) = {sys.getrefcount(1)}(0x{sys.getrefcount(1):x});"
          f"getrefcount(None) = {sys.getrefcount(None)}")
    print("  sys 文档(3.12 起):immortal 对象 have a very high refcount,0xFFFFFFFF 即 PEP 683 标记;"
          "故 do not rely on the returned value to be accurate, other than a value of 0 or 1")


# ---------------------------------------------------------------------------
# §2 归零即释放
# ---------------------------------------------------------------------------
class Named:
    alive = 0

    def __init__(self, name):
        self.name = name
        Named.alive += 1

    def __del__(self):
        Named.alive -= 1
        print(f"    __del__ 触发:{self.name}(当前存活 {Named.alive})")


def demo_immediate_free():
    header("[2] 引用计数归零即释放:__del__ 在 del 当场执行,GC 不参与")
    a = Named("A")
    print(f"  del a 之前 alive = {Named.alive}")
    del a
    print(f"  del a 之后 alive = {Named.alive}(说明释放是同步的,没有等 GC)")

    box = [Named("B")]
    print("  把 B 放进 list:引用计数 +1,del 局部名不会释放")
    b = box[0]
    del b
    print(f"  仅删除其中一个引用后 alive = {Named.alive};再清空 list:")
    box.clear()
    print(f"    alive = {Named.alive}")


# ---------------------------------------------------------------------------
# §3 引用循环
# ---------------------------------------------------------------------------
class Node:
    def __init__(self, name):
        self.name = name
        self.peer = None

    def __repr__(self):
        return f"<Node {self.name}>"


def demo_cycle():
    header("[3] 引用循环:引用计数永远不归零 -> 必须靠循环 GC")
    gc.disable()                                  # 关掉自动回收,让现象可复现
    a, b = Node("a"), Node("b")
    a.peer, b.peer = b, a                         # a <-> b,refcount 各为 2
    watch = weakref.ref(a)                        # 弱引用:不阻止回收,可用来观测
    print(f"  构造 a<->b 循环后,a 的弱引用可取到对象吗?{watch() is not None}")
    del a, b                                      # 外部强引用消失,但循环内部互指
    print(f"  删掉外部引用后:{watch()!r}(refcount 仍为 1,不会自动释放)")
    gc.collect()
    print(f"  gc.collect() 之后:{watch()!r}(循环 GC 打破了环)")
    gc.enable()
    print("  gc 文档:'Since the collector supplements the reference counting already used"
          " in Python, you can disable the collector if you are sure your program does not"
          " create reference cycles.'")


# ---------------------------------------------------------------------------
# §4 分代与阈值
# ---------------------------------------------------------------------------
def _on_collect(phase, info, sink):
    if phase == "stop":
        sink.append((info["generation"], info["collected"], info["uncollectable"]))


def demo_generations():
    header("[4] 分代与阈值:threshold / count / stats / callbacks")
    cb = []
    gc.callbacks.append(lambda phase, info: _on_collect(phase, info, cb))
    thr = gc.get_threshold()
    print(f"  gc.get_threshold() = {thr};gc.get_count() = {gc.get_count()}")
    print("  gc 文档:'New objects are placed in the youngest generation (generation 0)'、"
          "'If an object survives a collection it is moved into the next older generation'、"
          "'Since generation 2 is the oldest generation, objects in that generation remain"
          " there after a collection.'")
    print(f"  触发条件:分配数 - 释放数 > threshold0({thr[0]});gen0 被检查次数 > threshold1"
          f"({thr[1]}) 时连带检查 gen1;threshold2 语义随版本变动(3.14 曾忽略、3.14.5 恢复)")
    print("  注意:700/10/10 是旧版本默认值,3.13 实测第 0 代阈值为 2000 —— 别硬编码,用 gc.get_threshold() 现取")
    before = [s["collections"] for s in gc.get_stats()]
    gc.disable()
    for _ in range(2000):
        cycle = {}
        cycle["me"] = cycle                       # 自引用 dict:典型循环垃圾
    freed0 = gc.collect(0)                        # 只收集第 0 代
    freed = gc.collect()                          # 全量(含最老的 generation 2)
    gc.enable()
    after = [s["collections"] for s in gc.get_stats()]
    print(f"  制造 2000 个自引用 dict -> gc.collect(0) 回收 {freed0} 个;"
          f"随后 gc.collect() 又回收 {freed} 个")
    print(f"  三代 collections 计数 {before} -> {after}")
    print(f"  gc.callbacks 收到的 (generation, collected, uncollectable) 序列:{cb}"
          "(generation=0 是代数收集,generation=2 是全量收集)")
    print(f"  gc.is_tracked({{}}) = {gc.is_tracked({})};gc.is_tracked([]) = "
          f"{gc.is_tracked([])};gc.is_tracked(0) = {gc.is_tracked(0)}"
          "(原子类型不参与追踪)")
    gc.callbacks.pop()


# ---------------------------------------------------------------------------
# §5 PEP 442 安全终结
# ---------------------------------------------------------------------------
class Resurrector:
    calls = 0
    registry = []

    def __init__(self, name):
        self.name = name
        self.peer = None

    def __del__(self):
        Resurrector.calls += 1
        Resurrector.registry.append(self)          # 在终结器里"复活"自己
        print(f"    __del__ {self.name}:第 {Resurrector.calls} 次终结")


def demo_finalization():
    header("[5] PEP 442 安全终结:循环里的 __del__、复活、终结恰好一次")
    gc.disable()
    a, b = Resurrector("x"), Resurrector("y")
    a.peer, b.peer = b, a                          # 循环 + 终结器(3.4 之前会泄漏进 gc.garbage)
    keep = weakref.ref(a)
    del a, b
    print("  循环 + __del__:触发收集")
    gc.collect()
    print(f"  终结器调用次数 = {Resurrector.calls};registry 中被复活的对象 = "
          f"{[o.name for o in Resurrector.registry]}")
    if Resurrector.registry:
        print(f"  gc.is_finalized(复活对象) = {gc.is_finalized(Resurrector.registry[0])}"
              "(已打上终结标记,不会再被终结第二次)")
    print(f"  弱引用在 PEP 442 的步骤①就被清空,故 keep() = {keep()!r}"
          " —— 它无法用来判断对象是否被复活")
    print("  PEP 442 顺序:①清弱引用并回调 ②调用全部终结器 ③重新判断隔离性(被复活则放弃本次收集) ④tp_clear ⑤结束")
    print("  PEP 442:'an object's finalizer is always called exactly once, even if it was"
          " resurrected afterwards' —— 再次丢引用并收集:")
    Resurrector.registry.clear()
    gc.collect()
    print(f"    终结器累计调用次数仍为 {Resurrector.calls}(没有第二次);"
          f"registry 清空后对象被真正释放,weakref 仍为 {keep()!r}")
    print(f"  gc.garbage 当前长度 = {len(gc.garbage)}")
    gc.enable()


# ---------------------------------------------------------------------------
# §6 weakref
# ---------------------------------------------------------------------------
class Slotted:
    __slots__ = ("x",)                             # 没有 '__weakref__' 槽 -> 不可弱引用


class SlottedWeak:
    __slots__ = ("x", "__weakref__")               # 显式加槽后即可弱引用


class Cache:
    """用 WeakValueDictionary 做"大对象缓存":值不被缓存本身保活。"""

    def __init__(self):
        self.data = weakref.WeakValueDictionary()
        self.hits = 0

    def get(self, key, factory):
        obj = self.data.get(key)
        if obj is None:
            obj = self.data[key] = factory()
        else:
            self.hits += 1
        return obj


def demo_weakref():
    header("[6] weakref:ref / proxy / WeakValueDictionary / finalize / __slots__")
    order = []

    class Target:
        def __init__(self, name):
            self.name = name

        def __del__(self):
            pass

    t = Target("w1")
    r1 = weakref.ref(t, lambda ref: order.append("r1"))
    r2 = weakref.ref(t, lambda ref: order.append("r2"))
    print(f"  weakref.ref(t)() is t -> {r1() is t};两个弱引用"
          f" getweakrefcount = {weakref.getweakrefcount(t)}")
    del t
    print(f"  t 释放后 r1() = {r1()};回调顺序(文档:'from the most recently registered"
          f" callback to the oldest')= {order}")

    proxy_owner = Target("w2")
    proxy = weakref.proxy(proxy_owner)
    print(f"  proxy.name = {proxy.name}(免解引用);proxy 不可哈希:"
          f"{_hashable(proxy)}")
    del proxy_owner
    try:
        proxy.name
    except ReferenceError as exc:
        print(f"  referent 死后访问 proxy 属性 -> ReferenceError: {exc}")

    cache = Cache()
    big = cache.get("k", lambda: Target("cached"))
    print(f"  首次 get 拿到 {big.name};弱值缓存条目数 = {len(cache.data)},hits = {cache.hits}")
    cache.get("k", lambda: Target("cached"))
    print(f"  第二次 get(值仍活着)-> hits = {cache.hits}")
    del big
    print(f"  删掉唯一强引用后:条目数 = {len(cache.data)}(WeakValueDictionary 自动剔除)")
    big = cache.get("k", lambda: Target("cached"))
    print(f"  再取一次会重建对象 {big.name};hits 仍为 {cache.hits}")

    gate = Target("g")
    fired = []
    fin = weakref.finalize(gate, lambda: fired.append("done"))
    print(f"  finalize alive = {fin.alive};peek() = {fin.peek()[0]}")
    del gate
    print(f"  对象回收后:alive = {fin.alive};回调结果 = {fired}"
          "(finalize 保证存活到对象被回收,比 __del__ 更适合清理第三方对象)")

    try:
        weakref.ref(Slotted())
    except TypeError as exc:
        print(f"  __slots__ 未含 '__weakref__':{type(exc).__name__}: {exc}")
    print(f"  加上 '__weakref__' 槽后 weakref.ref(SlottedWeak()) 成功:"
          f"{weakref.ref(SlottedWeak()) is not None}")
    print("  weakref 文档:类实例/Python 函数(非 C 函数)/实例方法/set 等支持弱引用;"
          "list/dict 需子类化才支持,tuple/int 连子类化也不行")


def _hashable(obj):
    try:
        hash(obj)
        return "可哈希"
    except TypeError:
        return "不可哈希(TypeError)"


def main():
    print(f"Python {sys.version}")
    demo_refcount()
    demo_immediate_free()
    demo_cycle()
    demo_generations()
    demo_finalization()
    demo_weakref()
    print("\n全部章节执行完毕。")


if __name__ == "__main__":
    main()
