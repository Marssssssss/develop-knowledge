from notification_center import *

# NotificationCenter 自检:python selfcheck_notification_center.py
# 模型语义见 notification_center.py

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

    # --- 1. 没有观察者时投递是空转
    c = NotificationCenter()
    ok("无人注册时 post 不产生任何投递", c.post("N") == [])

    # --- 2. name / object 的筛选规则
    got = []
    c = NotificationCenter()
    c.add_observer(name="A", using=lambda note: got.append(("A", note.name)))
    c.add_observer(name="B", using=lambda note: got.append(("B", note.name)))
    c.post("A")
    ok("只投递给名字匹配的观察者", got == [("A", "A")], got)

    got.clear()
    c = NotificationCenter()
    c.add_observer(using=lambda note: got.append(("any", note.name)))   # name = nil
    c.post("X"); c.post("Y")
    ok("name 为 nil 时收下所有名字", got == [("any", "X"), ("any", "Y")], got)

    sender1, sender2 = object(), object()
    got.clear()
    c = NotificationCenter()
    c.add_observer(name="N", obj=sender1, using=lambda note: got.append("s1"))
    c.add_observer(name="N", using=lambda note: got.append("any"))
    c.post("N", obj=sender1)
    ok("object 匹配 + object 为 nil 的都命中(顺序 = 注册顺序)",
       got == ["s1", "any"], got)
    got.clear()
    c.post("N", obj=sender2)
    ok("object 不匹配的不投递", got == ["any"], got)

    # --- 3. queue = nil → 在投递线程同步执行
    log = []
    c = NotificationCenter()
    c.add_observer(name="N", queue=None,
                   using=lambda note: log.append(("block", note.name)))
    delivered = c.post("N", thread="bg")
    log.append(("post-returned",))
    ok("queue 为 nil:block 在 post 之内就跑完",
       log == [("block", "N"), ("post-returned",)], log)
    ok("投递记录里带的是投递线程", delivered[0][1] == "bg", delivered)

    # --- 4. queue 非 nil → 排进队列,post 返回时不执行
    q = OperationQueue("serial")
    log.clear()
    c = NotificationCenter()
    tok = c.add_observer(name="N", queue=q, using=lambda note: log.append("block"))
    c.post("N")
    ok("有 queue 时 post 返回后 block 还没跑", log == [], log)
    ok("但已经排进了那个 OperationQueue", c.pending(q) == 1)
    c.drain(q)
    ok("队列执行后才真正跑", log == ["block"], log)
    ok("排队的 block 跑完就出队", c.pending(q) == 0)

    # --- 5. 中心强持有 observer 与 block 的拷贝
    c = NotificationCenter()
    original = lambda note: None
    tok = c.add_observer(name="N", using=original)
    stored = c._observers[0].block
    ok("注册后观察者数量为 1", c.observer_count() == 1)
    ok("中心持有的是 block 的**拷贝**,不是原闭包", stored is not original)
    ok("拿到的 token 是不透明对象", isinstance(tok, Token))

    # 同一个 name+object 注册两次 → 两个独立 token,两次投递
    got.clear()
    c = NotificationCenter()
    t1 = c.add_observer(name="N", using=lambda note: got.append(1))
    t2 = c.add_observer(name="N", using=lambda note: got.append(2))
    c.post("N")
    ok("同名注册两次 → 两个 token、两次投递", got == [1, 2] and t1 is not t2, got)

    # --- 6. removeObserver
    got.clear()
    c = NotificationCenter()
    t1 = c.add_observer(name="N", using=lambda note: got.append(1))
    t2 = c.add_observer(name="N", using=lambda note: got.append(2))
    removed = c.remove_observer(t1)
    c.post("N")
    ok("removeObserver 精确移除一个", removed == 1 and got == [2], got)
    ok("移除后只剩一个观察者", c.observer_count() == 1)

    # 一次性通知:在 block 里移除自己
    got.clear()
    c = NotificationCenter()
    box = {}

    def once(note):
        got.append(note.name)
        c.remove_observer(box["token"])

    box["token"] = c.add_observer(name="N", using=once)
    c.post("N")
    c.post("N")
    ok("在 block 里移除自己 → 只生效一次", got == ["N"], got)
    ok("第二次 post 时已经没有观察者", c.observer_count() == 0)

    # 投递期间移除别的观察者:本轮仍按投递开始时的快照走完
    got.clear()
    c = NotificationCenter()
    late = {}

    def killer(note):
        got.append("killer")
        c.remove_observer(late["token"])

    c.add_observer(name="N", using=killer)
    late["token"] = c.add_observer(name="N", using=lambda note: got.append("victim"))
    c.post("N")
    ok("投递期间被移除的观察者,本轮仍会收到", got == ["killer", "victim"], got)
    ok("但下一轮就没有它了", c.observer_count() == 1)

    # --- 7. 多个 block 可以并发(模型里体现为「不保证先后」的独立执行)
    import threading
    order = []
    lock = threading.Lock()
    c = NotificationCenter()
    q1, q2 = OperationQueue("q1"), OperationQueue("q2")
    c.add_observer(name="N", queue=q1, using=lambda note: order.append("q1"))
    c.add_observer(name="N", queue=q2, using=lambda note: order.append("q2"))
    c.post("N")
    ok("命中多个观察者时各自排队,互不等候",
       c.pending(q1) == 1 and c.pending(q2) == 1)
    c.drain(q2); c.drain(q1)
    ok("两个队列谁先执行谁先落地", order == ["q2", "q1"], order)

    # --- 8. 每个进程有 default 中心,也可以自建;不同中心互不相通
    dflt = NotificationCenter("default")
    other = NotificationCenter("scoped")
    got.clear()
    dflt.add_observer(name="N", using=lambda note: got.append("default"))
    other.add_observer(name="N", using=lambda note: got.append("scoped"))
    dflt.post("N")
    ok("自建中心收不到 default 中心的通知", got == ["default"], got)

    # --- 9. weak self vs strong self:用真实的 GC 结果说话
    gc.collect()
    c = NotificationCenter()
    owner_strong = Owner(c, name="N", weak_self=False)
    ref_strong = weakref.ref(owner_strong)
    del owner_strong
    gc.collect()
    ok("block 里强持有 self → 中心让它无法释放(泄漏)", ref_strong() is not None)
    ok("泄漏的那个还在:post 仍然会命中它", len(c.post("N")) == 1)

    c2 = NotificationCenter()
    owner_weak = Owner(c2, name="N", weak_self=True)
    c2.post("N")
    ok("weak self:owner 在世时照常收到", owner_weak.hits == ["N"], owner_weak.hits)
    ref_weak = weakref.ref(owner_weak)
    del owner_weak
    gc.collect()
    ok("block 里用 weak self → 外部引用一断就释放", ref_weak() is None)
    ok("释放后 block 仍会被调用(只是拿不到 self),条目不会自己消失",
       len(c2.post("N")) == 1 and c2.observer_count() == 1)

    print(f"\n全部 {n} 条断言通过")
    return n


if __name__ == "__main__":
    run_selfcheck()
