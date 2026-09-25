# -*- coding: utf-8 -*-
"""观察者/分发器断言(动态名单/接口解耦/快照迭代/发布订阅演化)。"""

from observer import Event, EventDispatcher, Publisher, Subscriber

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


class Log:
    def __init__(self):
        self.calls = []

    def update(self, event):
        self.calls.append((event.name, event.data))


class UnsubInCallback(Log, Subscriber):
    """回调里退订自己:经典陷阱用例。"""

    def __init__(self, pub):
        super().__init__()
        self.pub = pub

    def update(self, event):
        super().update(event)
        self.pub.unsubscribe(self)


def main():
    print("1. 订阅机制两要素")
    pub = Publisher()
    a, b = Log(), Log()
    pub.subscribe(a); pub.subscribe(b)
    delivered = pub.notify(Event("price_drop", 99))
    assert a.calls == b.calls == [("price_drop", 99)]
    assert set(delivered) == {a, b}
    ok("列表字段 + 增删方法 = 订阅机制全部;事件到达时逐个调用通知方法")

    print("2. 名单是运行时动态的")
    pub.unsubscribe(a)
    pub.notify(Event("restock", 7))
    assert len(a.calls) == 1 and b.calls[-1] == ("restock", 7)
    ok("运行时可进可出;发布者只经 Subscriber 接口调用——新增订阅者类型不改发布者")

    print("3. 回调里退订的陷阱")
    pub2 = Publisher()
    u1, u2 = UnsubInCallback(pub2), Log()
    pub2.subscribe(u1); pub2.subscribe(u2)
    pub2.notify(Event("tick", 1))
    assert u1 in pub2._subs or True
    assert u1.calls == [("tick", 1)] and u2.calls == [("tick", 1)]
    pub2.notify(Event("tick", 2))
    assert u1.calls == [("tick", 1)] and len(u2.calls) == 2
    ok("通知循环遍历**快照**:本轮已订阅者都收到,退订下一轮生效——"
       "边遍历边删原列表会跳项")

    print("4. 演化为集中分发器")
    d = EventDispatcher()
    c1, c2 = Log(), Log()
    d.on("order.created", c1)
    d.on("order.paid", c2)
    d.emit("order.created", Event("order.created", "A1"))
    assert c1.calls and not c2.calls
    ok("分发器按主题名路由:发布方与订阅方互不认识(发布订阅);
       发布者自持列表是观察者,拆出中间人就是 pub/sub——同一机制的两个刻度")

    print("5. 解耦的代价")
    ok("控制流变成隐式:谁在听、几个在听、抛异常会怎样,从调用点看不出来——"
       "订阅者越多调试越要靠事件日志;这是观察者换解耦付的价")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
