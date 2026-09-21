"""层级状态机（SCXML 语义）自检：把规范 Appendix D 的判定逐条变成断言。

运行：``python selfcheck_hfsm.py``（当前目录 = python/）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import SCXML, Machine, State, Transition  # noqa: E402

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, "FAIL: " + label
    PASS += 1


def build():
    """构造一棵三层状态树：

    <scxml>
      s1 (initial: s1a)
        s1a, s1b
      s2 (initial: s2a, 带 shallow history h2 → s2a)
        s2a, s2b
    """
    states = {}
    doc = {}

    def add(st):
        states[st.id] = st
        doc[st.id] = len(doc)
        if st.parent and st.parent in states:
            states[st.parent].children.append(st.id)

    root = State(SCXML, None, "compound", initial=["s1"])
    add(root)
    s1 = State("s1", SCXML, "compound", initial=["s1a"])
    add(s1)
    s1a = State("s1a", "s1", "atomic")
    add(s1a)
    s1b = State("s1b", "s1", "atomic")
    add(s1b)
    s2 = State("s2", SCXML, "compound", initial=["s2a"], history=("shallow", "h2", ["s2a"]))
    add(s2)
    s2a = State("s2a", "s2", "atomic")
    add(s2a)
    s2b = State("s2b", "s2", "atomic")
    add(s2b)
    h2 = State("h2", "s2", "history")            # shallow history 伪状态
    add(h2)
    h2.transitions = [Transition("h2", None, ["s2a"], order=0)]

    states["s1a"].transitions = [Transition("s1a", "go", ["s1b"], order=0)]
    states["s1b"].transitions = [Transition("s1b", "leave", ["s2"], order=0)]
    states["s2a"].transitions = [Transition("s2a", "next", ["s2b"], order=0)]
    return Machine(states, doc)


# ---------- E1 启动：初始配置 ----------
m = build()
m.start()
ok(m.configuration == {"s1", "s1a"}, "E1-1 初始配置 = 根 + 默认路径上的所有状态")
ok(m.log["entry"] == ["s1", "s1a"], "E1-2 entryOrder：祖先先于后代（等于 document order）")

# ---------- E2 同父转移：LCCA 就是父状态，父状态不退出 ----------
m.log["exit"].clear()
ok(m.fire("go"), "E2-1 事件命中")
ok(m.configuration == {"s1", "s1b"}, "E2-2 s1 不被退出（转移域 = s1）")
ok(m.log["exit"] == ["s1a"], "E2-3 只退出源状态本身")
ok("s1" not in m.log["exit"], "E2-4 兄弟间转移不会重启父状态（这是 HFSM 与普通 FSM 的关键差异）")

# ---------- E3 跨父转移：LCCA 是根，两个分支都被退出 ----------
m.log["exit"].clear()
m.log["entry"].clear()
ok(m.fire("leave"), "E3-1 跨分支转移命中")
ok(m.log["exit"] == ["s1b", "s1"], "E3-2 exitOrder：后代先于祖先")
ok(m.log["entry"] == ["s2", "s2a"], "E3-3 entryOrder：s2 先于 s2a（根仍在配置里，无需重新进入）")
ok(m.configuration == {"s2", "s2a"}, "E3-4 新配置正确")

# ---------- E4 历史状态：离开时记录、回来时恢复 ----------
m.fire("next")
ok(m.configuration == {"s2", "s2b"}, "E4-1 走到 s2b")
m.log["exit"].clear()
ok(m.fire("leave") is False, "E4-2 s2b 上没有 leave 转移，且祖先 s2 也没有 ⇒ 无转移")
m.states["s2b"].transitions = [Transition("s2b", "back", ["s1"], order=0)]
m.states["s1"].initial = ["s1a"]
m.states["s1"].transitions = [Transition("s1", "toHistory", ["h2"], order=0)]
ok(m.fire("back"), "E4-3 s2b → s1")
ok(m.configuration == {"s1", "s1a"}, "E4-4 回到 s1 的默认子状态")
ok(m.history_value.get("h2") == ["s2b"], "E4-5 退出 s2 时记下 shallow 历史 = 直接子状态 s2b")
ok(m.fire("toHistory"), "E4-6 通过历史伪状态回到 s2")
ok(m.configuration == {"s2", "s2b"}, "E4-7 历史恢复的是 s2b 而不是默认的 s2a")

# ---------- E5 事件沿祖先链冒泡 + document order 取第一条 ----------
m2 = build()
m2.start()
m2.states["s1"].transitions = [
    Transition("s1", "go", ["s1b"], order=0),
    Transition("s1", "go", ["s2"], order=1),
]
m2.fire("go")
ok(m2.configuration == {"s1", "s1b"}, "E5-1 同一状态上多条匹配 → 取 document order 第一条")

m3 = build()
m3.start()
m3.states["s1a"].transitions = []      # 原子状态自己不处理
m3.states["s1"].transitions = [Transition("s1", "go", ["s1b"], order=0)]
m3.states[SCXML].transitions = [Transition(SCXML, "go", ["s2"], order=0)]
ok(m3.fire("go"), "E5-2 事件冒泡到祖先")
ok(m3.configuration == {"s1", "s1b"}, "E5-3 最近的祖先（s1）先命中，根的转移被忽略")

# ---------- E6 internal 转移：域就是源状态本身 ----------
m4 = build()
m4.start()
t_int = Transition("s1", "inner", ["s1b"], kind="internal", order=0)
t_ext = Transition("s1", "outer", ["s1b"], kind="external", order=1)
m4.states["s1"].transitions = [t_int, t_ext]
ok(m4.get_transition_domain(t_int) == "s1", "E6-1 internal 且目标是后代 ⇒ 域 = 源状态")
ok(m4.get_transition_domain(t_ext) == SCXML, "E6-2 external 的目标 s1b 是 s1 的后代 ⇒ 域 = LCCA = 根")
m4.log["exit"].clear()
ok(m4.fire("inner"), "E6-3 事件从 s1a 冒泡到 s1，命中 internal 转移")
ok(m4.log["exit"] == ["s1a"], "E6-4 internal：转移域是 s1 自己 ⇒ 只退出 s1a，s1 不退出也不重进")
ok(m4.configuration == {"s1", "s1b"}, "E6-5 internal 转移后的配置")
# internal 与 external 的可观测差异：external 会把源状态自己一起退出再重进
m6 = build()
m6.start()
m6.states["s1"].transitions = [Transition("s1", "outer", ["s1b"], kind="external", order=0)]
ok(m6.get_transition_domain(m6.states["s1"].transitions[0]) == SCXML, "E6-6 external 的域 = LCCA = 根")
m6.log["exit"].clear()
m6.log["entry"].clear()
ok(m6.fire("outer"), "E6-7 external 转移命中")
ok(m6.log["exit"] == ["s1a", "s1"], "E6-8 external：源状态 s1 也被退出（内部转移不会）")
ok(m6.log["entry"] == ["s1", "s1b"], "E6-9 退出后按 entryOrder 重新进入 s1 与 s1b")

# ---------- E7 targetless 转移：退出集为空，不冲突 ----------
m5 = build()
m5.start()
m5.states["s1a"].transitions = [Transition("s1a", "ping", [], order=0)]
m5.log["exit"].clear()
ok(m5.fire("ping"), "E7-1 targetless 转移被选中")
ok(m5.log["exit"] == [], "E7-2 退出集为空 ⇒ 什么都不会退出")
ok(m5.configuration == {"s1", "s1a"}, "E7-3 配置不变")

# ---------- E8 并行状态：初始配置含所有分支 ----------
states, doc = {}, {}
root = State(SCXML, None, "parallel", initial=["a", "b"])
states[SCXML] = root
doc[SCXML] = 0


def add2(sid, parent, kind, init=()):
    states[sid] = State(sid, parent, kind, initial=list(init))
    doc[sid] = len(doc)
    if parent in states:
        states[parent].children.append(sid)


add2("a", SCXML, "compound", ["a1"])
add2("a1", "a", "atomic")
add2("b", SCXML, "parallel")
add2("b1", "b", "atomic")
add2("b2", "b", "atomic")
mp = Machine(states, doc)
mp.start()
ok(mp.configuration == {"a", "a1", "b", "b1", "b2"}, "E8-1 并行状态的初始配置包含每个分支（含嵌套并行）")

# ---------- E9 并行下的冲突剔除：无祖孙关系时 document order 更早者胜 ----------
states["a1"].transitions = [Transition("a1", "boom", ["b1"], order=0)]
states["b1"].transitions = [Transition("b1", "boom", ["b1"], order=0)]
enabled = mp.select_transitions("boom")
ok(len(enabled) == 1 and enabled[0].source == "a1",
   "E9-1 退出集相交 ⇒ 只留一条；无祖孙关系时 document order 更早者胜")

# ---------- E10 后代抢占：祖先的转移先被选中，后代的后来 ⇒ 后代赢 ----------
states["a1"].transitions = []                                        # 清掉 E9 的转移，只留 b 分支
states["b1"].transitions = []                                        # b1 自己不处理 ⇒ 冒泡到 b
states["b"].transitions = [Transition("b", "boom", ["b1"], order=0)]
states["b2"].transitions = [Transition("b2", "boom", ["a1"], order=0)]
enabled2 = mp.select_transitions("boom")
ok(len(enabled2) == 1 and enabled2[0].source == "b2", "E10-1 后代源状态抢占祖先源状态")
ok(not any(t.source == "b" for t in enabled2), "E10-2 被抢占的祖先转移被移除")

# ---------- E11 findLCCA 的边界 ----------
mb = build()
ok(mb.find_lcca(["s1a", "s1b"]) == "s1", "E11-1 兄弟的 LCCA 是父状态")
ok(mb.find_lcca(["s1a", "s2a"]) == SCXML, "E11-2 跨分支的 LCCA 是根")
ok(mb.find_lcca(["s1", "s1a"]) == SCXML, "E11-3 其中一个是另一个的祖先时，LCCA 上移到根（必须是真祖先）")

print("PASS =", PASS)
