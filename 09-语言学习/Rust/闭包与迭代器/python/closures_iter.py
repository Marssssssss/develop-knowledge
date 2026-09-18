"""闭包 Fn 三 trait 推导 + 迭代器惰性的可运行模型。

对应 The Book ch13-01 / ch13-02 / ch13-04。官方只给了**定性**结论
（"Iterators are one of Rust's zero-cost abstractions" + 一组基准数字），
本模型把它翻译成两个**可计数的结构指标**：元素访问次数、中间分配次数——
不声称等价于汇编级对比。

协议约定（贯穿全文件）：
  visits = next() 的实际调用次数（含最后那次返回 None 的探测）
  allocs = 物化出中间容器的次数（只有 collect 会 +1）
"""

# ---------------------------------------------------------------- 捕获方式

SHARED = "shared-ref"   # &T
MUTABLE = "mut-ref"     # &mut T
OWNED = "move"          # 取得所有权


class Closure:
    """一个闭包 = 名字 + 对捕获变量做了什么。

    ops: [(kind, var)]，kind ∈ {"read", "mutate", "consume"}
    forced_move: 是否写了 `move` 关键字
    """

    def __init__(self, name, ops=(), forced_move=False):
        self.name = name
        self.ops = list(ops)
        self.forced_move = forced_move

    # -- 捕获方式由**闭包体怎么用**决定，而不是由变量本身决定
    def capture_mode(self, var):
        kinds = {k for k, v in self.ops if v == var}
        if self.forced_move or "consume" in kinds:
            return OWNED          # 要把值交出去 → 必须先拿到所有权
        if "mutate" in kinds:
            return MUTABLE
        if "read" in kinds:
            return SHARED
        return None               # 没被用到 → 不捕获

    def captures(self):
        out = {}
        for _k, v in self.ops:
            m = self.capture_mode(v)
            if m is not None:
                out[v] = m
        return out

    # -- trait 推导：官方原文 "in an additive fashion"，Fn ⊂ FnMut ⊂ FnOnce
    def traits(self):
        if any(k == "consume" for k, _ in self.ops):
            return {"FnOnce"}                       # 只能调一次
        if any(k == "mutate" for k, _ in self.ops):
            return {"FnOnce", "FnMut"}
        return {"FnOnce", "FnMut", "Fn"}            # 含「什么都没捕获」

    def satisfies(self, bound):
        return bound in self.traits()


def bind_error(closure, bound, var):
    """模拟 rustc 在 trait bound 不满足时的报错分类。"""
    if closure.satisfies(bound):
        return None
    if bound == "FnMut" and closure.capture_mode(var) == OWNED:
        # error[E0507]: cannot move out of `value`, a captured variable in
        # an `FnMut` closure
        return ("E0507",
                f"cannot move out of `{var}`, a captured variable in an `FnMut` closure")
    return ("E0277", f"expected a closure that implements the `{bound}` trait")


# ---------------------------------------------------------------- 迭代器

class RustIter:
    """Iterator trait 的最小模型：唯一必须实现的是 next(&mut self) -> Option<Item>。

    `consumed` 模拟「消费适配器拿走了迭代器所有权」这件事。
    """

    def __init__(self, items=(), label="iter"):
        self.items = list(items)
        self.pos = 0
        self.label = label
        self.consumed = False
        self.visits = 0
        self.allocs = 0

    def _tick(self):
        if self.consumed:
            raise RuntimeError(f"E0382: use of moved value `{self.label}` (already consumed)")
        self.visits += 1

    def next(self):
        self._tick()
        if self.pos >= len(self.items):
            return None
        v = self.items[self.pos]
        self.pos += 1
        return v

    # -- consuming adapters：拿走 self 的所有权
    def collect(self):
        out = []
        while True:
            v = self.next()
            if v is None:
                break
            out.append(v)
        self.allocs += 1
        self.consumed = True
        return out

    def sum(self):
        total = 0
        while True:
            v = self.next()
            if v is None:
                break
            total += v
        self.consumed = True
        return total

    def for_loop(self, body):
        """`for val in iter` 去糖：loop { match next() { Some(v)=>body(v), None=>break } }"""
        while True:
            v = self.next()
            if v is None:
                break
            body(v)
        self.consumed = True

    # -- iterator adapters（lazy，不消费）
    def map(self, f):
        return MapIter(self, f, label=f"{self.label}.map")

    def filter(self, f):
        return FilterIter(self, f, label=f"{self.label}.filter")

    def take(self, n):
        return TakeIter(self, n, label=f"{self.label}.take")


class Adapter(RustIter):
    """适配器不持有元素，只持有上游；它的 _tick 不额外计 visits。"""

    def __init__(self, src, label):
        super().__init__((), label=label)
        self.src = src

    def _tick(self):
        if self.consumed:
            raise RuntimeError(f"E0382: use of moved value `{self.label}` (already consumed)")


class MapIter(Adapter):
    def __init__(self, src, f, label):
        super().__init__(src, label)
        self.f = f
        self.calls = 0        # 闭包被实际调用的次数 —— 惰性的度量

    def next(self):
        Adapter._tick(self)
        v = self.src.next()
        if v is None:
            return None
        self.calls += 1
        return self.f(v)


class FilterIter(Adapter):
    def __init__(self, src, f, label):
        super().__init__(src, label)
        self.f = f
        self.calls = 0

    def next(self):
        Adapter._tick(self)
        while True:
            v = self.src.next()
            if v is None:
                return None
            self.calls += 1
            if self.f(v):
                return v


class TakeIter(Adapter):
    def __init__(self, src, n, label):
        super().__init__(src, label)
        self.remaining = n
        self.yielded = 0

    def next(self):
        Adapter._tick(self)
        if self.remaining <= 0:
            return None
        self.remaining -= 1
        v = self.src.next()
        if v is None:
            return None
        self.yielded += 1
        return v
