#!/usr/bin/env python3
r"""SwiftUI 状态管理与 Observation 依赖追踪 —— 可执行参考模型 + 自检。

权威依据(实际读过,完整引用见同目录 README 的「参考资料」):
  * Apple — SwiftUI/State:
    · "Use state as the single source of truth for a given value type";
    · "SwiftUI manages the property's storage. When the value changes, SwiftUI
      updates the parts of the view hierarchy that depend on the value.";
    · 子视图要能改必须传 Binding;状态声明为 private;
    · "A State property always instantiates its default value when SwiftUI
      instantiates the view. For this reason, avoid side effects ... .task";
    · Book/BookView 例:"updates each time title changes but not when
      isAvailable changes" —— 只按**读过的属性**失效;
    · 陷阱:"the view will only update when the reference to the object changes
      … will not update if any of the object's published properties change".
  * Apple — Observation:`withObservationTracking(_:onChange:)`"only tracks
    properties read in its apply closure"。

本文件把这些语义落成可运行的最小模型:注册器记录"谁读了谁",失效只通知读过该
属性的视图,并与"粗粒度整对象失效"对照。运行: python3 observation_check.py
"""

from __future__ import annotations

from typing import Callable


# ---------------------------------------------------------------------------
# 1. 注册器:Observation 的 ObservationRegistrar 语义
# ---------------------------------------------------------------------------
class Registrar:
    """记录"谁读了哪些属性";属性变化时只通知读过它的依赖。"""

    def __init__(self, label: str) -> None:
        self.label = label
        self.reading: set[str] | None = None      # 非 None = 正在追踪
        self.deps: dict[str, list[Callable[[], None]]] = {}
        self.notify_count = 0

    # -- apply 闭包求值期间调用:记录本次读到的 key --
    def access(self, key: str) -> None:
        if self.reading is not None:
            self.reading.add(key)

    # -- onChange 回调的登记 --
    def track(self, apply: Callable[[], None], on_change: Callable[[], None]) -> None:
        reads: set[str] = set()
        self.reading = reads
        try:
            apply()
        finally:
            self.reading = None
        for key in reads:
            self.deps.setdefault(key, []).append(on_change)

    def mutation(self, key: str) -> None:
        """属性将被写入前调用:通知读过该 key 的依赖(一次性,通知后清空)。"""
        for cb in self.deps.pop(key, []):
            self.notify_count += 1
            cb()


class Observable:
    """@Observable 的模型:属性读写都过注册器。"""

    def __init__(self, label: str) -> None:
        self.__dict__["_reg"] = Registrar(label)
        self.__dict__["_store"] = {}

    def get(self, key: str):
        self._reg.access(key)
        return self._store.get(key)

    def set(self, key: str, value) -> None:
        self._reg.mutation(key)
        self._store[key] = value


# ---------------------------------------------------------------------------
# 2. 两类失效策略:@Observable(细粒度) vs ObservableObject/@Published(粗粒度)
# ---------------------------------------------------------------------------
class View:
    """视图:body() 求值 → 把读过的属性登记为依赖。"""

    def __init__(self, name: str, reg: Registrar) -> None:
        self.name = name
        self.reg = reg
        self.renders = 0
        self.watched: set[str] = set()

    def body(self) -> None:
        self.renders += 1

    def render(self, read: list[str]) -> None:
        """模拟一次 body 求值:求值期间读到的属性成为本视图的依赖。"""
        self.reg.track(lambda: [self.reg.access(k) for k in read], self.on_change)
        self.watched = set(read)
        self.body()

    def on_change(self) -> None:
        self.body()                                # 失效 → 重新求值


class CoarsePublisher:
    """ObservableObject 的模型:objectWillChange 在**任何**属性变化前广播一次。"""

    def __init__(self) -> None:
        self.observers: list[Callable[[], None]] = []
        self.notify_count = 0

    def subscribe(self, cb: Callable[[], None]) -> None:
        self.observers.append(cb)

    def will_change(self, _key: str) -> None:
        for cb in list(self.observers):
            self.notify_count += 1
            cb()


# ---------------------------------------------------------------------------
# 3. @State 存储模型
# ---------------------------------------------------------------------------
class StateBox:
    """@State 的模型:值存在"视图身份"上,跨 body 求值保留。"""

    def __init__(self) -> None:
        self.store: dict[str, object] = {}
        self.init_count = 0

    def make(self, identity: str, initial: object) -> None:
        self.init_count += 1                       # 默认值每次实例化都会被求值
        if identity not in self.store:
            self.store[identity] = initial
        # 已存在 → 保留旧值(SwiftUI 管理存储,不采用新的默认值)

    def value(self, identity: str) -> object:
        return self.store[identity]

    def set(self, identity: str, v: object) -> None:
        self.store[identity] = v

    def remove(self, identity: str) -> None:
        self.store.pop(identity, None)


class Binding:
    """@Binding:对某个存储槽的可读写投影(`$state`)。"""

    def __init__(self, box: StateBox, identity: str) -> None:
        self.box, self.identity = box, identity

    def get(self) -> object:
        return self.box.value(self.identity)

    def set(self, v: object) -> None:
        self.box.set(self.identity, v)


# ---------------------------------------------------------------------------
# 4. 自检
# ---------------------------------------------------------------------------
PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(label)
    print(("  [PASS] " if ok else "  [FAIL] ") + label + (f"   {detail}" if detail else ""))


def t_tracked_only() -> None:
    """Apple 文档的 Book/BookView 例子:只按读过的属性失效。"""
    print("[1] 细粒度失效:只追踪 apply 闭包里读过的属性")
    book = Observable("Book")
    book.set("title", "A sample book")
    book.set("isAvailable", True)
    title_view = View("BookView(读 title)", book._reg)
    both_view = View("BookView(读 title+isAvailable)", book._reg)
    title_view.render(["title"])
    both_view.render(["title", "isAvailable"])
    before = (title_view.renders, both_view.renders)

    book.set("isAvailable", False)                 # 与 title 无关
    check("改 isAvailable 不触发只读 title 的视图",
          title_view.renders == before[0], f"renders={title_view.renders}")
    check("改 isAvailable 触发读过它的视图",
          both_view.renders == before[1] + 1, f"renders={both_view.renders}")

    book.set("title", "New title")
    check("改 title 触发两个视图(都读过 title)",
          title_view.renders == before[0] + 1 and both_view.renders == before[1] + 2,
          f"{title_view.renders}/{both_view.renders}")


def t_coarse_vs_fine() -> None:
    """粗粒度整对象失效 vs 细粒度:同一场景下的通知次数对比。"""
    print("[2] 对照:ObservableObject 的 objectWillChange(粗粒度)")
    book = Observable("Book")
    book.set("title", "T")
    book.set("isAvailable", True)
    fine_view = View("细粒度(读 title)", book._reg)
    fine_view.render(["title"])

    pub = CoarsePublisher()
    coarse_renders = [0]

    def coarse_render() -> None:
        coarse_renders[0] += 1

    pub.subscribe(coarse_render)
    pub.will_change("title")                        # 视图订阅了对象 → 任何变化都通知
    pub.will_change("isAvailable")
    check("粗粒度:改两个属性收到 2 次通知(即使视图没读 isAvailable)",
          pub.notify_count == 2, f"notify={pub.notify_count}")
    book.set("isAvailable", False)                  # 细粒度:0 次
    check("细粒度:改 isAvailable 收到 0 次通知",
          fine_view.renders == 1 and book._reg.notify_count == 0,
          f"renders={fine_view.renders} notify={book._reg.notify_count}")


def t_one_shot_tracking() -> None:
    """withObservationTracking 的 onChange 是一次性的:要连续追踪必须重新登记。"""
    print("[3] 追踪的一次性(重复追踪的时机由调用方决定)")
    car = Observable("Car")
    car.set("name", "Herbie")
    fires = []

    def apply() -> None:
        _ = car.get("name")

    car._reg.track(apply, lambda: fires.append("changed"))
    car.set("name", "Lightning")
    check("第一次修改触发 onChange", len(fires) == 1, f"fires={len(fires)}")
    car.set("name", "Sally")
    check("未重新登记时第二次修改不再触发(一次性)",
          len(fires) == 1, f"fires={len(fires)}")
    car._reg.track(apply, lambda: fires.append("changed"))
    car.set("name", "Doc")
    check("重新登记后再次触发", len(fires) == 2, f"fires={len(fires)}")


def t_state_lifecycle() -> None:
    """@State:默认值每次实例化都求值,但存储由框架管理、跨 body 求值保留。"""
    print("[4] @State 存储生命周期")
    box = StateBox()
    box.make("PlayerView#1", 0)                    # 首次实例化:采用默认值
    check("首次实例化采用默认值 0", box.value("PlayerView#1") == 0)
    box.set("PlayerView#1", 0.75)
    box.make("PlayerView#1", 0)                    # body 重新求值 → 结构体再次实例化
    check("重新实例化后旧值保留(默认值被忽略)",
          box.value("PlayerView#1") == 0.75, f"value={box.value('PlayerView#1')}")
    check("默认值仍然被求值了(所以别在默认值里做副作用/重活)",
          box.init_count == 2, f"init_count={box.init_count}")
    box.remove("PlayerView#1")                     # 视图被移除 → 状态销毁
    box.make("PlayerView#1", 0)
    check("视图移除后状态销毁,重新回到默认值", box.value("PlayerView#1") == 0)


def t_binding() -> None:
    """@Binding:子视图拿到 $state 才能写回父视图的状态。"""
    print("[5] @Binding 读写投影")
    box = StateBox()
    box.make("PlayerView#1", False)
    play_button = Binding(box, "PlayerView#1")     # 传的是 Binding 而不是值
    play_button.set(not play_button.get())         # 子视图 toggle
    check("子视图通过 Binding 写回父视图状态", box.value("PlayerView#1") is True)
    box.set("PlayerView#1", False)                 # 父视图侧写入同一存储槽
    check("父视图侧写入同样可见", play_button.get() is False)


def t_object_in_state_trap() -> None:
    """文档明确警告:ObservableObject 放进 @State,只有引用变化才更新视图。"""
    print("[6] 陷阱:ObservableObject 放进 @State")
    pub = CoarsePublisher()                        # ObservableObject
    box = StateBox()
    ref1 = {"title": "A", "isAvailable": True}
    box.make("ContentView#1", ref1)
    renders = [0]

    def state_changed() -> None:                   # @State 变化 → 视图重算 body
        renders[0] += 1

    state_changed()
    ref1["title"] = "B"                            # 只改对象内部属性,**不**碰 @State
    check("只改内部属性:引用未变、也没订阅 objectWillChange → 视图不更新",
          renders[0] == 1, f"renders={renders[0]}")
    box.set("ContentView#1", {"title": "B", "isAvailable": True})   # 换引用
    state_changed()
    check("换掉整个引用后视图更新(正确做法:@StateObject / @Observable)",
          renders[0] == 2, f"renders={renders[0]}")

    renders2 = [0]                                 # 对照:@StateObject 订阅了 objectWillChange
    pub.subscribe(lambda: renders2.__setitem__(0, renders2[0] + 1))
    renders2[0] += 1
    pub.will_change("title")
    check("@StateObject 订阅后,属性变化同样能触发更新",
          renders2[0] == 2 and pub.notify_count == 1,
          f"renders={renders2[0]} notify={pub.notify_count}")


if __name__ == "__main__":
    for fn in (t_tracked_only, t_coarse_vs_fine, t_one_shot_tracking,
               t_state_lifecycle, t_binding, t_object_in_state_trap):
        fn()
    print(f"\n断言 {len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项:", FAIL)
    raise SystemExit(1 if FAIL else 0)
