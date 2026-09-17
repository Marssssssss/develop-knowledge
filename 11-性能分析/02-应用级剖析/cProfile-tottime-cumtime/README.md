# cProfile 输出解读：tottime vs cumtime、ncalls 的 total/primitive

## 简介

- `cProfile` 是 Python 官方推荐的剖析器（C 扩展实现、开销适中），输出里最容易读错的就是两列时间：**tottime 不含子函数、cumtime 含子函数**；以及 ncalls 列的 `total/primitive` 双数字。
- 核心价值：确定性插桩给出**精确的调用计数与时间账本**（采样剖析只能给相对占比），tottime 之和恰等于总时长——每一微秒都归了账。
- 关键概念：
  - **确定性 vs 统计性**：cProfile 监控所有调用/返回/异常事件并精确计时；统计性剖析随机采样指令指针
  - **primitive call**：非递归引起的调用（"the call was not induced via recursion"）
  - **两个 percall**：tottime/ncalls 与 cumtime/primitive calls（分母不同！）
  - **递归的 cumtime 特殊处理**：只按 primitive 调用记账，"对递归函数也准确"

## 原理详解

依据 Python 官方文档《The Python Profilers》（docs.python.org/3/library/profile.html，本轮实读）：

1. **确定性剖析的定义**（文档原文）："all *function call*, *function return*, and *exception* events are monitored, and precise timings are made for the intervals between these events"。Python 解释器本身对每个事件提供钩子，所以**不需要额外插桩代码**就能做确定性剖析——这也是"解释型语言的开销反而让确定性剖析只增加少量成本"的原因。
2. **tottime**："for the total time spent in the given function (and **excluding** time made in calls to sub-functions)"。实现口径：**事件之间的时间记给当前栈顶函数**——call 进入时把间隙记给调用者，return 时把间隙记给返回者自身。
3. **cumtime**："is the cumulative time spent in this and all subfunctions (from invocation till exit). This figure is accurate **even for recursive functions**"。实现口径：只在**关闭一次 primitive 调用**（该函数深度从 1 归 0）时记账 `t_return − t_primitive进入`——递归的内层返回不重复累计。
4. **文档明说这是"特殊处理"**："the unusual handling of cumulative times in this profiler allows statistics for recursive implementations of algorithms to be **directly compared to iterative implementations**"——纯递归 fib 的 cumtime == 整棵递归树的墙钟时间，与等价迭代版可直接对比。
5. **ncalls 列**：不递归时只印单数字；递归时印 `total/primitive`（文档示例 `3/1`：共 3 次调用、其中 1 次非递归进入）。
6. **两个 percall 的分母不同**：第一个 = tottime ÷ ncalls（**总**调用数），第二个 = cumtime ÷ **primitive** calls。看平均单次成本时用第一个，看"单次顶层调用的总成本"用第二个。
7. **pstats 三板斧**：`strip_dirs()`（去文件路径、压窄输出）、`sort_stats(SortKey.TIME/CUMULATIVE/CALLS)`（TIME=tottime；官方建议用 SortKey 枚举而非字符串，更不易错）、`print_stats(*restrictions)`（int=前 N 行；0.0–1.0 浮点=行数占比；字符串=正则匹配）。
8. **三列指标各自的用途**（文档原话）：
   - 调用计数 → 找 bug（意外计数）与**内联展开点**（高调用数）
   - 内部时间（tottime）→ 找该精打细算的**热循环**
   - 累计时间（cumtime）→ 找**算法选型**层面的高层错误
9. **cProfile vs profile**：前者 C 扩展（基于 lsprof）"reasonable overhead"、适合长跑程序、不可校准；后者纯 Python、开销显著、可 `calibrate()` 校准、便于扩展剖析器本身。另注意口径：**剖析开销加在 Python 代码上而不加在 C 级函数上**——C 代码会显得"格外快"。

```
事件流（确定性插桩）              账本
call A @0 ─┐                     tottime: 事件间隙 → 当前栈顶
call B @2 ─┤ 间隙 0→2 记给 A      cumtime: primitive 调用闭合时
call C @5 ─┤ 间隙 2→5 记给 B              t_return − t_enter
ret   C @7 ─┤ 间隙 5→7 记给 C      ncalls: total / primitive
ret   B @9 ─┘ 间隙 7→9 记给 B      恒等式: Σtottime == 总时长
```

## 对比 / 选型

| 维度 | cProfile（确定性） | py-spy（统计性采样） |
| --- | --- | --- |
| 精确计数 | 精确（每次调用都记） | 只有采样占比 |
| 目标进程扰动 | 中（进程内跑钩子） | 近零（旁观进程） |
| 需要重启/改代码 | 是 | 否 |
| 短命函数 | 也能捕捉 | 可能采不到 |

## 环境准备

- Python ≥ 3.10（demo 为纯逻辑模拟，不 import cProfile）
- Go ≥ 1.21（Go 版本机无工具链，走人工审查 + 括号配平）

## 运行方式

```bash
python3 python/cprofile_stats.py   # 12 组断言
# go run go/cprofile_stats.go      # 同 12 组断言
```

## 关键代码片段

```python
if kind == CALL:
    if stack:                          # 事件间隙归栈顶（正在执行者）
        stats[stack[-1]].tottime += t - last
    last = t
    st.ncalls += 1
    if st.depth == 0:                  # 首次进入：primitive 调用
        st.primitive += 1
        st.prim_start = t
    st.depth += 1
    stack.append(func)
elif kind == RET:
    ...
    if st.depth == 0:                  # 关闭 primitive 调用才计 cumtime
        st.cumtime += t - st.prim_start
```

## 性能与边界

- cProfile 开销"reasonable"但**非零**：全部 Python 调用都过一遍 C 级钩子，调用密度极高的程序被拖慢的比例更大。
- 精确计时的分辨率受时钟精度限制；极短函数的 tottime 可能全为 0（但 ncalls 仍然精确）。
- C 级函数不产生事件：`sorted` 内部的 C 排序、numpy 调用等对 cProfile 不可见——**会显得比真实更快**（文档明示的偏差口径）。

## 注意事项与常见坑

- **percall 两列分母不同**：`tottime/ncalls` 与 `cumtime/primitive`——递归函数第二个 percall 是"平均每次顶层调用"，不是"平均每次调用"。
- **递归时 cumtime 不会膨胀**：如果按"每次 return 都累计"理解，fib(30) 的 cumtime 会爆炸；实际只按 primitive 调用记账（本 demo 断言 7 用 2/1 递归用例验证）。
- **tottime 排序找热点自身，cumtime 排序找算法层**：只看默认输出容易把"入口函数 cumtime 巨大"误当热点自身——先 `sort_stats(SortKey.TIME)` 再看。
- **异常路径也产生事件**：异常展开等价于一系列 return，时间照记账（本 demo 断言 12 用无名 return 模拟）。
- **别拿 cProfile 的绝对时间做基准测试**：它的开销会改变程序的时间分布，测性能用 timeit/benchmark。

## 参考资料（实际阅读过的权威来源）

- [The Python Profilers - Python 3 官方文档](https://docs.python.org/3/library/profile.html) — tottime/cumtime/percall 列定义、primitive calls、确定性 vs 统计性、递归 cumtime 特殊处理、pstats 用法、指标用途三句话
- [benfred/py-spy README](https://github.com/benfred/py-spy) — 统计性剖析的对极参照（采样指令指针口径）
