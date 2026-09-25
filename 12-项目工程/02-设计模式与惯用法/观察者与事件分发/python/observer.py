# -*- coding: utf-8 -*-
"""观察者与事件分发(发布订阅)模型。

口径(实读源):refactoring.guru Observer 页——
subject/publisher 持订阅者列表、订阅机制两要素(列表字段+增删方法)、
经统一接口通知、订阅名单运行时动态、以及『升级为集中式事件分发器,
让任意对象都能当发布者』的演化路径。
"""


class Event:
    def __init__(self, name, data):
        self.name = name
        self.data = data


class Subscriber:
    def update(self, event):  # pragma: no cover
        raise NotImplementedError


class Publisher:
    """订阅机制 = 订阅者列表 + 增删方法;通知只走统一接口。"""

    def __init__(self):
        self._subs = []

    def subscribe(self, sub):
        self._subs.append(sub)

    def unsubscribe(self, sub):
        self._subs.remove(sub)

    def notify(self, event):
        delivered = []
        for sub in list(self._subs):        # 快照迭代:回调里退订不影响本轮
            sub.update(event)
            delivered.append(sub)
        return delivered


class EventDispatcher:
    """集中式分发器:发布方与订阅方互不相识,只认『主题名』。"""

    def __init__(self):
        self._topics = {}

    def on(self, topic, sub):
        self._topics.setdefault(topic, []).append(sub)

    def emit(self, topic, event):
        return [s.update(event) for s in list(self._topics.get(topic, []))]
