# -*- coding: utf-8 -*-
"""cProfile 输出解读复刻：确定性插桩事件流 → ncalls(total/primitive)/tottime/cumtime/percall。

口径（Python 官方文档 The Python Profilers，本轮实读）：
  - 确定性剖析：**所有**函数调用、返回、异常事件都被监控，事件间精确计时；
    统计性剖析则随机采样指令指针（本模块不做）
  - tottime：该函数内花费的总时间，**不含**调用子函数的时间
  - cumtime：该函数及所有子函数的累计时间（从调用到退出），"对递归函数也准确"；
    该特殊处理使"递归实现与迭代实现的统计可直接比较"
  - ncalls 列 total/primitive 双数字：primitive = 非递归引起的调用
    （"the call was not induced via recursion"）；不递归时只印单个数字
  - percall 两个：tottime/ncalls 与 cumtime/primitive calls
  - pstats：strip_dirs 去路径、sort_stats(SortKey.TIME/CUMULATIVE/CALLS)、
    print_stats 限制（int 行数 / 0.0-1.0 小数百分比 / 正则）
  - 指标用途（文档原话）：调用计数 → 找 bug 与内联展开点；内部时间 → 热循环；
    累计时间 → 高层算法选型错误
"""

CALL, RET = "call", "return"


class FuncStat:
    def __init__(self):
        self.ncalls = 0          # 总调用次数
        self.primitive = 0       # 非递归调用次数
        self.tottime = 0.0
        self.cumtime = 0.0
        self.depth = 0           # 当前在栈上的深度（判 primitive 用）
        self.prim_start = 0.0    # 当前 primitive 调用的进入时刻


def run_events(events):
    """确定性插桩引擎：事件间时长记给栈顶函数（= tottime 的"不含子函数"口径）。"""
    stats, stack, last = {}, [], 0.0
    for kind, func, t in events:
        if kind == CALL:
            if stack:                              # 事件间隙归栈顶（正在执行者）
                stats[stack[-1]].tottime += t - last
            last = t
            st = stats.setdefault(func, FuncStat())
            st.ncalls += 1
            if st.depth == 0:                      # 首次进入：primitive 调用
                st.primitive += 1
                st.prim_start = t
            st.depth += 1
            stack.append(func)
        elif kind == RET:
            if stack:
                stats[stack[-1]].tottime += t - last
            last = t
            func = func if func else (stack[-1] if stack else None)
            st = stats[func]
            st.depth -= 1
            stack.pop()
            if st.depth == 0:                      # 关闭 primitive 调用：计 cumtime
                st.cumtime += t - st.prim_start
    return stats


def ncalls_str(st):
    """real pstats 输出口径：不递归只印单数字；递归印 total/primitive。"""
    if st.primitive == st.ncalls:
        return str(st.ncalls)
    return "%d/%d" % (st.ncalls, st.primitive)


class Stats:
    """pstats.Stats 的最小复刻：键 (file, lineno, funcname)。"""

    def __init__(self, stats, keys):
        self.rows = [(k, stats[k[2]]) for k in keys]

    def strip_dirs(self):
        """去掉文件名的路径前缀（文档：modifies the object，信息丢失）。"""
        import os
        self.rows = [((os.path.basename(f), ln, fn), st) for (f, ln, fn), st in self.rows]
        return self

    def sort_stats(self, key):
        order = {"TIME": lambda st: -st.tottime, "CUMULATIVE": lambda st: -st.cumtime,
                 "CALLS": lambda st: -st.ncalls}
        self.rows.sort(key=lambda kv: (order[key](kv[1]), kv[0][2]))
        return self

    def print_stats(self, restrict=None):
        """int=前 N 行；float=行数占比；str=按函数名子串过滤。"""
        rows = self.rows
        if isinstance(restrict, int):
            rows = rows[:restrict]
        elif isinstance(restrict, float):
            rows = rows[:max(1, int(restrict * len(rows)))]
        elif isinstance(restrict, str):
            rows = [kv for kv in rows if restrict in kv[0][2]]
        out = []
        for (f, ln, fn), st in rows:
            per1 = st.tottime / st.ncalls if st.ncalls else 0.0
            per2 = st.cumtime / st.primitive if st.primitive else 0.0
            out.append("%s %g %g %g %g %s:%d(%s)" %
                       (ncalls_str(st), st.tottime, per1, st.cumtime, per2, f, ln, fn))
        return out


def fname(row):
    """从 b.py:20(B) 形态的输出行提取函数名。"""
    return row.split("(")[-1].rstrip(")")


def usage_hint(st):
    """文档三句用途指引：计数→bug/内联；内部时间→热循环；累计时间→算法选型。"""
    hints = []
    if st.ncalls >= 1000:
        hints.append("inline-candidate")
    if st.tottime > 10:
        hints.append("hot-loop")
    if st.cumtime > 50:
        hints.append("algorithm-choice")
    return hints


CASE1 = [  # A 调 B 两次，B 各调 C 一次
    (CALL, "A", 0), (CALL, "B", 2), (CALL, "C", 5), (RET, "C", 7), (RET, "B", 9),
    (CALL, "B", 10), (CALL, "C", 12), (RET, "C", 13), (RET, "B", 15), (RET, "A", 18),
]

CASE2 = [  # 递归：F 直接调 F
    (CALL, "F", 0), (CALL, "F", 1), (RET, "F", 2), (RET, "F", 3),
]


def main():
    s = run_events(CASE1)
    # 1. tottime：事件间隙记给执行中函数（不含子函数）
    assert s["A"].tottime == 6 and s["B"].tottime == 9 and s["C"].tottime == 3
    # 2. cumtime：从调用到退出（含子函数）
    assert s["A"].cumtime == 18 and s["B"].cumtime == 12 and s["C"].cumtime == 3
    # 3. tottime 之和 == 总时长（确定性剖析把全部时间归了账）
    assert abs(sum(x.tottime for x in s.values()) - 18.0) < 1e-9
    # 4. "不含子函数"的算术验证：B 的 cumtime - tottime == C 的 tottime
    assert abs((s["B"].cumtime - s["B"].tottime) - s["C"].tottime) < 1e-9
    # 5. percall：tottime/ncalls 与 cumtime/primitive
    assert abs(s["B"].tottime / s["B"].ncalls - 4.5) < 1e-9
    assert abs(s["B"].cumtime / s["B"].primitive - 6.0) < 1e-9
    # 6. 无递归 → ncalls 只印单数字；有递归 → total/primitive
    assert ncalls_str(s["B"]) == "2" and ncalls_str(s["A"]) == "1"
    r = run_events(CASE2)
    assert ncalls_str(r["F"]) == "2/1"
    # 7. 递归的 cumtime 特殊处理：只按 primitive 调用记账 → 递归与迭代可直接比较
    assert r["F"].tottime == 3 and r["F"].cumtime == 3 and r["F"].primitive == 1
    assert abs(r["F"].cumtime / r["F"].primitive - 3.0) < 1e-9

    # 8. pstats：sort TIME vs CUMULATIVE 顺序不同
    keys = [("/app/pkg/a.py", 10, "A"), ("/app/pkg/b.py", 20, "B"), ("/app/pkg/c.py", 30, "C")]
    ps = Stats(s, keys)
    by_time = [fname(row) for row in ps.sort_stats("TIME").print_stats()]
    assert by_time == ["B", "A", "C"], by_time          # tottime: 9 > 6 > 3
    by_cum = [fname(row) for row in ps.sort_stats("CUMULATIVE").print_stats()]
    assert by_cum == ["A", "B", "C"], by_cum            # cumtime: 18 > 12 > 3
    by_calls = [fname(row) for row in ps.sort_stats("CALLS").print_stats()]
    assert by_calls == ["B", "C", "A"], by_calls        # ncalls: 2 = 2 > 1（平局按名）

    # 9. strip_dirs 去路径
    ps2 = Stats(s, keys).strip_dirs()
    assert ps2.rows[0][0][0] == "a.py", ps2.rows[0][0]

    # 10. print_stats 限制：int / float / 正则子串
    ps.sort_stats("TIME")
    assert len(ps.print_stats(2)) == 2
    assert len(ps.print_stats(0.5)) == 1                # 3 行截断取 50% → 1 行（至少 1 行）
    assert [fname(row) for row in ps.print_stats("A")] == ["A"]
    row = ps.print_stats()[0]
    assert row.startswith("2 9 4.5 12 6 /app/pkg/b.py:20(B)"), row  # 完整行格式

    # 11. 指标用途指引（文档三句原话的映射）
    class Fake:
        pass
    hot = Fake()
    hot.ncalls, hot.tottime, hot.cumtime = 5000, 20, 80
    assert usage_hint(hot) == ["inline-candidate", "hot-loop", "algorithm-choice"]
    warm = Fake()
    warm.ncalls, warm.tottime, warm.cumtime = 10, 1, 2
    assert usage_hint(warm) == []

    # 12. 异常路径的口径（文档：函数调用/返回/**异常**事件都被监控）
    #     模拟：RET 事件函数名为 None → 弹出当前栈顶（异常展开也走返回语义）
    s3 = run_events([(CALL, "A", 0), (CALL, "B", 1), (RET, None, 4), (RET, "A", 6)])
    assert s3["A"].tottime == 3 and s3["B"].tottime == 3
    assert s3["B"].cumtime == 3 and s3["A"].cumtime == 6

    print("cprofile_stats: 12 组断言全部通过")


if __name__ == "__main__":
    main()
