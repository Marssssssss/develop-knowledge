from combine_backpressure import *

# Combine 背压 自检:python selfcheck_combine_backpressure.py
# 模型语义见 combine_backpressure.py

def _ck(label, cond, detail=""):
    if not cond:
        raise AssertionError(f"{label} 失败: {detail}")


def run_selfcheck():
    n = 0

    def ok(label, cond, detail=""):
        nonlocal n
        n += 1
        _ck(label, cond, detail)
        print(f"ok {n:02d} - {label}")

    # --- 1. Demand 的算术
    ok("none + max(3) = max(3)", (Demand.none() + Demand(3)) == 3)
    ok("max(2) + max(3) = max(5)(累加)", (Demand(2) + Demand(3)) == 5)
    ok("任何值加到 unlimited 上仍是 unlimited",
       (Demand.unlimited() + Demand(5)).max == UNLIMITED)
    ok("max(5) 减 1 得 max(4)(只有发元素才减欠量)", (Demand(5) - 1) == 4)
    ok("unlimited 减 1 仍是 unlimited", (Demand.unlimited() - 1).max == UNLIMITED)
    try:
        Subscription(None, None).request(Demand(-1))
        raised = False
    except ValueError:
        raised = True
    ok("请求负值 demand 被拒", raised)

    # --- 2. 零欠量:publisher 存在也不产元素
    pub = Publisher([1, 2, 3, 4, 5])
    sub = CustomSubscriber(initial=Demand.none())
    pub.subscribe(sub)
    ok("subscribe 之后零欠量 → 一个元素都不发", sub.values == [], sub.values)
    ok("但订阅关系确实建立了", len(pub.subscriptions) == 1)

    # --- 3. 请求 3 个:只发 3 个,而且不 finish
    pub = Publisher([1, 2, 3, 4, 5])
    sub = CustomSubscriber(initial=Demand(3))
    pub.subscribe(sub)
    ok("请求 max(3) → 恰好收到 3 个", sub.values == [1, 2, 3], sub.values)
    ok("第 4 个及之后不会自己送上门", 4 not in sub.values)
    ok("publisher 只是等欠量,不发 finished", sub.completions == [], sub.completions)
    sub.ask(Demand(2))
    ok("补要 2 个后接着发第 4、5 个", sub.values == [1, 2, 3, 4, 5], sub.values)

    # --- 4. 在 receive 里返回新欠量:细水长流
    pub = Publisher(list(range(10)))
    sub = CustomSubscriber(initial=Demand(1), per_element=Demand(1))
    pub.subscribe(sub)
    ok("每收到一个再补 1 个 → 流完整跑完", sub.values == list(range(10)), sub.values)
    ok("跑完后收到 finished", sub.completions == ["finished"], sub.completions)

    # --- 5. sink:一上手就要 unlimited,此后无协商
    pub = Publisher([1, 2, 3, 4, 5])
    sink = Sink()
    s = pub.subscribe(sink)
    ok("sink 一次性拿到全部元素", sink.values == [1, 2, 3, 4, 5], sink.values)
    ok("sink 的欠量是 unlimited", s.unsatisfied.max == UNLIMITED)
    sink.ask = None
    s.request(Demand(3))
    ok("已经是 unlimited 之后再请求也不改变局面", s.unsatisfied.max == UNLIMITED)

    # --- 6. 自动来源也只在有欠量时产出(TimerPublisher)
    timer = TimerPublisher(["t1", "t2", "t3"])
    sub = CustomSubscriber(initial=Demand.none())
    timer.subscribe(sub)
    ok("Timer 有订阅但零欠量 → 不产出", timer.emitted == [], timer.emitted)
    sub.ask(Demand(2))
    ok("要 2 个才产出 2 个", timer.emitted == ["t1", "t2"], timer.emitted)
    sub.ask(Demand.unlimited())
    ok("改要 unlimited 后把剩下的也发完", timer.emitted == ["t1", "t2", "t3"], timer.emitted)

    # --- 7. 用 buffer / collect 在不写自定义 Subscriber 的前提下做背压
    kept, dropped = buffer(list(range(10)), 3)
    ok("buffer(size:3) 只留 3 个", kept == [0, 1, 2], kept)
    ok("满了以后新来的被丢掉", len(dropped) == 7, dropped)
    kept, dropped = buffer(list(range(10)), 3, strategy="dropOldest")
    ok("dropOldest 留下的是最后 3 个", kept == [7, 8, 9], kept)
    try:
        buffer(list(range(10)), 3, strategy="error")
        raised = False
    except RuntimeError:
        raised = True
    ok("满溢策略也可以是报错", raised)
    ok("collect(3) 把 10 个元素打成 4 批",
       collect(list(range(10)), 3) == [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]])

    # --- 8. flatMap(maxPublishers:) 限制的是并发订阅数
    expected = sorted([1, 10, 2, 20, 3, 30, 4, 40, 5, 50])
    fm = FlatMap([1, 2, 3, 4, 5], max_publishers=2, transform=lambda e: [e, e * 10])
    out = fm.run()
    ok("maxPublishers=2 时并发订阅数不超过 2", fm.peak == 2, fm.peak)
    ok("限制的是并发数,上游元素一个不丢(只是被推迟)",
       sorted(out) == expected, out)
    ok("并发位腾出后才向上游要下一个",
       out.index(3) > out.index(20) and out.index(4) > out.index(3), out)

    fm_big = FlatMap([1, 2, 3, 4, 5], max_publishers=5, transform=lambda e: [e, e * 10])
    fm_big.run()
    ok("maxPublishers 放大到 5 后并发峰值就是 5", fm_big.peak == 5, fm_big.peak)
    ok("并发上限不影响最终元素集合", sorted(fm_big.output) == expected)

    print(f"\n全部 {n} 条断言通过")
    return n


if __name__ == "__main__":
    run_selfcheck()
