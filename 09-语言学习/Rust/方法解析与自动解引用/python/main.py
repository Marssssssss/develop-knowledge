"""方法解析与自动解引用 —— 演示入口。

与 `selfcheck_resolve.py` 共用 `resolve` 模型；这里只挑 5 个最反直觉的场景打印出来：
候选列表的顺序、trait 抢在固有方法之前、固有方法的反超、E0034 歧义、
以及「可变性不参与查找、只在调用期报错」这件事。
"""

from resolve import (
    Ambiguity,
    Method,
    MethodTable,
    apply_call,
    candidate_receivers,
    resolve,
    std_env,
)


def main() -> None:
    env = std_env()

    print("[1] 候选接收者列表：Box<[i32;2]>（Reference 官方例子，共 9 项）")
    for i, c in enumerate(candidate_receivers("Box<[i32;2]>", env)):
        print("    #%d  %s" % (i, c))

    print("\n[2] trait 的 &self 抢在 struct 的 &mut self 之前")
    t = MethodTable()
    t.add("Foo", Method("bar", "inherent", "&mut self"))
    t.add("Foo", Method("bar", "Bar", "&self"))
    r = resolve("bar", "Foo", env, t)
    print("    %r" % (r,))

    print("\n[3] 同一个候选类型上，固有方法压过 trait 方法")
    t3 = MethodTable()
    t3.add("Foo", Method("baz", "inherent", "&self"))
    t3.add("Foo", Method("baz", "Baz", "&self"))
    print("    %r" % (resolve("baz", "Foo", env, t3),))

    print("\n[4] 两个 trait 同名方法 → E0034 歧义")
    t4 = MethodTable()
    t4.add("Foo", Method("dup", "Alpha", "&self"))
    t4.add("Foo", Method("dup", "Beta", "&self"))
    try:
        resolve("dup", "Foo", env, t4)
        print("    （未歧义，异常）")
    except Ambiguity as e:
        print("    %s" % e)

    print("\n[5] 可变性不参与查找，只在调用期检查")
    t5 = MethodTable()
    t5.add("Foo", Method("bump", "inherent", "&mut self"))
    r5 = resolve("bump", "Foo", env, t5)
    print("    查找结果：%r" % (r5,))
    print("    不可变绑定调用 → %s" % (apply_call(r5, place_is_mut=False) or "OK",))
    print("    可变绑定调用   → %s" % (apply_call(r5, place_is_mut=True) or "OK",))


if __name__ == "__main__":
    main()
