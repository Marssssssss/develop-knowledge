"""Stalker 模型自检：逐条对照 gumevent.h 与 Frida JavaScript API 文档。

运行：python selfcheck_stalker.py
"""

from stalker_trace import (
    GUM_NOTHING, GUM_CALL, GUM_RET, GUM_EXEC, GUM_BLOCK, GUM_COMPILE, EVENT_NAMES,
    event_struct_size, PTR, TYPE_W,
    DEFAULT_TRUST_THRESHOLD, DEFAULT_QUEUE_CAPACITY, DEFAULT_QUEUE_DRAIN_INTERVAL,
    ConfigError, StalkerConfig, trusted,
    Instruction, StalkerIterator, default_transform, transform_on_ret,
    EventQueue, Range, Stalker,
)

PASS = [0]


def eq(a, b, label):
    assert a == b, "FAILED: %s (期望 %r 实际 %r)" % (label, b, a)
    PASS[0] += 1


def ok(cond, label):
    assert cond, "FAILED: " + label
    PASS[0] += 1


# ---------- 1. GumEventType 枚举（gumevent.h） ----------

eq(GUM_NOTHING, 0, "GUM_NOTHING = 0")
eq(GUM_CALL, 1, "GUM_CALL = 1 << 0")
eq(GUM_RET, 2, "GUM_RET = 1 << 1")
eq(GUM_EXEC, 4, "GUM_EXEC = 1 << 2")
eq(GUM_BLOCK, 8, "GUM_BLOCK = 1 << 3")
eq(GUM_COMPILE, 16, "GUM_COMPILE = 1 << 4")
# 位掩码可叠加：events 配置就是按位或出来的
eq(GUM_CALL | GUM_RET | GUM_EXEC | GUM_BLOCK | GUM_COMPILE, 31, "五种事件全开是 31")
eq(sorted(EVENT_NAMES.values()), ["block", "call", "compile", "exec", "ret"], "五个事件名")

# ---------- 2. 六个结构体的大小（按 gumevent.h 字段 + 8 字节对齐推算） ----------

eq(event_struct_size(GUM_NOTHING), 4, "GumAnyEvent 只有 type（4 字节）")
eq(event_struct_size(GUM_CALL), 32, "GumCallEvent: type+pad+location+target+depth+pad = 32")
eq(event_struct_size(GUM_RET), 32, "GumRetEvent 与 CallEvent 同构，同为 32")
eq(event_struct_size(GUM_EXEC), 16, "GumExecEvent: type+pad+location = 16")
eq(event_struct_size(GUM_BLOCK), 24, "GumBlockEvent: type+pad+start+end = 24")
eq(event_struct_size(GUM_COMPILE), 24, "GumCompileEvent 与 BlockEvent 同构，同为 24")
# 联合体的大小 = 最大成员
eq(max(event_struct_size(k) for k in (GUM_CALL, GUM_RET, GUM_EXEC, GUM_BLOCK, GUM_COMPILE)),
   32, "union GumEvent 取最大成员 32 字节")
eq(PTR, 8, "64 位进程上 gpointer 是 8 字节")
eq(TYPE_W, 4, "GumEventType 是 guint（4 字节）")

# ---------- 3. 配置默认值与互斥约束 ----------

eq(DEFAULT_TRUST_THRESHOLD, 1, "trustThreshold 默认 1")
eq(DEFAULT_QUEUE_CAPACITY, 16384, "queueCapacity 默认 16384 条")
eq(DEFAULT_QUEUE_DRAIN_INTERVAL, 250, "queueDrainInterval 默认 250ms（每秒排空 4 次）")
try:
    StalkerConfig(on_receive=True, on_call_summary=True)
    raise AssertionError("本应抛错")
except ConfigError:
    PASS[0] += 1        # 文档：Only specify one of the two following callbacks

cfg_call = StalkerConfig(events={"call": True}, on_receive=True)
eq(cfg_call.mask(), GUM_CALL, "只开 call 时掩码是 GUM_CALL")
cfg_all = StalkerConfig(events={"call": True, "ret": True, "exec": True,
                                "block": True, "compile": True})
eq(cfg_all.mask(), 31, "五种全开时掩码是 31")
ok(cfg_call.wants(GUM_CALL) and not cfg_call.wants(GUM_EXEC), "wants() 按掩码判定")
cfg_none = StalkerConfig(events={})
eq(cfg_none.mask(), GUM_NOTHING, "全关时掩码是 GUM_NOTHING")

# ---------- 4. trustThreshold 语义 ----------

ok(trusted(0, 0), "threshold=0：从一开始信任")
ok(trusted(100, 0), "threshold=0：任何执行次数都已信任")
ok(not trusted(0, 1), "threshold=1：还没执行过，不信任")
ok(trusted(1, 1), "threshold=1：执行过一次即信任")
ok(trusted(3, 2), "threshold=2：执行两次后信任")
ok(not trusted(1, 2), "threshold=2：只执行一次还不信任")
for n in range(10):
    ok(not trusted(n, -1), "threshold=-1：永不信任（慢但安全）")

# ---------- 5. transform 迭代器 ----------

insns = [Instruction(0x1000, "mov"), Instruction(0x1004, "cmp"),
         Instruction(0x1008, "ret"), Instruction(0x100c, "nop")]
it = StalkerIterator(insns)
default_transform(it)
eq([i.address for i in it.kept], [0x1000, 0x1004, 0x1008, 0x100c], "默认 transform 保留全部指令")
eq(it.callouts, [], "默认 transform 不插 callout")

# 只 keep 一半：文档说没调 keep() 的指令会被丢弃
it2 = StalkerIterator(insns)
while it2.next() is not None:
    if it2.current.mnemonic != "nop":
        it2.keep()
eq([i.address for i in it2.kept], [0x1000, 0x1004, 0x1008], "nop 未 keep 被丢弃")

# 文档示例：在 app 地址范围内的 ret 前插 callout
def on_match(ctx):
    return ctx

it3 = StalkerIterator(insns)
transform_on_ret(it3, 0x1000, 0x1010, on_match)
eq([a for a, _fn in it3.callouts], [0x1008], "只对 app 范围内的 ret 插 callout")
eq(len(it3.kept), 4, "示例 transform 仍然保留全部指令")

# 范围外的 ret 不插桩
it4 = StalkerIterator(insns)
transform_on_ret(it4, 0x2000, 0x2010, on_match)
eq(it4.callouts, [], "范围外的 ret 不插 callout")

# memoryAccess 不是 open 时不允许插桩（ARM/ARM64 独占访存的坑）
it5 = StalkerIterator(insns, memory_access="exclusive")
try:
    it5.next()
    it5.putCallout(on_match)
    raise AssertionError("本应抛错")
except RuntimeError:
    PASS[0] += 1

# ---------- 6. 事件队列 ----------

q = EventQueue(capacity=4, drain_interval=250)
for i in range(4):
    ok(q.push(i), "队列未满时 push 成功")
ok(not q.push(4), "队列满后 push 失败")
eq(q.dropped, 1, "队列满时丢弃一条")
eq(len(q.tick()), 4, "一个 drainInterval 后排出 4 条")
eq(len(q.tick()), 0, "排空后再 tick 没有事件")

q0 = EventQueue(capacity=8, drain_interval=0)     # 0 = 关闭周期排空
q0.push(1)
q0.push(2)
eq(len(q0.tick()), 0, "drainInterval 为 0 时周期排空被禁用")
eq(len(q0.flush()), 2, "interval 为 0 时必须靠 flush() 手动排空")

# ---------- 7. exclude：看得见入参与返回值，看不见中间指令 ----------

st = Stalker(StalkerConfig(events={"call": True, "ret": True, "exec": True, "block": True}))
st.exclude(Range(0x9000, 0x1000))
ok(st.is_excluded(0x9500), "0x9500 落在排除范围内")
ok(not st.is_excluded(0x8000), "0x8000 不在排除范围内")
ok(st.on_call(0x4000, 0x9500), "进入被排除范围的调用仍会产生 CALL 事件")
ok(not st.on_exec(0x9500), "排除范围内的 exec 被抑制")
ok(st.on_exec(0x4004), "范围外的 exec 正常发射")
eq(len([e for e in st.queue.buf if e["type"] == GUM_EXEC]), 1, "只有范围外那条 exec 入队")
eq(len([e for e in st.queue.buf if e["type"] == GUM_CALL]), 1, "CALL 事件仍然入队")

# ---------- 8. call probe 与 summary ----------

st2 = Stalker(StalkerConfig(events={"call": True}, on_call_summary=True))
pid = st2.addCallProbe(0x7000, lambda site: None, data=1337)
eq(pid, 1, "addCallProbe 返回自增 id")
addr, _cb, data = st2.probes[pid]
eq(addr, 0x7000, "probe 记录了地址")
eq(data, 1337, "第三个参数作为 user_data 传入")
ok(st2.removeCallProbe(pid) is not None, "removeCallProbe 摘掉该 probe")
eq(st2.probes, {}, "摘掉后 probe 表为空")
st2.on_call(0x4000, 0x7000)
st2.on_call(0x4004, 0x7000)
st2.on_call(0x4008, 0x7100)
eq(st2.call_summary, {0x7000: 2, 0x7100: 1}, "onCallSummary 只统计目标与次数，不保序")

# ---------- 9. follow / unfollow / garbageCollect ----------

st3 = Stalker(StalkerConfig(events={"block": True}))
st3.follow()
st3.on_block(0x5000, 0x5020)
st3.on_block(0x5000, 0x5020)
eq(st3.exec_counts[0x5000], 2, "同一基本块被执行两次")
eq(len([e for e in st3.queue.buf if e["type"] == GUM_BLOCK]), 2, "两次 block 事件都入队")
# 文档：garbageCollect 要在 unfollow 之后的安全点调用，否则刚 unfollow 的线程
# 还在执行它的最后几条指令，会踩到竞态 —— 故跟踪中调用视为不安全
ok(not st3.garbage_collect(), "仍在 follow 时 garbageCollect 不安全")
st3.unfollow()
ok(st3.garbage_collect(), "unfollow 之后 garbageCollect 才安全")
ok(not st3.following, "unfollow 后不再跟踪")
# invalidate 只作废指定块，unfollow 会作废全部：exec_counts 的键就是已翻译块
eq(st3.invalidation_needed(), [0x5000], "已翻译的基本块列表")

print("PASS %d 项断言全部通过" % PASS[0])
