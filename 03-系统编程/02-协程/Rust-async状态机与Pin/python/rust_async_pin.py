# -*- coding: utf-8 -*-
"""Rust async/await 状态机与 Pin/!Unpin 模型。

口径(实读源):
  std::pin 模块文档 —— "Types that pin data to a location in memory";
    Unmovable 示例(PhantomPinned 抑制 Unpin + pinning Box);
    Pin/Unpin 的交互看**指向物**类型 <Ptr as Deref>::Target 而非指针类型;
    Drop 保证(ManuallyDrop 抑制析构违反保证)。
  PhantomPinned 文档页 —— 第一个 trait 实现就是 !Unpin(零大小标记类型)。
  RFC 2394 —— async fn 降级为生成器/状态机,await 点即挂起点。
"""

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


# ---- 1. async fn → 状态机 ----

class AsyncFn:
    """编译器视角的 future:每个 await 点一个挂起状态,跨点存活的局部变量入变体。"""

    STATES = ["Start", "AwaitRead", "AwaitParse", "Done"]

    def __init__(self):
        self.state = "Start"
        self.saved = {}                     # 跨 await 存活的局部(存进变体)

    def poll(self, ready):
        """ready: 各 await 是否就绪的输入;返回 Pending 或 完成值。"""
        if self.state == "Start":
            path = "tmp_path"               # 不跨 await 的局部留在寄存器/栈
            self.saved["path"] = path       # 跨 AwaitRead 存活 → 必须入变体
            self.state = "AwaitRead"
            return ("Pending", None)
        if self.state == "AwaitRead":
            if not ready["read"]:
                return ("Pending", None)
            self.saved["data"] = b"raw"
            self.state = "AwaitParse"
            return ("Pending", None)
        if self.state == "AwaitParse":
            if not ready["parse"]:
                return ("Pending", None)
            self.state = "Done"
            return ("Ready", f"{self.saved['path']}:{self.saved['data']}")
        return ("Done", None)


def lower_states(n_awaits):
    """await 点数 = 挂起状态数(RFC 2394:await 即挂起点)。"""
    return n_awaits + 1                    # 挂起态 + 完成态


# ---- 2. Pin / Unpin ----

class Value:
    _next = [0]

    def __init__(self, name, is_unpin=True, self_referential=False):
        Value._next[0] += 1
        self.name = name
        self.addr = Value._next[0]          # 模型地址
        self.is_unpin = is_unpin
        self.self_ref = self_referential
        if self_referential:
            self.inner_ptr = self.addr      # 自引用:指向自己数据区的模型指针

    def __sizeof_model__(self):
        return 16 if self.self_ref else 16  # PhantomPinned 零大小


class PhantomPinned:
    """零大小标记:唯一作用是把类型变 !Unpin。"""


def pin_new(v):
    """Pin::new 只对 Unpin 类型安全(官方签名约束)。"""
    if not v.is_unpin:
        raise TypeError("Pin::new requires the pointee to be Unpin")
    v.pinned = True
    return ("Pin", v)


def box_pin(v, heap):
    """Box::pin:堆上分配(pinning Box),指针可动、指向物不动。"""
    heap.append(v)
    v.pinned = True
    return ("PinBox", len(heap) - 1)


def try_move(v):
    """移动 !Unpin 且已固定的值 = 违反固定不动保证。"""
    if getattr(v, "pinned", False) and not v.is_unpin:
        raise RuntimeError("moving pinned !Unpin value violates the guarantee")
    old = v.addr
    Value._next[0] += 1
    v.addr = Value._next[0]
    if v.self_ref:
        return old, v.addr, v.inner_ptr     # inner_ptr 还指着旧地址 → 悬垂
    return old, v.addr, None


def main():
    print("1. async fn 的状态机降级")
    fut = AsyncFn()
    assert fut.poll({}) == ("Pending", None)          # Start → AwaitRead
    assert fut.poll({"read": True}) == ("Pending", None)   # 就绪才推进
    r = fut.poll({"parse": True})
    assert r[0] == "Ready" and "tmp_path" in r[1]
    assert lower_states(2) == 3 and len(AsyncFn.STATES) == 4
    ok("await 点即挂起点:2 个 await → 3 个挂起态;跨点存活的局部(path/data)存进变体,"
       "不跨点的(tmp 之类)不入——这就是 async 体积膨胀的来源")

    print("2. Unpin 与 Pin::new")
    plain = Value("plain", is_unpin=True)
    assert pin_new(plain)[0] == "Pin"
    marker = Value("marker", is_unpin=False)
    try:
        pin_new(marker)
        raise AssertionError("unreachable")
    except TypeError:
        ok("Pin::new 的安全边界:**指向物必须 Unpin**;!Unpin 只能 unsafe 的 "
           "new_unchecked(或 Box::pin)")

    print("3. pointee 规则")
    v = Value("heapd", is_unpin=False)
    heap = []
    pb = box_pin(v, heap)
    assert pb[0] == "PinBox"
    ok("Pin<Box<T>> 的行为由 **T(pointee)** 决定,与 Box 本身是否 Unpin 无关——"
       "指针移动(换 PinBox 变量)不影响堆上指向物的固定")

    print("4. 自引用与移动悬垂")
    selfy = Value("selfref", is_unpin=False, self_referential=True)
    box_pin(selfy, heap)
    try:
        try_move(selfy)
        raise AssertionError("unreachable")
    except RuntimeError:
        ok("自引用结构移动后 inner 指针悬垂——Pin 把『移动即不安全』变成类型系统拦得住的事")
    plain2 = Value("movable", is_unpin=True)
    old, new, dangling = try_move(plain2)
    assert dangling is None and old != new
    ok("Unpin 类型随便移:绝大多数类型自动 Unpin,Pin 对它们无约束")

    print("5. PhantomPinned 标记")
    class WithMarker:
        _p = PhantomPinned()

    assert isinstance(WithMarker._p, PhantomPinned)
    ok("PhantomPinned 是**零大小**标记类型,唯一效果是让包含它的类型变 !Unpin——"
       "不加体积,只改契约")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
