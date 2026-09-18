# ---------------------------------------------------------------- 自检

from closures_iter import (
    Closure, RustIter, bind_error, MUTABLE, OWNED, SHARED,
)


def check(label, cond, detail=""):
    if not cond:
        raise AssertionError(f"{label} FAILED {detail}")


def run():
    # ---- 1. 捕获方式推导（Listing 13-4 / 13-5 / 13-6）
    only_borrows = Closure("only_borrows", [("read", "list")])
    check("1.1 只读→共享借用", only_borrows.capture_mode("list") == SHARED)
    check("1.2 只读闭包是 Fn", only_borrows.traits() == {"FnOnce", "FnMut", "Fn"},
          str(only_borrows.traits()))

    borrows_mutably = Closure("borrows_mutably", [("mutate", "list")])
    check("1.3 改动→可变借用", borrows_mutably.capture_mode("list") == MUTABLE)
    check("1.4 改动闭包是 FnMut", borrows_mutably.traits() == {"FnOnce", "FnMut"},
          str(borrows_mutably.traits()))
    check("1.5 FnMut 闭包不满足 Fn 约束", not borrows_mutably.satisfies("Fn"))

    spawn = Closure("thread::spawn", [("read", "list")], forced_move=True)
    check("1.6 move 强制取所有权", spawn.capture_mode("list") == OWNED)
    check("1.7 move 只改捕获方式、不改 trait 集",
          spawn.traits() == {"FnOnce", "FnMut", "Fn"}, str(spawn.traits()))

    # ---- 2. trait bound 与报错（Listing 13-8 / 13-9）
    moves_out = Closure("count_by_push", [("consume", "value"), ("read", "r")])
    check("2.1 移出捕获值→只有 FnOnce", moves_out.traits() == {"FnOnce"},
          str(moves_out.traits()))
    err = bind_error(moves_out, "FnMut", "value")
    check("2.2 sort_by_key 报 E0507", err is not None and err[0] == "E0507", str(err))

    counter = Closure("count_by_inc", [("mutate", "n"), ("read", "r")])
    check("2.3 计数器版本满足 FnMut", counter.satisfies("FnMut"))
    check("2.4 计数器版本是 FnMut 而非 Fn", counter.traits() == {"FnOnce", "FnMut"})
    check("2.5 计数器版本不报错", bind_error(counter, "FnMut", "n") is None)

    # unwrap_or_else 的约束是 FnOnce —— 官方原文：因此三种闭包都能接
    for c in (only_borrows, borrows_mutably, moves_out):
        check(f"2.6 unwrap_or_else 接受 {c.name}", c.satisfies("FnOnce"))

    # ---- 3. 惰性：不消费就一次都不跑（Listing 13-14 的 unused `Map` 警告）
    v = RustIter([1, 2, 3], label="v.iter()")
    m = v.map(lambda x: x + 1)
    check("3.1 建完 map 闭包零调用", m.calls == 0, str(m.calls))
    check("3.2 建完 map 零上游访问", v.visits == 0, str(v.visits))
    out = m.collect()
    check("3.3 collect 后才跑", out == [2, 3, 4], str(out))
    check("3.4 每个元素恰好一次", m.calls == 3, str(m.calls))

    # ---- 4. 消费适配器拿走所有权（ch13-02："We aren't allowed to use v1_iter
    #         after the call to sum"）
    v2 = RustIter([1, 2, 3], label="v1_iter")
    check("4.1 sum 结果", v2.sum() == 6)
    try:
        v2.next()
        raise AssertionError("4.2 消费后仍可 next（应当报错）")
    except RuntimeError as e:
        check("4.2 消费后 next 报 E0382", "E0382" in str(e), str(e))

    # ---- 5. take 的短路：上游只前进到必要位置
    src = RustIter(list(range(1, 101)), label="big")
    t = src.take(3)
    check("5.1 take(3).collect()", t.collect() == [1, 2, 3])
    check("5.2 只产出 3 个", t.yielded == 3, str(t.yielded))
    check("5.3 上游只前进 3 步（未被全量拉取）", src.pos == 3, str(src.pos))
    check("5.4 上游 visits = 3 + 0", src.visits == 3, str(src.visits))

    # ---- 6. 零成本的结构证据：链式 = 单趟 + 1 次分配；分步 = 2 次分配
    data = list(range(1, 21))

    chained_src = RustIter(data, label="chained")
    chain = chained_src.filter(lambda x: x % 2 == 0).map(lambda x: x * 10)
    chain_res = chain.collect()
    check("6.1 链式结果", chain_res == [20, 40, 60, 80, 100, 120, 140, 160, 180, 200],
          str(chain_res))
    check("6.2 链式只物化 1 个容器（终态 collect）", chain.allocs == 1, str(chain.allocs))
    check("6.3 链式上游只走一趟", chained_src.visits == 21, str(chained_src.visits))

    step1 = RustIter(data, label="step1")
    f1 = step1.filter(lambda x: x % 2 == 0)
    mid = f1.collect()                       # 中间 Vec 在此物化
    step2 = RustIter(mid, label="step2")
    m2 = step2.map(lambda x: x * 10)
    step_res = m2.collect()                  # 又一次物化
    check("6.4 分步结果相同", step_res == chain_res)
    check("6.5 中间 Vec 确实存在", mid == [2, 4, 6, 8, 10, 12, 14, 16, 18, 20], str(mid))
    check("6.6 分步共物化 2 个容器", f1.allocs + m2.allocs == 2,
          f"{f1.allocs}+{m2.allocs}")

    # ---- 7. for 循环去糖后的访问次数
    loop_src = RustIter([1, 2, 3], label="for")
    seen = []
    loop_src.for_loop(seen.append)
    check("7.1 for 去糖拿到全部元素", seen == [1, 2, 3], str(seen))
    check("7.2 for 的 next 次数 = 3 元素 + 1 次 None 探测",
          loop_src.visits == 4, str(loop_src.visits))

    # ---- 8. 官方基准数字（ch13-04）：迭代器版不慢于手写循环版
    ns_for, ns_iter = 19_620_300, 19_234_900
    check("8.1 迭代器版 ≤ 循环版", ns_iter <= ns_for)
    check("8.2 差距落在噪声内(<3%)", (ns_for - ns_iter) / ns_for < 0.03,
          f"{(ns_for - ns_iter) / ns_for:.4f}")

    print("closures_iter: all assertions passed")


if __name__ == "__main__":
    run()
