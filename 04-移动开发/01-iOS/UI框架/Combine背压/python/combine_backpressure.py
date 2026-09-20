"""Combine 的 Publisher / Subscriber / Demand / 背压,可执行模型。

模型口径(全部对应 Apple 官方文档,见 README 参考资料):
* publisher 只有在 subscriber 索要之后才会产出元素;
* Demand 是**累加**的:已欠 2 个,再请求 max(3) → 欠 5 个;
* 只有「真的发出一个元素」才会减少欠量;subscriber 不能请求负值;
* 任何值加到 .unlimited 上仍是 .unlimited;一旦 unlimited,后续不再有需求协商;
* sink / assign 这类便捷 subscriber 一上手就要 unlimited;
* TimerPublisher 这类自动来源也只在有欠量时才产出。
"""

UNLIMITED = "unlimited"


class Demand:
    __slots__ = ("max",)

    def __init__(self, max_value):
        self.max = max_value                    # int 或 UNLIMITED

    @staticmethod
    def none():
        return Demand(0)

    @staticmethod
    def unlimited():
        return Demand(UNLIMITED)

    def __add__(self, other):
        n = other if isinstance(other, int) else other.max
        if self.max == UNLIMITED or n == UNLIMITED:
            return Demand.unlimited()
        return Demand(self.max + n)

    def __sub__(self, n):
        if self.max == UNLIMITED:
            return Demand.unlimited()
        return Demand(max(0, self.max - n))

    def __eq__(self, other):
        if isinstance(other, Demand):
            return self.max == other.max
        if other == UNLIMITED:
            return self.max == UNLIMITED
        return self.max == other

    def __repr__(self):
        return "unlimited" if self.max == UNLIMITED else f"max({self.max})"

    def satisfied_by(self):
        return self.max == 0


class Subscription:
    def __init__(self, publisher, subscriber):
        self.publisher = publisher
        self.subscriber = subscriber
        self.unsatisfied = Demand.none()
        self.cancelled = False

    def request(self, demand):
        if demand.max != UNLIMITED and demand.max < 0:
            raise ValueError("subscriber 不能请求负值 demand")
        self.unsatisfied = self.unsatisfied + demand
        self.publisher.pump(self)

    def cancel(self):
        self.cancelled = True


class Publisher:
    """一个手里已经有一批元素、只按欠量往外发的 publisher。"""

    def __init__(self, elements, completion=True):
        self.elements = list(elements)
        self.index = 0
        self.completion = completion
        self.finished = False
        self.subscriptions = []

    def subscribe(self, subscriber):
        sub = Subscription(self, subscriber)
        self.subscriptions.append(sub)
        subscriber.receive_subscription(sub)
        return sub

    def pump(self, sub):
        while (not self.finished and sub.unsatisfied.max != 0
               and self.index < len(self.elements)):
            element = self.elements[self.index]
            self.index += 1
            sub.unsatisfied = sub.unsatisfied - 1
            extra = sub.subscriber.receive(element)
            if extra is not None and extra.max != 0:
                sub.unsatisfied = sub.unsatisfied + extra
        if (self.completion and self.index >= len(self.elements)
                and sub.unsatisfied.max != 0 and not self.finished):
            self.finished = True
            sub.subscriber.receive_completion("finished")


class Sink:
    """sink:一上手就请求 unlimited。"""

    def __init__(self, on_value=None, on_completion=None):
        self.values = []
        self.completions = []
        self._on_value = on_value
        self._on_completion = on_completion

    def receive_subscription(self, sub):
        sub.request(Demand.unlimited())         # 之后不再有任何协商

    def receive(self, value):
        self.values.append(value)
        if self._on_value:
            self._on_value(value)
        return Demand.none()

    def receive_completion(self, c):
        self.completions.append(c)
        if self._on_completion:
            self._on_completion(c)


class CustomSubscriber:
    """自定义 Subscriber:自己决定每轮要多少。"""

    def __init__(self, initial=None, per_element=Demand.none()):
        self.values = []
        self.completions = []
        self.initial = initial if initial is not None else Demand.none()
        self.per_element = per_element
        self.subscription = None

    def receive_subscription(self, sub):
        self.subscription = sub
        if self.initial.max != 0:
            sub.request(self.initial)

    def receive(self, value):
        self.values.append(value)
        return self.per_element

    def receive_completion(self, c):
        self.completions.append(c)

    def ask(self, demand):
        self.subscription.request(demand)


class TimerPublisher:
    """只在有欠量时才产出;没有欠量就干等(对应文档里的 TimerPublisher 行为)。"""

    def __init__(self, ticks):
        self.ticks = list(ticks)
        self.emitted = []
        self.subscription = None

    def subscribe(self, subscriber):
        self.subscriber = subscriber
        self.subscription = Subscription(self, subscriber)
        subscriber.receive_subscription(self.subscription)
        return self.subscription

    def pump(self, sub=None):
        while self.ticks and self.subscription.unsatisfied.max != 0:
            self.emitted.append(self.ticks.pop(0))
            self.subscription.unsatisfied = self.subscription.unsatisfied - 1
            self.subscriber.receive(self.emitted[-1])


class FlatMap:
    """Publishers.FlatMap:maxPublishers 限制**并发订阅数**。

    模型按 Combine 的需求语义实现:在飞 inner 数达到上限时**不再向上游索要**,
    等有 inner 完成腾出位置再要下一个 —— 于是上游元素不会被丢,只会被推迟。
    (官方文档只写了「maximum number of concurrent publisher subscriptions」,
    如何腾位置属模型的读法,见 README 注意事项。)
    """

    def __init__(self, upstream, max_publishers, transform):
        self.upstream = list(upstream)
        self.max_publishers = max_publishers
        self.transform = transform
        self.active = []            # [[inner_elements, cursor], ...]
        self.peak = 0
        self.output = []

    def _fill(self):
        while self.upstream and len(self.active) < self.max_publishers:
            element = self.upstream.pop(0)
            self.active.append([self.transform(element), 0])
            self.peak = max(self.peak, len(self.active))

    def run(self):
        self._fill()
        while self.active:
            for entry in list(self.active):
                inner, cursor = entry
                if cursor < len(inner):
                    self.output.append(inner[cursor])
                    entry[1] = cursor + 1
                else:
                    self.active.remove(entry)   # inner 完成 → 腾出一个并发位
            self._fill()
        return self.output


def buffer(elements, size, strategy="dropNewest"):
    """buffer(size:prefetch:whenFull:):满了以后按策略丢,或报错。"""
    kept, dropped = [], []
    for e in elements:
        if len(kept) < size:
            kept.append(e)
        elif strategy == "dropNewest":
            dropped.append(e)
        elif strategy == "dropOldest":
            dropped.append(kept.pop(0))
            kept.append(e)
        elif strategy == "error":
            raise RuntimeError("buffer overflow")
    return kept, dropped


def collect(elements, count):
    """collect(_:):攒够 count 个就发一个数组。"""
    out, batch = [], []
    for e in elements:
        batch.append(e)
        if len(batch) >= count:
            out.append(list(batch))
            batch = []
    if batch:
        out.append(list(batch))
    return out
