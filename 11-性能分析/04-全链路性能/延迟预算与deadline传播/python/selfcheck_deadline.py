#!/usr/bin/env python3
"""demo 599 延迟预算与 deadline 传播自检。"""

import sys

from deadline import (
    CANCELLED, DEADLINE_EXCEEDED, OK, client_status, consumed, propagate,
    server_status, split_by_cost, split_by_headroom, split_equal, to_deadline, to_timeout,
)

FAILED, COUNT = [], 0


def check(name, cond):
    global COUNT
    COUNT += 1
    if not cond:
        FAILED.append(name)
        print(f"  FAIL {name}")


def eq(name, got, want):
    global COUNT
    COUNT += 1
    if got != want:
        FAILED.append(f"{name}")
        print(f"  FAIL {name}: got={got!r} want={want!r}")


def close(name, got, want, tol=1e-9):
    global COUNT
    COUNT += 1
    if abs(got - want) > tol:
        FAILED.append(name)
        print(f"  FAIL {name}: got={got} want={want}")


T0 = 1_700_000_000.0

# --- 1. 时刻与时长互换 ---
d = T0 + 2.0
close("deadline → timeout（未耗时）", to_timeout(d, T0), 2.0)
close("deadline → timeout（已耗 0.5s）", to_timeout(d, T0 + 0.5), 1.5)
close("timeout → deadline 往返一致", to_timeout(to_deadline(1.5, T0), T0), 1.5)
close("往返与起点无关", to_deadline(to_timeout(d, T0), T0 + 7.0), d + 7.0)

# --- 2. propagate 扣已耗时与预留 ---
close("扣掉已耗时", propagate(d, T0 + 0.5), 1.5)
close("再扣 0.2s 预留", propagate(d, T0 + 0.5, 0.2), 1.3)
close("预留超过剩余时截断到 0", propagate(d, T0 + 1.8, 0.5), 0.0)
check("截断后不会给出负数", propagate(d, T0 + 5.0, 1.0) >= 0.0)

# --- 3. 官方时序：2s 预算、本跳 0.5s、下游 1.5s ---
close("官方示例的下游 timeout", propagate(T0 + 2.0, T0 + 0.5), 1.5)

# --- 4. 客户端 DEADLINE_EXCEEDED / 服务端 CANCELLED ---
eq("恰好在 deadline 上完成 → OK", client_status(d, d), OK)
eq("早于 deadline → OK", client_status(d, d - 0.001), OK)
eq("晚于 deadline → DEADLINE_EXCEEDED", client_status(d, d + 0.001), DEADLINE_EXCEEDED)
eq("服务端到达 deadline 仍算 OK", server_status(d, d), OK)
eq("服务端越过 deadline → CANCELLED", server_status(d, d + 0.001), CANCELLED)
check("两个状态不是同一个值", DEADLINE_EXCEEDED != CANCELLED)

# --- 5. 时钟偏移免疫（正向）与直传时刻失效（负控） ---
budget = 2.0
wire = to_timeout(T0 + budget, T0)
for skew in (-300.0, -30.0, 0.0, 7.5, 300.0):
    now_s = T0 + skew
    close(f"偏 {skew:+.1f}s 时剩余仍是 {budget}s", to_deadline(wire, now_s) - now_s, budget)
    check(f"偏 {skew:+.1f}s 时直传时刻会算错", abs((T0 + budget) - now_s - budget) > 1e-9 or skew == 0.0)

# --- 6. 等分 ---
eq("等分求和", sum(split_equal(200.0, 3)) - 200.0 < 1e-9, True)
eq("等分空表", split_equal(200.0, 0), [])
close("等分单项", split_equal(200.0, 1)[0], 200.0)

# --- 7. 按成本加权 ---
alloc = split_by_cost(200.0, [120.0, 50.0, 30.0])
close("按成本加权求和 = 预算", sum(alloc), 200.0)
close("比例正确（120/200）", alloc[0], 120.0)
close("比例正确（30/200）", alloc[2], 30.0)
eq("全零成本退化为等分", split_by_cost(90.0, [0.0, 0.0, 0.0]), split_equal(90.0, 3))
close("缩放不变性", split_by_cost(200.0, [12.0, 5.0, 3.0])[0], 120.0)

# --- 8. 注水法：下限与上限都被尊重 ---
res = split_by_headroom(200.0, floors=[40.0, 20.0, 10.0], caps=[150.0, 60.0, 30.0])
check("可行", res["feasible"])
close("刚好分完", sum(res["alloc"]), 200.0)
check("不低于下限", all(a >= f - 1e-9 for a, f in zip(res["alloc"], [40.0, 20.0, 10.0])))
check("不高于上限", all(a <= c + 1e-9 for a, c in zip(res["alloc"], [150.0, 60.0, 30.0])))
# 可压缩空间大的那一跳拿到更多余量：跳1 余量空间 110、跳3 只有 20
check("余量按可压缩空间分配",
      (res["alloc"][0] - 40.0) > (res["alloc"][2] - 10.0))

# 上限生效：预算充裕时按上限封顶，不再注水
res2 = split_by_headroom(1000.0, floors=[40.0, 20.0, 10.0], caps=[150.0, 60.0, 30.0])
check("上限封顶", all(a <= c + 1e-9 for a, c in zip(res2["alloc"], [150.0, 60.0, 30.0])))
check("预算大于总上限时留余量", res2["slack"] > 0)
close("封顶后分配等于上限", sum(res2["alloc"]), 240.0)

# 下限之和超过预算 → 不可行
bad = split_by_headroom(50.0, floors=[40.0, 20.0, 10.0], caps=[150.0, 60.0, 30.0])
check("下限超预算判定不可行", not bad["feasible"])
check("slack 为负", bad["slack"] < 0)
eq("空列表可行", split_by_headroom(50.0, [], [])["feasible"], True)

# 全部无压缩空间时（floors == caps）不再注水
flat = split_by_headroom(100.0, floors=[30.0, 30.0], caps=[30.0, 30.0])
close("无可压缩空间时和 = 下限和", sum(flat["alloc"]), 60.0)

# --- 9. 逐跳对账 ---
rows = consumed([120.0, 50.0, 30.0], [140.0, 45.0, 32.0])
close("跳1 超支 20ms", rows[0]["over"], 20.0)
close("跳2 达标 -5ms", rows[1]["over"], -5.0)
close("跳1 超支比例", rows[0]["over_pct"], 20.0 / 120.0)
check("超支比例为正", rows[0]["over_pct"] > 0)
check("达标比例为负", rows[1]["over_pct"] < 0)
eq("零预算时比例记为正无穷", consumed([0.0], [1.0])[0]["over_pct"], float("inf"))

print(f"\n{COUNT - len(FAILED)}/{COUNT} 断言通过")
sys.exit(1 if FAILED else 0)
