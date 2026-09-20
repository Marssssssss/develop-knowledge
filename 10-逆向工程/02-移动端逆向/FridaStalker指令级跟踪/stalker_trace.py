"""Frida Stalker 的行为模型：GumEvent 二进制布局、事件位掩码、transform 迭代器、
trustThreshold / exclude / 事件队列 / call probe。

对照来源：
  - Frida JavaScript API 文档的 Stalker 一节（frida.re/docs/javascript-api/）
  - frida-gum 源码 gum/gumevent.h（GumEventType 枚举与六个事件结构体）
"""

# ---------- gum/gumevent.h：GumEventType ----------

GUM_NOTHING = 0
GUM_CALL = 1 << 0
GUM_RET = 1 << 1
GUM_EXEC = 1 << 2
GUM_BLOCK = 1 << 3
GUM_COMPILE = 1 << 4

EVENT_NAMES = {
    GUM_CALL: "call",
    GUM_RET: "ret",
    GUM_EXEC: "exec",
    GUM_BLOCK: "block",
    GUM_COMPILE: "compile",
}

PTR = 8          # 64 位进程上的 gpointer 宽度
TYPE_W = 4       # GumEventType 是 guint


def _pad(n):
    """gpointer 要求 8 字节对齐：guint 之后要补到 8 的倍数。"""
    return (-n) % PTR


def event_struct_size(kind):
    """按 gumevent.h 的字段顺序计算结构体大小（含对齐填充）。"""
    size = TYPE_W
    if kind == GUM_NOTHING:
        return TYPE_W                                   # GumAnyEvent: 只有 type
    if kind in (GUM_CALL, GUM_RET):
        size += _pad(size) + PTR                        # location
        size += PTR                                     # target
        size += 4                                       # gint depth
        size += _pad(size)
        return size
    if kind == GUM_EXEC:
        size += _pad(size) + PTR                        # location
        return size
    if kind in (GUM_BLOCK, GUM_COMPILE):
        size += _pad(size) + PTR                        # start
        size += PTR                                     # end
        return size
    raise ValueError("未知事件类型")


# ---------- Stalker 配置默认值（Frida JavaScript API 文档） ----------

DEFAULT_TRUST_THRESHOLD = 1        # 默认 1：执行过一次就假定可信
DEFAULT_QUEUE_CAPACITY = 16384     # 事件队列容量（事件条数）
DEFAULT_QUEUE_DRAIN_INTERVAL = 250  # 毫秒；250ms 即每秒排空 4 次


class ConfigError(Exception):
    pass


class StalkerConfig(object):
    """follow() 的 options 对象。"""

    def __init__(self, events=None, on_receive=False, on_call_summary=False,
                 trust_threshold=DEFAULT_TRUST_THRESHOLD,
                 queue_capacity=DEFAULT_QUEUE_CAPACITY,
                 queue_drain_interval=DEFAULT_QUEUE_DRAIN_INTERVAL):
        self.events = dict(events or {})
        self.on_receive = on_receive
        self.on_call_summary = on_call_summary
        self.trust_threshold = trust_threshold
        self.queue_capacity = queue_capacity
        self.queue_drain_interval = queue_drain_interval
        # 文档原话：Only specify one of the two following callbacks
        if on_receive and on_call_summary:
            raise ConfigError("onReceive 与 onCallSummary 只能指定其中一个")

    def mask(self):
        m = GUM_NOTHING
        for name, bit in (("call", GUM_CALL), ("ret", GUM_RET), ("exec", GUM_EXEC),
                          ("block", GUM_BLOCK), ("compile", GUM_COMPILE)):
            if self.events.get(name):
                m |= bit
        return m

    def wants(self, kind):
        return bool(self.mask() & kind)


def trusted(exec_count, threshold):
    """trustThreshold：-1 永不信任；0 从一开始信任；N 执行 N 次后信任。"""
    if threshold < 0:
        return False
    if threshold == 0:
        return True
    return exec_count >= threshold


# ---------- transform 迭代器 ----------

class Instruction(object):

    def __init__(self, address, mnemonic):
        self.address = address
        self.mnemonic = mnemonic


class StalkerIterator(object):
    """transform(iterator) 的模型：next() 取指令，keep() 保留，不调 keep() 即丢弃。

    文档原话：Note that not calling keep() will result in the instruction
    getting dropped, which makes it possible for your transform to fully
    replace certain instructions.
    """

    def __init__(self, instructions, memory_access="open"):
        self.instructions = list(instructions)
        self.memory_access = memory_access
        self.i = -1
        self.current = None
        self.kept = []
        self.callouts = []

    @property
    def memoryAccess(self):
        """对齐 JS API 的驼峰属性名（iterator.memoryAccess）。"""
        return self.memory_access

    def next(self):
        self.i += 1
        if self.i >= len(self.instructions):
            self.current = None
            return None
        self.current = self.instructions[self.i]
        return self.current

    def keep(self):
        if self.current is None:
            raise RuntimeError("没有当前指令")
        self.kept.append(self.current)

    def putCallout(self, fn):
        """插入同步回调；ARM/ARM64 上只有 memoryAccess 为 open 时才安全插桩。"""
        if self.memory_access != "open":
            raise RuntimeError("memoryAccess 不是 open，插桩可能破坏独占访存序列")
        self.callouts.append((self.current.address, fn))


def default_transform(iterator):
    """文档给出的默认实现。"""
    while iterator.next() is not None:
        iterator.keep()


def transform_on_ret(iterator, app_start, app_end, on_match):
    """文档示例：在每个 ret 前插一段比较 + 同步 callout。"""
    instruction = iterator.next()
    while instruction is not None:
        start = instruction.address
        is_app_code = app_start <= start < app_end
        can_emit = iterator.memoryAccess == "open"
        if is_app_code and can_emit and instruction.mnemonic == "ret":
            iterator.putCallout(on_match)
        iterator.keep()
        instruction = iterator.next()


# ---------- 事件队列 ----------

class EventQueue(object):
    """queueCapacity + queueDrainInterval；interval 为 0 时只靠 flush() 排空。"""

    def __init__(self, capacity=DEFAULT_QUEUE_CAPACITY,
                 drain_interval=DEFAULT_QUEUE_DRAIN_INTERVAL):
        self.capacity = capacity
        self.drain_interval = drain_interval
        self.buf = []
        self.dropped = 0
        self.ticks = 0

    def push(self, event):
        if len(self.buf) >= self.capacity:
            self.dropped += 1        # 队列满：丢弃（文档未承诺不丢，实测会丢）
            return False
        self.buf.append(event)
        return True

    def tick(self):
        """推进一个 drainInterval；返回本次排出的事件列表。"""
        self.ticks += 1
        if self.drain_interval == 0:
            return []                # 周期排空被禁用
        return self.flush()

    def flush(self):
        out, self.buf = self.buf, []
        return out


# ---------- 排除范围与 call probe ----------

class Range(object):

    def __init__(self, base, size):
        self.base = base
        self.size = size

    def contains(self, address):
        return self.base <= address < self.base + self.size


class Stalker(object):
    """把上面各块拼起来的最小 Stalker 模型。"""

    def __init__(self, config=None):
        self.config = config or StalkerConfig()
        self.excluded = []
        self.queue = EventQueue(self.config.queue_capacity,
                                self.config.queue_drain_interval)
        self.exec_counts = {}
        self.probes = {}
        self._next_probe_id = 1
        self.following = False
        self.call_summary = {}

    def exclude(self, rng):
        """文档：遇到对该范围内指令的调用时不再跟进去 —— 看得见入参与返回值，
        看不见中间的指令。"""
        self.excluded.append(rng)

    def is_excluded(self, address):
        return any(r.contains(address) for r in self.excluded)

    def addCallProbe(self, address, callback, data=None):
        pid = self._next_probe_id
        self._next_probe_id += 1
        self.probes[pid] = (address, callback, data)
        return pid

    def removeCallProbe(self, pid):
        return self.probes.pop(pid, None)

    def invalidation_needed(self):
        """invalidate() 只作废指定基本块；unfollow 会作废全部已翻译代码。"""
        return sorted(self.exec_counts)

    # --- 事件发射 ---
    def _emit(self, kind, payload):
        if not self.config.wants(kind):
            return False
        payload = dict(payload)
        payload["type"] = kind
        return self.queue.push(payload)

    def on_block(self, start, end):
        self.exec_counts[start] = self.exec_counts.get(start, 0) + 1
        self._emit(GUM_COMPILE, {"start": start, "end": end})
        self._emit(GUM_BLOCK, {"start": start, "end": end})
        return self.config.wants(GUM_BLOCK)

    def on_call(self, location, target, depth=0):
        self._emit(GUM_CALL, {"location": location, "target": target, "depth": depth})
        self.call_summary[target] = self.call_summary.get(target, 0) + 1
        return self.is_excluded(target)

    def on_ret(self, location, target, depth=0):
        self._emit(GUM_RET, {"location": location, "target": target, "depth": depth})

    def on_exec(self, location):
        if self.is_excluded(location):
            return False
        self._emit(GUM_EXEC, {"location": location})
        return True

    def follow(self):
        self.following = True

    def unfollow(self):
        self.following = False

    def garbage_collect(self):
        """文档：unfollow 之后要在安全点释放累积内存，
        否则刚 unfollow 的线程还在执行它的最后几条指令，会踩到竞态。"""
        self.queue.flush()
        return not self.following
