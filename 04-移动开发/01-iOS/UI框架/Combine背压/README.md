# Combine 背压:Publisher 什么时候才肯吐元素

Combine 和 RxSwift 最大的差别不在算子,而在**方向**:Rx 是 push,Combine 是
**pull-based** —— publisher 手里有元素也不准自己往外发,必须先由 subscriber 通过
`Demand` 索要。这条设计把「生产太快消费太慢」从运行时问题变成了**协议问题**。

`python/combine_backpressure.py` 把这套协议做成可执行模型(30 条断言),
`swift/BackpressureSubscriber.swift` 是同题 Swift 实现。

## 1. 三个角色与一次订阅

```
Subscriber.receive(subscription:)   ← 拿到 Subscription,在这里 request(initial)
        │
        ├── Subscription.request(.max(n))   ──►  publisher 的欠量 +n
        │
        ├── Subscriber.receive(value)       ──►  欠量 -1,返回值可以再 +n
        │
        └── Subscriber.receive(completion:) ──►  finished / failure
```

文档原话:**「a publisher can't send elements until the subscriber attaches and asks for
them」**,而且**「Publishers only emit values when explicitly requested to do so by
subscribers」** —— 这两句就是 Combine 的全部前提。

## 2. `Demand` 的四条算术规则

| 规则 | 模型断言 |
| --- | --- |
| **累加**:欠 2 个再要 `max(3)` → 欠 **5** 个 | 02 |
| **只有发出元素才减少欠量** | 04 |
| **subscriber 不能请求负值** | 06 |
| **任何值加到 `.unlimited` 上仍是 `.unlimited`** | 03 |

`unlimited` 减 1 也还是 `unlimited`(05)—— 它不是一个很大的数,是**另一种状态**。

## 3. 零欠量:订阅关系成立,但一个元素都不发

这是最反直觉的一点。模型里 `Publisher([1…5])` 被订阅后,只要没人索要:

* `sub.values == []`(07),但订阅关系确实建好了(08);
* 对应文档里的 `TimerPublisher`:**订阅之后 5 秒不索要,就一个元素都不产**(18)。

也就是说,**「订阅」和「开始流动」是两件事**。

## 4. 请求 3 个:恰好发 3 个,而且**不会结束**

文档里的例子请求 `.max(3)`,结果是「第三个之后不再发,但**也不发 `finished`**」——
因为 publisher 只是在等欠量。模型逐条验过(09–12):

| 步骤 | `values` | `completions` |
| --- | --- | --- |
| 订阅并请求 `max(3)` | `[1, 2, 3]` | `[]` |
| 补要 `max(2)` | `[1, 2, 3, 4, 5]` | `["finished"]` |

想细水长流就在 `receive(_:)` 里返回新的 `Demand`(13–14):每收到一个再要一个,
流会一直跑到自然结束。

## 5. `sink` / `assign`:一上手就要 unlimited

便捷 subscriber 索要的是 **`.unlimited`**,所以:

* 元素会被一次性全部推给你(15);
* **之后不再有任何需求协商**(16、17)。

文档因此列了三条要求:`sink` 的闭包和 `assign` 的副作用**不能阻塞 publisher**、
**不能自己缓冲**、**不能被压垮**。对 UI 事件、只发一次的 `DataTaskPublisher` 没问题;
对高频来源就要小心。

## 6. 不写自定义 Subscriber 也能做背压

文档给的替代方案是「用算子把速率降下来,再接一个 unlimited 的 sink」:

| 算子 | 作用 | 模型断言 |
| --- | --- | --- |
| `buffer(size:)` | 固定容量,满了按策略丢**或报错** | 21–24 |
| `debounce` | 上游停下来一段时间才发 | — |
| `throttle` | 限速,每个区间只放最新/最旧的一个 | — |
| `collect` | 攒够 N 个发一个数组 | 25 |

`buffer` 的满溢策略里「报错」这一条最容易被忽略:它不只是「丢数据」,
也可以让整条 pipeline 以失败终止。

## 7. `flatMap(maxPublishers:)`:限制的是**并发订阅数**

文档对 `maxPublishers` 的定义只有一句:the maximum number of concurrent publisher
subscriptions。模型按 Combine 的需求语义实现 —— 在飞 inner 数达到上限时**不再向上游索要**,
腾出位置再要下一个:

| `maxPublishers` | 并发峰值 | 上游元素 |
| --- | --- | --- |
| 2 | 2 | 一个不丢,只是被推迟(26–28) |
| 5 | 5 | 同上(29–30) |

**关键区别:它是背压信号,不是丢弃策略。** 上限再小,最终元素集合也不变,
变的只是「同时在飞几个 inner」。

> 口径说明:官方只写了「并发订阅数上限」,**没有**写「达到上限后怎么处理」。
> 模型取「停止向上游索要」这一读法,与 Combine 整体的需求语义一致;
> 若实现选择丢弃上游元素,观察到的行为会不同。

## 8. 运行方式

```bash
cd python && python selfcheck_combine_backpressure.py    # 30 条断言
```

Swift 侧 `swift/BackpressureSubscriber.swift` 只作人工对照(本机无 Swift 工具链)。

## 9. 关键代码

```python
def pump(self, sub):
    while (not self.finished and sub.unsatisfied.max != 0
           and self.index < len(self.elements)):
        element = self.elements[self.index]
        self.index += 1
        sub.unsatisfied = sub.unsatisfied - 1        # 只有发元素才减欠量
        extra = sub.subscriber.receive(element)
        if extra is not None and extra.max != 0:
            sub.unsatisfied = sub.unsatisfied + extra   # 返回值再累加上去
```

## 10. 性能与适用边界

* 背压的代价是**一次额外的往返**:每个元素都要等 subscriber 表态。对 UI 事件(每秒几十个)
  完全可以忽略;对每秒几十万条的流式数据源,`sink` + `unlimited` 会直接把内存吃满。
* 一旦有人要了 `unlimited`,**整条上游链路的背压就断了**,下游再加什么算子都来不及。
* 模型是单线程确定性的:真实 Combine 里 `Subscription.request` 可以来自任意线程,
  多个观察者「可以并发执行」(文档原话),欠量更新需要外部同步。

## 11. 注意事项与常见坑

1. **忘记 `request` 是最常见的「收不到值」**:代码看着订阅了,实际欠量是 0。
2. **`receive(_:)` 返回 `.none` 不等于「到此为止」**,只是这一轮不再续要;
   后面还能用 `subscription.request` 补。
3. `.max(n)` 不是「上限」,是**增量**。想「总共只要 3 个」必须自己计数。
4. **`sink` 拿到的 `AnyCancellable` 要持有**,出了作用域就被 cancel,表现为「没反应」。
5. `buffer` 满了默认不是阻塞,是**丢**;要报错得显式选策略。
6. `flatMap` 的 inner publisher 不完成,并发位就永远不释放 —— 上游会一直被卡住。

## 参考资料

* Apple《Processing Published Elements with Subscribers》(背压与 Demand 语义)
  <https://developer.apple.com/tutorials/data/documentation/combine/processing-published-elements-with-subscribers.json>
* Apple《Combine》框架概览
  <https://developer.apple.com/tutorials/data/documentation/combine.json>
* Apple《Subscribers.Demand》
  <https://developer.apple.com/tutorials/data/documentation/combine/subscribers/demand.json>
* Apple《Publishers.FlatMap》
  <https://developer.apple.com/tutorials/data/documentation/combine/publishers/flatmap.json>
