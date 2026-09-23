"""ae.c 时间事件部分自检：重排、自毁、refcount、maxId、超时计算、aeMain。"""

from ae import (
    AE_DELETED_EVENT_ID,
    AE_ERR,
    AE_NOMORE,
    AE_OK,
    AE_READABLE,
    Clock,
    EventLoop,
    FiredEvent,
    ae_create_file_event,
    ae_main,
)
from ae_time import (
    ae_create_time_event,
    ae_delete_time_event,
    process_time_events,
    us_until_earliest_timer,
)

PASS = 0
FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL {name}: got {got!r} want {want!r}")


def make_loop(setsize=64, now=0):
    clock = Clock(now)
    return EventLoop(setsize, clock, lambda el, tvp: []), clock


# ---------------------------------------------------------------- 重排基准
el, clock = make_loop(now=1_000_000)
fired = []


def tick(el_, tid, data):
    fired.append(tid)
    clock.advance(50_000)   # 回调内部耗时 50ms
    return 100


te_id = ae_create_time_event(el, 0, tick, None, None)
check("定时器 id 从 0 开始分配", te_id, 0)
check("到期定时器被处理", process_time_events(el), 1)
check("下一次到期 = 回调返回后的 now + 100ms", el.time_event_head.when, 1_150_000)

# 成对用例：回调内不推进时钟时，基准是同一个 now
el2, clock2 = make_loop(now=1_000_000)
ae_create_time_event(el2, 0, lambda *a: 100, None, None)
process_time_events(el2)
check("回调内不推进时钟 → when = 原 now + 100ms", el2.time_event_head.when, 1_100_000)
check("  与推进 50ms 的用例相差正好 50ms", el.time_event_head.when - el2.time_event_head.when, 50_000)

# ---------------------------------------------------------------- AE_NOMORE 自毁
el, clock = make_loop()
finalized = []
ae_create_time_event(el, 0, lambda *a: AE_NOMORE, lambda lp, d: finalized.append(1), None)
n1 = process_time_events(el)
check("AE_NOMORE 当轮仍计入 processed", n1, 1)
check("AE_NOMORE 当轮只打标记，不释放", el.time_event_head.id, AE_DELETED_EVENT_ID)
check("  当轮未调用 finalizer", finalized, [])
n2 = process_time_events(el)
check("下一轮才真正释放", el.time_event_head, None)
check("  释放时调用 finalizer 一次", finalized, [1])
check("  释放轮不计入 processed", n2, 0)

# ---------------------------------------------------------------- refcount 保护
el, clock = make_loop()
finalized = []
order = []


def recursive(el_, tid, data):
    order.append(("fire", tid))
    ae_delete_time_event(el_, tid)
    process_time_events(el_)   # 模拟 timeProc 内递归进入
    order.append(("back", el_.time_event_head is not None))
    return AE_NOMORE


ae_create_time_event(el, 0, recursive, lambda lp, d: finalized.append(1), None)
process_time_events(el)
check("递归期间 refcount>0，事件不被释放", el.time_event_head is not None, True)
check("  递归返回后事件仍在链表上", order[-1], ("back", True))
check("  递归期间未调用 finalizer", finalized, [])
process_time_events(el)
check("refcount 归零后的下一轮才释放并调用 finalizer", finalized, [1])

# ---------------------------------------------------------------- maxId 防重入（白盒构造）
el, clock = make_loop()
hit = []


def p_a(el_, tid, data):
    hit.append("A")
    return AE_NOMORE


def p_b(el_, tid, data):
    hit.append("B")
    return AE_NOMORE


a_id = ae_create_time_event(el, 0, p_a, None, None)
b_id = ae_create_time_event(el, 0, p_b, None, None)
check("新定时器插在链表头", el.time_event_head.id, b_id)
el.time_event_next_id = 1   # 人为回退，使 maxId = 0，B(id=1) 落到本轮范围外
process_time_events(el)
check("id > maxId 的事件被跳过", hit, ["A"])
check("  B 未被执行", "B" in hit, False)

# ---------------------------------------------------------------- aeDeleteTimeEvent
el, clock = make_loop()
tid = ae_create_time_event(el, 0, lambda *a: AE_NOMORE, None, None)
check("删除存在的 id → AE_OK", ae_delete_time_event(el, tid), AE_OK)
check("  删除后 id = AE_DELETED_EVENT_ID", el.time_event_head.id, AE_DELETED_EVENT_ID)
check("再次按 -1 删除会命中已删除节点（返回 OK）", ae_delete_time_event(el, AE_DELETED_EVENT_ID), AE_OK)
check("删除不存在的 id → AE_ERR", ae_delete_time_event(el, 9999), AE_ERR)

# ---------------------------------------------------------------- usUntilEarliestTimer
el, clock = make_loop()
check("空链表 → -1", us_until_earliest_timer(el), -1)
ae_create_time_event(el, 50, lambda *a: AE_NOMORE, None, None)
check("有定时器 → 剩余微秒", us_until_earliest_timer(el), 50_000)
ae_delete_time_event(el, 0)
check("全部标记删除 → -1（不会解引用空指针）", us_until_earliest_timer(el), -1)

el, clock = make_loop()
ae_create_time_event(el, 10, lambda *a: AE_NOMORE, None, None)
ae_create_time_event(el, 90, lambda *a: AE_NOMORE, None, None)
check("取最早的那个（链表头是最新的，别按链表顺序取）", us_until_earliest_timer(el), 10_000)
clock.advance(10_000)
check("刚好到期 → 0", us_until_earliest_timer(el), 0)
clock.advance(5_000)
check("已过期 → 仍返回 0，不是负数", us_until_earliest_timer(el), 0)

# ---------------------------------------------------------------- aeMain
el, clock = make_loop()
log = []


def stopper(loop):
    log.append("bs")
    if len([x for x in log if x == "bs"]) >= 3:
        loop.stop = 1


el.beforesleep = stopper
el.aftersleep = lambda loop: log.append("as")
rounds = ae_main(el)
check("aeMain 跑到 stop=1 为止", rounds, 3)
check("aeMain 每轮都带 BEFORE_SLEEP 回调", log.count("bs"), 3)
check("aeMain 每轮都带 AFTER_SLEEP 回调", log.count("as"), 3)

print(f"\nae.c 时间事件自检：{PASS} 条通过，{FAIL} 条失败")
if FAIL:
    raise SystemExit(1)
