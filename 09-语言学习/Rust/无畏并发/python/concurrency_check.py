"""无畏并发自检：Send/Sync 推导 + 共享计数器 + 死锁可检测性。"""

from send_sync import (
    Deadlock, Mutex, Poisoned, has_cycle, panic_while_holding,
    run_two_threads, spawn_error, struct_traits, traits,
)


def check(label, cond, detail=""):
    if not cond:
        raise AssertionError(f"{label} FAILED {detail}")


def run():
    # ---- 1. 基本类型与裸指针
    check("1.1 i32 是 Send+Sync", traits("i32") == (True, True))
    check("1.2 String 是 Send+Sync", traits("String") == (True, True))
    check("1.3 裸指针既非 Send 也非 Sync（唯一的原始类型例外）",
          traits("*const u8") == (False, False))

    # ---- 2. Rc vs Arc（ch16-03/16-04 的核心对照）
    check("2.1 Rc<i32> 既非 Send 也非 Sync", traits("Rc<i32>") == (False, False))
    check("2.2 Arc<i32> 是 Send+Sync", traits("Arc<i32>") == (True, True))
    check("2.3 Arc 的 Send/Sync 依赖内部类型",
          traits("Arc<Rc<i32>>") == (False, False), str(traits("Arc<Rc<i32>>")))
    err = spawn_error("Rc<Mutex<i32>>")
    check("2.4 thread::spawn 传 Rc<Mutex<i32>> 报 E0277",
          err is not None and "E0277" in err and "Send" in err, str(err))
    check("2.5 换成 Arc 就能过", spawn_error("Arc<Mutex<i32>>") is None)

    # ---- 3. 内部可变性的非对称性
    check("3.1 Cell<i32> 是 Send", traits("Cell<i32>")[0] is True)
    check("3.2 Cell<i32> 不是 Sync", traits("Cell<i32>")[1] is False)
    check("3.3 RefCell<i32> 是 Send 不是 Sync", traits("RefCell<i32>") == (True, False))
    check("3.4 Arc<RefCell<i32>> 连 Send 都不是（这是必须换 Mutex 的原因）",
          traits("Arc<RefCell<i32>>") == (False, False),
          str(traits("Arc<RefCell<i32>>")))

    # ---- 4. Mutex 与 MutexGuard 的对照
    check("4.1 Mutex<i32> 是 Send+Sync", traits("Mutex<i32>") == (True, True))
    check("4.2 Mutex 的两条 impl 都只要 T: Send —— 所以包不住不 Send 的 Rc",
          traits("Mutex<Rc<i32>>") == (False, False),
          str(traits("Mutex<Rc<i32>>")))          # Rc 不 Send → Mutex<Rc> 既不 Send 也不 Sync
    check("4.3 MutexGuard 不能离开加锁线程（!Send）", traits("MutexGuard<i32>")[0] is False)
    check("4.4 但 &MutexGuard<i32> 可以（T: Sync ⇒ Sync）",
          traits("MutexGuard<i32>")[1] is True)

    # ---- 5. 引用的四条关系（std::marker::Sync 原文）
    check("5.1 &T 是 Send iff T 是 Sync", traits("&RefCell<i32>") == (False, False),
          str(traits("&RefCell<i32>")))
    check("5.2 &mut T 是 Send iff T 是 Send", traits("&mut RefCell<i32>") == (True, False),
          str(traits("&mut RefCell<i32>")))
    check("5.3 &mut T 竟然是 Sync（官方称为 somewhat surprising consequence）",
          traits("&mut i32") == (True, True))
    check("5.4 & &mut T 退化成只读，所以没有数据竞争",
          traits("& &mut i32") == (True, True), str(traits("& &mut i32")))

    # ---- 6. 结构体组合规则
    check("6.1 全 Send/Sync 字段 → 整体 Send/Sync",
          struct_traits(["i32", "String", "Vec<i32>"]) == (True, True))
    check("6.2 掺一个 Rc 就整体失效（自动 trait 是「全员通过」）",
          struct_traits(["i32", "Rc<String>"]) == (False, False))
    check("6.3 掺一个 RefCell → 仍 Send 但不再 Sync",
          struct_traits(["i32", "RefCell<i32>"]) == (True, False))

    # ---- 7. 共享计数器：上锁才会对（Listing 16-15）
    m = Mutex("counter", 0)
    for _ in range(10):
        g = m.lock("worker", {})
        check("7.1 每次都能拿到锁", g is not None)
        g.set(g.get() + 1)
        g.release()
    check("7.2 Arc<Mutex<i32>> 计数 = 10", m.value == 10, str(m.value))

    # 不上锁：显式交错下的丢失更新（Rust 里这段代码根本编译不过）
    shared = {"v": 0}
    t1_read = shared["v"]                 # T1 读到 0
    t2_read = shared["v"]                 # T2 也读到 0
    shared["v"] = t1_read + 1             # T1 写 1
    shared["v"] = t2_read + 1             # T2 写 1 —— 覆盖，丢了一次
    check("7.3 无锁 read-modify-write 丢失更新", shared["v"] == 1, str(shared["v"]))

    # ---- 8. 中毒（poisoning）
    pm = Mutex("poisoned", 0)
    g = pm.lock("T1", {})
    panic_while_holding(g)
    try:
        pm.lock("T2", {})
        raise AssertionError("8.1 中毒后仍能加锁（应当抛 Poisoned）")
    except Poisoned:
        pass
    check("8.2 中毒标记不会自己消失", pm.poisoned is True)

    # ---- 9. 死锁：类型系统拦不住
    dead, graph = run_two_threads(["A", "B"], ["B", "A"])
    check("9.1 相反加锁顺序 → 死锁", dead, str(graph))
    dead2, graph2 = run_two_threads(["A", "B"], ["A", "B"])
    check("9.2 相同加锁顺序 → 不死锁", not dead2, str(graph2))
    check("9.3 两次加锁动作都合法、都能通过 borrow check",
          all(traits("Arc<Mutex<i32>>") == (True, True) for _ in range(2)))
    check("9.4 wait-for 图判环本身是自洽的",
          has_cycle({"T1": "T2", "T2": "T1"}) and not has_cycle({"T1": "T2"}))

    # ---- 10. 不可重入
    rm = Mutex("reentrant", 0)
    g1 = rm.lock("T1", {})
    try:
        rm.lock("T1", {})
        raise AssertionError("10.1 同线程重入应当死锁")
    except Deadlock:
        pass

    print("send_sync: all assertions passed")


if __name__ == "__main__":
    run()
