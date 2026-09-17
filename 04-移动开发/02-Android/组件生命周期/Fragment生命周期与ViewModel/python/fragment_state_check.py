#!/usr/bin/env python3
"""Fragment 生命周期状态机 + ViewModel 作用域 —— 教学模型 + 自检(实跑)。

权威依据(见 README「参考资料」):
  * androidx `Fragment.java` 的 9 个状态常量及源码注释:
      INITIALIZING=-1(未附着)/ ATTACHED=0 / CREATED=1 / VIEW_CREATED=2 /
      AWAITING_EXIT_EFFECTS=3(向下过渡,等待 exit effects)/
      ACTIVITY_CREATED=4(完全创建,未启动)/ STARTED=5 /
      AWAITING_ENTER_EFFECTS=6(向上过渡,等待 enter effects)/ RESUMED=7
  * `Fragment.java` 各回调 javadoc:onStart / onResume / onStop 与宿主 Activity 的对应回调
    "generally tied to";onDestroyView「在 onStop 之后、onDestroy 之前;与 onCreateView 是否
    返回非空 view 无关;下次显示时会创建新 view」。
  * androidx `ViewModel.kt`:ViewModel 的作用域属于 ViewModelStoreOwner,跨配置变更保留;
    onCleared 在 owner 被**永久销毁**时调用;viewModelScope 在 onCleared **之前**被取消。

口径声明:两个 AWAITING_* 按源码注释建模为「过渡态」——只在对应方向的迁移上经过、不新增回调、
只记录 effect 事件;`FragmentStateManager` 内部的精确调用次序不在断言范围内。

运行: python3 fragment_state_check.py
"""

from __future__ import annotations

import sys

INITIALIZING, ATTACHED, CREATED, VIEW_CREATED = -1, 0, 1, 2
AWAITING_EXIT_EFFECTS, ACTIVITY_CREATED, STARTED, AWAITING_ENTER_EFFECTS, RESUMED = 3, 4, 5, 6, 7

STATE_NAMES = {
    INITIALIZING: "INITIALIZING", ATTACHED: "ATTACHED", CREATED: "CREATED",
    VIEW_CREATED: "VIEW_CREATED", AWAITING_EXIT_EFFECTS: "AWAITING_EXIT_EFFECTS",
    ACTIVITY_CREATED: "ACTIVITY_CREATED", STARTED: "STARTED",
    AWAITING_ENTER_EFFECTS: "AWAITING_ENTER_EFFECTS", RESUMED: "RESUMED",
}
# 停留态路径(过渡态不在其中);数值顺序与原常量一致
PATH = [INITIALIZING, ATTACHED, CREATED, VIEW_CREATED, ACTIVITY_CREATED, STARTED, RESUMED]
RESTING = list(PATH)

ENTER = {ATTACHED: ["onAttach"], CREATED: ["onCreate"],
         VIEW_CREATED: ["onCreateView", "onViewCreated", "onViewStateRestored"],
         ACTIVITY_CREATED: [], STARTED: ["onStart"], RESUMED: ["onResume"]}
EXIT = {RESUMED: ["onPause"], STARTED: ["onStop"], ACTIVITY_CREATED: [],
        VIEW_CREATED: ["onDestroyView"], CREATED: ["onDestroy"], ATTACHED: ["onDetach"]}
EFFECTS = {AWAITING_EXIT_EFFECTS: "run_exit_effects", AWAITING_ENTER_EFFECTS: "run_enter_effects"}

PASS = 0


class IllegalStateException(RuntimeError):
    """对应 androidx 在 view 已销毁时抛出的 IllegalStateException。"""


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        print(f"FAIL  {label}  {detail}")
        raise AssertionError(label)
    PASS += 1
    print(f"ok    {label}" + (f"  [{detail}]" if detail else ""))


class Fragment:
    def __init__(self, tag: str = "frag"):
        self.tag = tag
        self.state = INITIALIZING
        self.events: list[str] = []
        self.view_created = False

    def move_to(self, target: int) -> None:
        """把状态迁到 target(必须是停留态);向上进入回调,向下触发离开回调。"""
        if target not in RESTING:
            raise ValueError(f"{STATE_NAMES[target]} 是过渡态,不能作为目标状态")
        while self.state != target:
            i = PATH.index(self.state)
            if PATH.index(target) > i:
                nxt = PATH[i + 1]
                if nxt == RESUMED:                       # 进 RESUMED 前先跑 enter effects
                    self._effects(AWAITING_ENTER_EFFECTS)
                self.state = nxt
                self.events.extend(ENTER.get(nxt, []))
                if nxt == VIEW_CREATED:
                    self.view_created = True
            else:
                self.events.extend(EXIT.get(self.state, []))
                if self.state == VIEW_CREATED:
                    self.view_created = False
                if self.state == ACTIVITY_CREATED:       # 离开 4 先跑 exit effects
                    self._effects(AWAITING_EXIT_EFFECTS)
                self.state = PATH[i - 1]

    def _effects(self, state: int) -> None:
        self.state = state
        self.events.append(EFFECTS[state])

    @property
    def view_lifecycle_owner(self) -> str:
        if not self.view_created or self.state < VIEW_CREATED:
            raise IllegalStateException(
                "Called getViewLifecycleOwner() but the fragment's view is not created "
                "or has already been destroyed")
        return f"{self.tag}#viewLifecycleOwner"

    def count(self, callback: str) -> int:
        return self.events.count(callback)

    def on_calls(self) -> list[str]:
        return [e for e in self.events if e.startswith("on")]


class ViewModel:
    def __init__(self, key: str):
        self.key = key
        self.scope_cancelled = False
        self.cleared = False


class ViewModelStore:
    def __init__(self):
        self.map: dict[str, ViewModel] = {}
        self.clear_events: list[str] = []

    def get(self, key: str, create) -> ViewModel:
        if key not in self.map:
            self.map[key] = create(key)
        return self.map[key]

    def clear(self) -> None:
        """官方:viewModelScope 在 onCleared **之前**取消;清空后不再持有实例。"""
        for vm in list(self.map.values()):
            vm.scope_cancelled = True
            self.clear_events.append(f"{vm.key}:scope_cancelled")
            vm.cleared = True
            self.clear_events.append(f"{vm.key}:onCleared")
        self.map.clear()


class Owner:
    """ViewModelStoreOwner:Activity 或 Fragment。"""

    def __init__(self, name: str, store: ViewModelStore | None = None):
        self.name = name
        self.store = store if store is not None else ViewModelStore()
        self.created: list[str] = []

    def view_model(self, key: str) -> ViewModel:
        def create(k: str) -> ViewModel:
            self.created.append(k)
            return ViewModel(k)
        return self.store.get(key, create)


def _raises(fn) -> bool:
    try:
        fn()
    except IllegalStateException:
        return True
    return False


# ------------------------------------------------------------------ 场景
def scenario_state_constants() -> None:
    check("9 个状态常量取值与 Fragment.java 源码一致",
          [INITIALIZING, ATTACHED, CREATED, VIEW_CREATED, AWAITING_EXIT_EFFECTS, ACTIVITY_CREATED,
           STARTED, AWAITING_ENTER_EFFECTS, RESUMED] == [-1, 0, 1, 2, 3, 4, 5, 6, 7],
          "[-1..7]")
    check("两个 AWAITING_* 是过渡态,不能作为目标状态",
          AWAITING_EXIT_EFFECTS not in RESTING and AWAITING_ENTER_EFFECTS not in RESTING,
          f"RESTING={[STATE_NAMES[s] for s in RESTING]}")
    f = Fragment()
    try:
        f.move_to(AWAITING_ENTER_EFFECTS)
        rejected = False
    except ValueError as exc:
        rejected = "过渡态" in str(exc)
    check("直接迁到过渡态被拒绝", rejected)


def scenario_ascend_to_resumed() -> None:
    f = Fragment("home")
    f.move_to(RESUMED)
    expect = ["onAttach", "onCreate", "onCreateView", "onViewCreated", "onViewStateRestored",
              "onStart", "run_enter_effects", "onResume"]
    check("附着到 RESUMED 的回调顺序正确", f.events == expect, " -> ".join(f.events))
    check("停在 RESUMED", f.state == RESUMED, STATE_NAMES[f.state])
    check("view 生命周期已建立", f.view_lifecycle_owner == "home#viewLifecycleOwner", f.view_lifecycle_owner)
    check("向上迁移只经过 enter effects 过渡态",
          "run_enter_effects" in f.events and "run_exit_effects" not in f.events, str(f.events))


def scenario_back_stack_destroys_view_only() -> None:
    f = Fragment("detail")
    f.move_to(RESUMED)
    f.move_to(CREATED)                      # 入返回栈:视图销毁,Fragment 实例保留
    check("入返回栈后 Fragment 停在 CREATED", f.state == CREATED, STATE_NAMES[f.state])
    check("视图确实被销毁(onDestroyView 已触发)", f.count("onDestroyView") == 1, str(f.events))
    check("Fragment 本身未被销毁(onDestroy / onDetach 均未触发)",
          f.count("onDestroy") == 0 and f.count("onDetach") == 0, str(f.events))
    check("onDestroyView 在 onStop 之后触发",
          f.events.index("onStop") < f.events.index("onDestroyView"), " -> ".join(f.events))
    check("视图销毁后 getViewLifecycleOwner 抛 IllegalStateException",
          _raises(lambda: f.view_lifecycle_owner))

    f.move_to(RESUMED)                      # 出返回栈:视图重建
    check("回到前台时视图重建(onCreateView 第 2 次)", f.count("onCreateView") == 2, str(f.events))
    check("onCreate 只调用一次(复用 Fragment 实例,不重新创建)", f.count("onCreate") == 1,
          f"onCreate={f.count('onCreate')}")
    check("重建视图时 onCreateView → onViewCreated → onViewStateRestored 依次重跑",
          f.events[-6:] == ["onCreateView", "onViewCreated", "onViewStateRestored", "onStart",
                            "run_enter_effects", "onResume"], " -> ".join(f.events[-6:]))


def scenario_full_teardown_order() -> None:
    f = Fragment("tmp")
    f.move_to(RESUMED)
    f.move_to(INITIALIZING)
    seq = f.on_calls()
    check("完整销毁以 onPause→onStop→onDestroyView→onDestroy→onDetach 收尾",
          seq[-5:] == ["onPause", "onStop", "onDestroyView", "onDestroy", "onDetach"], " -> ".join(seq))
    check("onDestroyView 早于 onDestroy(javadoc 原文)",
          seq.index("onDestroyView") < seq.index("onDestroy"))
    check("onDestroy 在 onStop 之后(javadoc 原文)", seq.index("onStop") < seq.index("onDestroy"))
    check("向下迁移经过 exit effects 过渡态", "run_exit_effects" in f.events, str(f.events))
    check("每个构造期回调只出现一次(向下不重放 enter 回调)",
          seq.count("onAttach") == 1 and seq.count("onCreate") == 1 and seq.count("onStart") == 1,
          str(seq))
    check("最终回到 INITIALIZING", f.state == INITIALIZING, STATE_NAMES[f.state])


def scenario_host_activity_clamps_max_state() -> None:
    """javadoc:onStart / onResume / onStop 与宿主 Activity 的对应回调 generally tied。"""

    def max_state_for(activity_started: bool, activity_resumed: bool) -> int:
        if activity_resumed:
            return RESUMED
        return STARTED if activity_started else ACTIVITY_CREATED

    check("宿主仅 CREATED:Fragment 最多到 ACTIVITY_CREATED",
          max_state_for(False, False) == ACTIVITY_CREATED, STATE_NAMES[max_state_for(False, False)])
    check("宿主 STARTED:Fragment 最多到 STARTED",
          max_state_for(True, False) == STARTED, STATE_NAMES[max_state_for(True, False)])
    check("宿主 RESUMED:Fragment 才能到 RESUMED",
          max_state_for(True, True) == RESUMED, STATE_NAMES[max_state_for(True, True)])
    f = Fragment("a")
    f.move_to(max_state_for(True, False))
    check("宿主 STARTED 时 Fragment 收不到 onResume", f.count("onResume") == 0, " -> ".join(f.events))
    f.move_to(max_state_for(True, True))
    check("宿主 RESUMED 后 Fragment 才收到 onResume", f.count("onResume") == 1, " -> ".join(f.events))


def scenario_view_model_scope() -> None:
    activity = Owner("MainActivity")
    a = activity.view_model("UserViewModel")
    b = activity.view_model("UserViewModel")
    check("同一 owner + 同一 key 只创建一次 ViewModel",
          a is b and activity.created == ["UserViewModel"], f"created={activity.created}")

    rotated = Owner("MainActivity#rotated", store=activity.store)   # 配置变更:换实例、留 store
    check("配置变更后拿到同一个 ViewModel 实例",
          rotated.view_model("UserViewModel") is a, "same instance")
    check("配置变更不触发 onCleared(跨配置变更保留)", a.cleared is False, str(a.cleared))

    frag_a = Owner("FragmentA", store=activity.store)
    frag_b = Owner("FragmentB", store=activity.store)
    check("两个 Fragment 共用 Activity 作为 store owner 即可共享 ViewModel",
          frag_a.view_model("UserViewModel") is frag_b.view_model("UserViewModel") is a,
          "shared instance")

    activity.store.clear()
    check("owner 永久销毁时 onCleared 被调用", a.cleared is True, str(a.cleared))
    check("viewModelScope 在 onCleared 之前被取消",
          activity.store.clear_events == ["UserViewModel:scope_cancelled", "UserViewModel:onCleared"],
          str(activity.store.clear_events))
    activity.store.clear()
    check("重复清理不会二次回调(onCleared 只调用一次)",
          activity.store.clear_events.count("UserViewModel:onCleared") == 1,
          str(activity.store.clear_events))


def main() -> int:
    for fn in (scenario_state_constants, scenario_ascend_to_resumed,
               scenario_back_stack_destroys_view_only, scenario_full_teardown_order,
               scenario_host_activity_clamps_max_state, scenario_view_model_scope):
        print(f"\n--- {fn.__name__} ---")
        fn()
    print(f"\nALL PASS: {PASS} assertions")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
