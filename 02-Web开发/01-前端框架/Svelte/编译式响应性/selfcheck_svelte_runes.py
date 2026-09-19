# -*- coding: utf-8 -*-
"""svelte_runes.py 自检：每条断言都对照官方文档给出的语义。"""
from svelte_runes import (Source, Derived, Effect, StateProxy, state, flush,
                          read, dget)

ok = 0
fails = []


def check(label, cond, detail=""):
    global ok
    if cond:
        ok += 1
    else:
        fails.append("%s %s" % (label, detail))


# --- 1. $state 深代理：写只落在 Source，原对象永不被 mutate（$state.md 明写） ---
src = {"a": 1, "b": {"c": 2}}
p = state(src)
p["a"] = 99
check("A1 原对象未被 mutate", src["a"] == 1, "src=%r" % (src,))
check("A2 proxy 读到新值", p["a"] == 99)
p["b"]["c"] = 7
check("A3 嵌套写也不改原对象", src["b"]["c"] == 2, "src=%r" % (src,))
check("A4 嵌套读到新值", p["b"]["c"] == 7)
check("A5 原 dict 对象身份未变", isinstance(src, dict) and not isinstance(src, StateProxy))

# --- 2. $derived 是 pull：标脏后「下次读取」才重算（$derived.md） ---
n = state(1)
d = Derived(lambda: dget(n) * 2)
check("B1 首次读取才求值", d.recomputes == 0)
check("B2 首次读值正确", d.get() == 2 and d.recomputes == 1)
n.set(2)
check("B3 改 state 后尚未重算(pull)", d.recomputes == 1, "recomputes=%d" % d.recomputes)
check("B4 读取时才重算", d.get() == 4 and d.recomputes == 2)
d.get()
check("B5 clean 后再读不重算", d.recomputes == 2)

# --- 3. 依赖是「同步读到的」那些，分支切换后依赖集随之变化 ---
flag = state(True)
a, b = state(1), state(100)
picks = []
dyn = Derived(lambda: (picks.append("a"), dget(a))[1] if dget(flag) else (picks.append("b"), dget(b))[1])
check("C1 走 a 分支", dyn.get() == 1 and picks == ["a"])
b.set(200)
check("C2 未读过的 b 变化不标脏", not dyn.dirty)
flag.set(False)
check("C3 依赖变化后标脏", dyn.dirty)
check("C4 切换后走 b 分支且读到新值", dyn.get() == 200)
a.set(5)
check("C5 切走后旧依赖 a 不再触发", not dyn.dirty)

# --- 4. $effect 重跑批处理（$effect.md：changing color and size in the same moment） ---
log = []
c1, c2 = state("red"), state(50)
ef = Effect(lambda: (log.append((dget(c1), dget(c2))), None)[1])
ef.run()
check("D1 挂载后跑第一次", log == [("red", 50)])
c1.set("blue")
c2.set(80)
check("D2 同步两次改动尚未 flush", len(log) == 1)
flush()
check("D3 flush 后只重跑一次", len(log) == 2 and log[1] == ("blue", 80), "log=%r" % (log,))

# --- 5. teardown 立即先于重跑 ---
td = []
seen_in_td = []
x = state(1)


def _body():
    v = dget(x)
    td.append("run%d" % v)
    return lambda: (td.append("td"), seen_in_td.append(dget(x)))


e2 = Effect(_body)
e2.run()
x.set(2)
flush()
check("E1 teardown 紧邻且先于重跑", td == ["run1", "td", "run2"], "td=%r" % (td,))
check("E2 teardown 计数", e2.td_runs == 1)
check("E3 teardown 里读到的是已更新的值(不是旧值)", seen_in_td == [2], "%r" % (seen_in_td,))

# --- 6. 模板/DOM 更新 effect 先于用户 effect ---
order = []
s = state(0)
tpl = Effect(lambda: (order.append("tpl%d" % dget(s)), None)[1], prio=0)
usr = Effect(lambda: (order.append("usr%d" % dget(s)), None)[1], prio=1)
tpl.run()
usr.run()
order.clear()
s.set(1)
flush()
check("F1 模板更新先于用户 effect", order == ["tpl1", "usr1"], "order=%r" % (order,))

# --- 7. 值没真变：derived 不推进 version，下游 effect 不重跑 ---
raw = state(3)
dbl = Derived(lambda: dget(raw) * 2)
runs = []
e3 = Effect(lambda: (runs.append(dbl.get()), None)[1])
e3.run()
check("G1 初值", runs == [6])
raw.set(4)
raw.set(3)          # 来回改，最终值与初值相同
flush()
check("G2 值回到原值后 effect 未重跑", len(runs) == 1, "runs=%r" % (runs,))

# --- 8. 解构即失去响应性（$state.md：「references are not reactive」） ---
pv = state({"v": 1})
grabbed = pv["v"]
pv["v"] = 42
check("H1 解构出的值是快照", grabbed == 1 and pv["v"] == 42)

# --- 9. 数组 push 同样触发（$state.md：push 会 proxify 新元素） ---
arr = state([1])
total = Derived(lambda: sum(arr[i] for i in range(len(arr))))
check("I1 初始和", total.get() == 1)
arr.append(5)
check("I2 push 后标脏且重算", total.dirty and total.get() == 6)

# --- 10. Source 写同值不推进 version、不通知 ---
z = state(7)
v0 = z.version
hit = []
e4 = Effect(lambda: (hit.append(dget(z)), None)[1])
e4.run()
z.set(7)
check("J1 同值写入 version 不变", z.version == v0)
flush()
check("J2 同值写入不触发 effect", len(hit) == 1, "hit=%r" % (hit,))

print("svelte_runes: %d/%d assertions passed" % (ok, ok + len(fails)))
for f in fails:
    print("  FAIL", f)
raise SystemExit(1 if fails else 0)
