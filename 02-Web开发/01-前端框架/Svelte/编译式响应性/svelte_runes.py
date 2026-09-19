# -*- coding: utf-8 -*-
"""
Svelte 5 runes 最小运行时（Python 复刻）

权威依据（本文件每条机制都可在下列官方文档中逐条对上）：
  - https://cdn.jsdelivr.net/gh/sveltejs/svelte@main/documentation/docs/02-runes/01-what-are-runes.md
  - .../02-runes/02-$state.md      （深代理、原对象不被改写、解构即失去响应性）
  - .../02-runes/03-$derived.md    （依赖 = 同步读到的 state；标脏后下次读取才重算）
  - .../02-runes/04-$effect.md     （挂载后 microtask 运行、重跑批处理、teardown 先于重跑）

模型要点：
  Source   —— 一个可订阅槽位（$state 的原子），带 version
  Derived  —— $derived：pull 语义，标脏后「下次读取」才重算（不是 push 重算）
  Effect   —— $effect：挂载后运行，重跑走批处理队列，且排在 DOM 更新 effect 之后
  StateProxy —— $state 的对象/数组形式：写时只改 Source，原对象永不被 mutate
"""

_cur = []          # 依赖收集栈
_queue = []        # 待 flush 的 effect
_microtask = False


def read(src):
    """读一个 Source：当前消费者登记依赖 + 读取时的 version，并反向订阅。"""
    if _cur:
        c = _cur[-1]
        c.deps[id(src)] = (src, src.version)
        src.sub(c)
        c.subscribed.add(src)
    return src.value


class Source:
    __slots__ = ("value", "version", "subs")

    def __init__(self, value):
        self.value = value
        self.version = 0
        self.subs = set()

    def set(self, v):
        if v == self.value:
            return False
        self.value = v
        self.version += 1
        for s in list(self.subs):
            s.notify()
        return True

    def sub(self, c):
        self.subs.add(c)


class Consumer:
    def __init__(self):
        self.deps = {}          # id -> (obj, version_at_read)
        self.subscribed = set()  # 反向订阅表，重算前必须先解绑旧依赖

    def unsubscribe_all(self):
        """重新收集依赖前解绑：否则切走的分支仍会被旧 Source 唤醒（伪依赖）。"""
        for s in self.subscribed:
            s.subs.discard(self)
        self.subscribed.clear()

    def stale(self):
        return any(o.version != v for o, v in self.deps.values())


class Derived(Consumer):
    """$derived：dirty 标记 + 读取时重算（lazy pull）。"""

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.value = None
        self.version = 0
        self.dirty = True
        self.recomputes = 0
        self.downstream = set()

    def get(self):
        if self.dirty or self.stale():
            self.recompute()
        if _cur:
            _cur[-1].deps[id(self)] = (self, self.version)
            self.downstream.add(_cur[-1])
        return self.value

    def notify(self):
        """被上游 Source 通知：只标脏，绝不立刻算——这就是 pull 语义的关键。"""
        self.dirty = True
        for e in list(self.downstream):
            schedule(e)

    def recompute(self):
        self.unsubscribe_all()
        self.deps = {}
        _cur.append(self)
        try:
            new = self.fn()
        finally:
            _cur.pop()
        self.dirty = False
        self.recomputes += 1
        if new != self.value:
            self.value = new
            self.version += 1          # 只有值真的变了才推进 version


class Effect(Consumer):
    """$effect：prio=0 表示框架内部的模板/DOM 更新，prio=1 表示用户 effect。"""

    def __init__(self, fn, prio=1):
        super().__init__()
        self.fn = fn
        self.prio = prio
        self.teardown = None
        self.runs = 0
        self.td_runs = 0

    def run(self):
        self.unsubscribe_all()
        if self.teardown is not None:
            self.teardown()
            self.teardown = None
            self.td_runs += 1
        self.deps = {}
        _cur.append(self)
        try:
            r = self.fn()
        finally:
            _cur.pop()
        self.runs += 1
        if callable(r):
            self.teardown = r

    def notify(self):
        for e in _queue:
            if e is self:
                return
        _queue.append(self)


def schedule(e):
    e.notify()


def flush():
    """microtask 边界：模板更新先于用户 effect；derived 先求值，值没变则 effect 不重跑。"""
    global _queue
    for _ in range(16):
        pending = sorted(_queue, key=lambda x: x.prio)
        _queue = []
        if not pending:
            return
        for e in pending:
            for o, _v in list(e.deps.values()):
                if isinstance(o, Derived):
                    o.get()            # 触发重算并把 version 推进到最新
            if e.stale():
                e.run()
    return


class StateProxy:
    """$state 的对象形式：写只落在 Source 上，`_obj` 从头到尾不被改写。"""

    def __init__(self, obj):
        object.__setattr__(self, "_obj", obj)
        object.__setattr__(self, "_src", {})
        object.__setattr__(self, "_proxies", {})
        object.__setattr__(self, "_len_src", Source(len(obj)))

    def _initial(self, k):
        try:
            return self._obj[k]
        except (KeyError, IndexError):
            return None

    def __getitem__(self, k):
        src = self._src.get(k)
        if src is None:
            src = Source(self._initial(k))
            self._src[k] = src
        v = read(src)
        if isinstance(v, (dict, list)):
            p = self._proxies.get(k)
            if p is None:
                p = StateProxy(v)
                self._proxies[k] = p
            return p
        return v

    def __setitem__(self, k, v):
        src = self._src.get(k)
        if src is None:
            src = Source(self._initial(k))
            self._src[k] = src
        self._proxies.pop(k, None)
        src.set(v)

    def __len__(self):
        # 长度本身是一个可订阅槽位：$state 的数组 length 变化必须能唤醒读者
        return read(self._len_src)

    def append(self, v):
        k = self._len_src.value
        self.__setitem__(k, v)
        self._len_src.set(k + 1)


def state(v):
    """标量 $state 直接是一个 Source；对象/数组返回深代理。"""
    if isinstance(v, (dict, list)):
        return StateProxy(v)
    return Source(v)


def dget(x):
    """统一取值：Source 读 value（并登记依赖），Derived 走 get()。"""
    if isinstance(x, Source):
        return read(x)
    return x.get()
