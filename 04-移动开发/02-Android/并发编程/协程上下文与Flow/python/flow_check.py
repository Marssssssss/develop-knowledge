#!/usr/bin/env python3
"""Kotlin Flow:冷流 / 中间算子惰性 / flowOn 只改上游 / 上下文保护 —— 自检(实跑)。

依据 kotlinlang.org 官方《Flows》文档:
  * 冷流是惰性的:builder 代码块不运行,直到被 collect;每个新收集者从零重跑一遍
  * 中间算子同样是冷的:上游即使是热流,返回的新流在 collect 之前也不处理值
  * flowOn 只改变**它上游**的协程上下文;它之后的算子仍在收集者上下文执行
  * 上下文保护:不允许在另一个协程里发射值(否则抛 IllegalStateException)

运行: python3 flow_check.py
"""

from __future__ import annotations

import sys

PASS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        print(f"FAIL  {label}  {detail}")
        raise AssertionError(label)
    PASS += 1
    print(f"ok    {label}" + (f"  [{detail}]" if detail else ""))


class FlowInvariantViolation(RuntimeError):
    """对应 kotlinx.coroutines 的 IllegalStateException("Flow invariant is violated...")。"""


class ColdFlow:
    """冷流:builder(emit, ctx) 只在 collect 时被调用,每次 collect 重新执行一遍。"""

    def __init__(self, builder, ops=(), n_upstream_ops: int = 0, upstream: str = "caller"):
        self._builder, self._ops = builder, list(ops)
        self._n_upstream_ops, self.upstream = n_upstream_ops, upstream

    def map(self, fn):
        """中间算子:只登记,不立刻执行。"""
        return ColdFlow(self._builder, self._ops + [fn], self._n_upstream_ops, self.upstream)

    def flowOn(self, upstream_ctx: str):
        """把**当前已有**的算子全部划归上游,之后新增的算子属于下游(收集者上下文)。"""
        return ColdFlow(self._builder, self._ops, len(self._ops), upstream_ctx)

    def collect(self, action) -> list:
        ctx_up, ctx_down = self.upstream, "caller"
        values: list = []

        def emit(value, from_ctx: str) -> None:
            if from_ctx != ctx_up:
                raise FlowInvariantViolation(
                    "Flow invariant is violated: emission from another coroutine is detected")
            for i, fn in enumerate(self._ops):
                seen = ctx_up if i < self._n_upstream_ops else ctx_down
                value = fn(value, seen)
            action(value, ctx_up, ctx_down)
            values.append(value)

        self._builder(emit, ctx_up)
        return values


class HotFlow:
    """热流(SharedFlow 的极简模型):发射时只送达**当前**的收集者。"""

    def __init__(self):
        self.collectors: list = []
        self.received: dict[str, list] = {}

    def collector(self, name: str):
        self.collectors.append(name)
        self.received.setdefault(name, [])
        return lambda v: self.received[name].append(v)

    def emit(self, value) -> None:
        for name in self.collectors:
            self.received[name].append(value)


def flow_of(values, journal, name: str = "flow") -> ColdFlow:
    def build(emit, ctx):
        journal.append(f"emitter<{name}> runs in {ctx}")
        for v in values:
            emit(v, ctx)
    return ColdFlow(build)


def scenario_cold_flow_is_lazy() -> None:
    runs, journal = {"n": 0}, []

    def build(emit, ctx):
        runs["n"] += 1
        journal.append("builder executed")
        for v in (1, 2, 3):
            emit(v, ctx)

    flow = ColdFlow(build)
    check("冷流:不 collect 就完全不执行 builder", runs["n"] == 0 and journal == [],
          f"runs={runs['n']}")
    first = flow.collect(lambda v, up, down: None)
    second = flow.collect(lambda v, up, down: None)
    check("冷流:每次 collect 都从零重跑一遍 builder", runs["n"] == 2, f"runs={runs['n']}")
    check("冷流:每个收集者都拿到完整序列", first == [1, 2, 3] and second == [1, 2, 3],
          f"{first} {second}")


def scenario_intermediate_operators_lazy() -> None:
    calls, journal = {"n": 0}, []
    flow = flow_of([1, 2, 3], journal)

    def doubling(v, ctx):
        calls["n"] += 1
        journal.append(f"map<{v}> runs in {ctx}")
        return v * 2

    mapped = flow.map(doubling)
    check("中间算子在 collect 之前不执行", calls["n"] == 0, f"calls={calls['n']}")
    out = mapped.collect(lambda v, up, down: None)
    check("collect 后每个值过一次算子", calls["n"] == 3, f"calls={calls['n']}")
    check("算子链的结果正确", out == [2, 4, 6], str(out))


def scenario_flow_on_boundary() -> None:
    journal = []
    seen = []
    flow = (flow_of([1, 2], journal, name="upstream")
            .map(lambda v, ctx: (journal.append(f"map-before-flowOn in {ctx}"), v + 1)[1])
            .flowOn("Dispatchers.IO")
            .map(lambda v, ctx: (journal.append(f"map-after-flowOn in {ctx}"), v * 10)[1]))
    out = flow.collect(lambda v, up, down: seen.append((v, up, down)))

    check("flowOn:上游 emitter 在 IO 跑", any("runs in Dispatchers.IO" in line for line in journal),
          "; ".join(journal))
    check("flowOn:它之前的算子归属上游(IO)",
          all("map-before-flowOn in Dispatchers.IO" in line for line in journal
              if "map-before-flowOn" in line), "; ".join(journal))
    check("flowOn:它之后的算子仍在收集者上下文(caller)",
          all("map-after-flowOn in caller" in line for line in journal
              if "map-after-flowOn" in line), "; ".join(journal))
    check("flowOn 不改变收集者自身所在的上下文",
          [(v, down) for v, up, down in seen] == [(20, "caller"), (30, "caller")], str(seen))
    check("上游/下游两个上下文同时可见于同一次收集",
          {up for _v, up, _down in seen} == {"Dispatchers.IO"}, str(seen))
    check("flowOn 之后值域正确(先 +1 再 *10)", out == [20, 30], str(out))


def scenario_context_preservation() -> None:
    def bad_build(emit, ctx):
        emit(1, ctx)
        emit(2, "another-coroutine")            # 在另一个协程里发射 → 违反上下文保护

    raised = None
    try:
        ColdFlow(bad_build).collect(lambda v, up, down: None)
    except FlowInvariantViolation as exc:
        raised = str(exc)
    check("上下文保护:在另一个协程里发射值会抛异常并中止收集", raised is not None, str(raised))
    check("异常信息指向 emission from another coroutine",
          raised is not None and "emission from another coroutine" in raised, str(raised))

    good = flow_of([1, 2], [], name="ok")
    check("合规的冷流可以正常收完", good.collect(lambda v, up, down: None) == [1, 2])


def scenario_hot_flow_contrast() -> None:
    hot = HotFlow()
    hot.collector("a")                      # 先订阅
    hot.emit("first")
    hot.collector("b")                      # 发射之后才订阅
    hot.emit("second")
    check("热流:晚订阅的收集者收不到之前发射过的值",
          hot.received["b"] == ["second"], str(hot.received))
    check("热流:发射时在场的收集者收到全部值",
          hot.received["a"] == ["first", "second"], str(hot.received))
    cold = flow_of(["first", "second"], [], name="cold")
    both = [cold.collect(lambda v, up, down: None) for _ in range(2)]
    check("对比冷流:后加入的收集者仍能收到完整序列(每次 collect 从头重跑)",
          both[0] == both[1] == ["first", "second"], str(both))


def main() -> int:
    for fn in (scenario_cold_flow_is_lazy, scenario_intermediate_operators_lazy,
               scenario_flow_on_boundary, scenario_context_preservation,
               scenario_hot_flow_contrast):
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
