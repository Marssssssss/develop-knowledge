#!/usr/bin/env python3
"""WorkManager 约束 / 退避重试 / 唯一工作 / 依赖链 —— 教学模型 + 自检(实跑)。

依据 androidx 源码与 javadoc(见 README「参考资料」):
  * Constraints:默认 `NOT_REQUIRED` + 四个开关(charging / deviceIdle / batteryNotLow /
    storageNotLow),所有要求是**与**关系;新增 content Uri 触发器(API 24+)。
  * WorkRequest:默认退避策略 EXPONENTIAL、默认退避 30_000 ms;
    backoffDelay 会被 clamp 到 [MIN_BACKOFF_MILLIS=10s, MAX_BACKOFF_MILLIS=5h]。
  * Worker.doWork():每个实例只调用一次,返回 Result.success / failure / retry;
    返回 failure 时**依赖它的后续工作不会执行**;执行窗口上限 10 分钟,超时会被通知停止。

口径声明:「失败后第 n 次重试的实际倍数」在官方 javadoc 中没有给出数值公式,demo 采用
`base × 2^(attempt-1)`(指数)/ `base × attempt`(线性)这一常见口径,并用 clamp 区间锚定官方数值。

运行: python3 workmanager_check.py
"""

from __future__ import annotations

import sys

from workmanager_model import (Constraints, DEFAULT_BACKOFF_DELAY_MS, Device, Engine, WorkRequest,
                               backoff_delay_ms, within_execution_window)


PASS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        print(f"FAIL  {label}  {detail}")
        raise AssertionError(label)
    PASS += 1
    print(f"ok    {label}" + (f"  [{detail}]" if detail else ""))


# --------------------------------------------------------------------- 场景
def scenario_default_constraints() -> None:
    c = Constraints()
    worst = Device(network="DISCONNECTED", metered=True, roaming=True, charging=False,
                   idle=False, battery_low=True, storage_low=True)
    check("默认网络要求是 NOT_REQUIRED", c.network_type == "NOT_REQUIRED", c.network_type)
    check("默认四个开关全为 false",
          not any((c.charging, c.device_idle, c.battery_not_low, c.storage_not_low)), str(c))
    check("无任何约束时,即使最差设备状态也满足(官方:可直接运行)", c.met(worst), str(c.unmet(worst)))


def scenario_constraint_gates() -> None:
    cases = [
        (Constraints(network_type="CONNECTED"), Device(network="DISCONNECTED"), False, "network"),
        (Constraints(network_type="UNMETERED"), Device(network="CONNECTED", metered=True), False,
         "network_unmetered"),
        (Constraints(network_type="UNMETERED"), Device(network="CONNECTED", metered=False), True, ""),
        (Constraints(network_type="NOT_ROAMING"), Device(network="CONNECTED", roaming=True), False,
         "network_not_roaming"),
        (Constraints(network_type="METERED"), Device(network="CONNECTED", metered=False), False,
         "network_metered"),
        (Constraints(charging=True), Device(charging=False), False, "charging"),
        (Constraints(device_idle=True), Device(idle=False), False, "device_idle"),
        (Constraints(battery_not_low=True), Device(battery_low=True), False, "battery_not_low"),
        (Constraints(storage_not_low=True), Device(storage_low=True), False, "storage_not_low"),
    ]
    for c, d, expect_met, expect_reason in cases:
        got = c.met(d)
        check(f"约束 {c.network_type if c.network_type != 'NOT_REQUIRED' else c.unmet(d) or 'ok'} "
              f"→ met={expect_met}", got == expect_met, f"unmet={c.unmet(d)}")
        if expect_reason:
            check(f"未满足原因包含 {expect_reason}", expect_reason in c.unmet(d), str(c.unmet(d)))
    both = Constraints(network_type="UNMETERED", charging=True)
    check("多个约束是「与」关系:只满足一个仍不可运行",
          not both.met(Device(network="CONNECTED", metered=False, charging=False)),
          str(both.unmet(Device(network="CONNECTED", metered=False, charging=False))))


def scenario_hold_until_constraints_met() -> None:
    device = Device(network="CONNECTED", metered=True, charging=False)
    eng = Engine(device)
    req = WorkRequest("sync", lambda e, r: "success", Constraints(network_type="UNMETERED", charging=True))
    eng.enqueue(req)
    eng.run()
    check("约束未满足:工作保持 ENQUEUED,不执行", req.state == "ENQUEUED" and req.runs == 0,
          f"{req.state} runs={req.runs}")
    check("未满足原因被记录(网络非计费 + 充电中)", eng.deferrals and
          "network_unmetered" in eng.deferrals[0] and "charging" in eng.deferrals[0], str(eng.deferrals))
    eng.device = Device(network="CONNECTED", metered=False, charging=True)
    eng.run()
    check("约束满足后立刻执行并成功", req.state == "SUCCEEDED" and req.runs == 1,
          f"{req.state} runs={req.runs}")


def scenario_backoff_table() -> None:
    exp = [backoff_delay_ms("EXPONENTIAL", DEFAULT_BACKOFF_DELAY_MS, n) for n in range(1, 6)]
    check("默认退避 30s + 指数:第 1~5 次重试为 30/60/120/240/480 秒",
          exp == [30_000, 60_000, 120_000, 240_000, 480_000], str(exp))
    lin = [backoff_delay_ms("LINEAR", 10_000, n) for n in range(1, 5)]
    check("线性退避 10s:第 1~4 次为 10/20/30/40 秒", lin == [10_000, 20_000, 30_000, 40_000], str(lin))
    big = [backoff_delay_ms("EXPONENTIAL", 60 * 60 * 1000, n) for n in range(1, 5)]
    check("base=1h 时第 4 次将被 clamp 到 MAX_BACKOFF_MILLIS=5h",
          big == [3_600_000, 7_200_000, 14_400_000, 18_000_000], str(big))
    small = backoff_delay_ms("EXPONENTIAL", 1_000, 1)
    check("base=1s 被 clamp 到 MIN_BACKOFF_MILLIS=10s", small == 10_000, str(small))


def scenario_retry_then_success() -> None:
    eng = Engine(Device())
    state = {"n": 0}

    def flaky(e, r):
        state["n"] += 1
        return "success" if state["n"] >= 3 else "retry"

    req = WorkRequest("flaky", flaky)
    eng.enqueue(req)
    eng.run()
    check("返回 retry:重新排队并累加 attempts", req.state == "ENQUEUED" and req.attempts == 1,
          f"{req.state} attempts={req.attempts}")
    check("第 1 次重试延迟 = 30s(默认退避)", req.next_retry_at == 30_000, f"next={req.next_retry_at}")
    eng.advance(30_000)
    eng.run()
    check("第 2 次重试延迟翻倍到 60s(指数退避)", req.attempts == 2 and req.next_retry_at == 90_000,
          f"attempts={req.attempts} next={req.next_retry_at}")
    eng.advance(60_000)
    eng.run()
    check("第 3 次调用成功 → SUCCEEDED", req.state == "SUCCEEDED" and req.runs == 3,
          f"{req.state} runs={req.runs}")


def scenario_chain_dependency() -> None:
    eng = Engine(Device())
    order: list[str] = []
    a = WorkRequest("A", lambda e, r: (order.append("A"), "success")[1])
    b = WorkRequest("B", lambda e, r: (order.append("B"), "success")[1], depends_on="A")
    c = WorkRequest("C", lambda e, r: (order.append("C"), "success")[1], depends_on="B")
    for r in (a, b, c):
        eng.enqueue(r)
    check("依赖链初始:B、C 处于 BLOCKED", b.state == "BLOCKED" and c.state == "BLOCKED",
          f"B={b.state} C={c.state}")
    eng.run()
    check("依赖链按顺序 A→B→C 执行", order == ["A", "B", "C"], str(order))

    eng2, order2 = Engine(Device()), []
    a2 = WorkRequest("A", lambda e, r: (order2.append("A"), "failure")[1])
    b2 = WorkRequest("B", lambda e, r: (order2.append("B"), "success")[1], depends_on="A")
    c2 = WorkRequest("C", lambda e, r: (order2.append("C"), "success")[1], depends_on="B")
    for r in (a2, b2, c2):
        eng2.enqueue(r)
    eng2.run()
    check("Result.failure 时依赖它的后续工作不会执行(官方 javadoc)",
          order2 == ["A"] and c2.state == "CANCELLED", f"order={order2} C={c2.state}")


def scenario_unique_work() -> None:
    eng = Engine(Device())
    first = WorkRequest("download", lambda e, r: "success")
    eng.enqueue(first, unique_name="download", conflict="KEEP")
    second = WorkRequest("download", lambda e, r: "success")
    kept = eng.enqueue(second, unique_name="download", conflict="KEEP")
    check("ExistingWorkPolicy.KEEP:同名未结束的工作存在时,新请求被忽略",
          kept is first and len(eng.requests) == 1, f"requests={len(eng.requests)}")
    third = WorkRequest("download", lambda e, r: "success")
    eng.enqueue(third, unique_name="download", conflict="REPLACE")
    check("ExistingWorkPolicy.REPLACE:取消旧工作并换上新工作",
          first.state == "CANCELLED" and third.state == "ENQUEUED" and len(eng.requests) == 2,
          f"first={first.state} third={third.state} n={len(eng.requests)}")


def scenario_constraint_lost_and_window() -> None:
    eng = Engine(Device(network="CONNECTED", metered=False))
    seen = {"n": 0}

    def loses_network(e, r):
        seen["n"] += 1
        return "constraint_lost"

    req = WorkRequest("uplink", loses_network, Constraints(network_type="UNMETERED"))
    eng.enqueue(req)
    eng.run(max_steps=2)
    check("运行中约束丢失 → 停止并重新排队,attempts 递增",
          req.state == "ENQUEUED" and req.attempts == 1, f"{req.state} attempts={req.attempts}")
    check("重新排队使用退避延迟", req.next_retry_at == DEFAULT_BACKOFF_DELAY_MS,
          f"next_retry_at={req.next_retry_at}")
    check("执行窗口:599s 允许,601s 超出 10 分钟上限被通知停止",
          within_execution_window(599_000) and not within_execution_window(601_000),
          "窗口 = 600000 ms")


def main() -> int:
    for fn in (scenario_default_constraints, scenario_constraint_gates,
               scenario_hold_until_constraints_met, scenario_backoff_table,
               scenario_retry_then_success, scenario_chain_dependency,
               scenario_unique_work, scenario_constraint_lost_and_window):
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
