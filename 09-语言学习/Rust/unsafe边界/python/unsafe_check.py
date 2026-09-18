"""unsafe 边界自检：五大 superpower、借用检查不关闭、UnsafeCell、transmute、safe 抽象。"""

from unsafe_model import (
    CALL_UNSAFE_FN, Cell, DEREF_RAW, IMPL_UNSAFE_TRAIT, MUT_STATIC, RawPtr, Ref,
    SUPERPOWERS, UNION_FIELD, UB, CompileError, create_raw, perform,
    split_at_mut, transmute, unsafe_split_impl,
)


def check(label, cond, detail=""):
    if not cond:
        raise AssertionError(f"{label} FAILED {detail}")


def run():
    # ---- 1. 五大 superpower（官方列表，一个不多一个不少）
    check("1.1 恰好五件", len(SUPERPOWERS) == 5, str(len(SUPERPOWERS)))
    for op in (DEREF_RAW, CALL_UNSAFE_FN, MUT_STATIC, IMPL_UNSAFE_TRAIT, UNION_FIELD):
        check(f"1.2 unsafe 里可以做：{op}", perform(op, True))
        try:
            perform(op, False)
            raise AssertionError(f"1.3 安全代码不该能做 {op}")
        except CompileError as e:
            check(f"1.3 安全代码被拦：{op}", "E0133" in str(e), str(e))
    # 普通操作不受影响
    check("1.4 普通操作不需要 unsafe", perform("read a field", False))

    # ---- 2. unsafe 不关借用检查（官方原话）
    shared = Ref(1, mutable=False)
    try:
        shared.write(2)
        raise AssertionError("2.1 应当报错")
    except CompileError as e:
        check("2.1 共享引用不可写", "E0596" in str(e), str(e))
    mutable = Ref(1, mutable=True)
    mutable.write(2)
    check("2.2 可变引用可写（与 unsafe 无关）", mutable.read() == 2)
    check("2.3 借用检查不看是否在 unsafe 块内 —— 模型里没有 in_unsafe 参数可绕过",
          "in_unsafe" not in Ref.write.__code__.co_varnames)

    # ---- 3. 裸指针：创建安全、解引用不安全
    cell = Cell(41)
    p = create_raw(cell, mutable=True)
    check("3.1 安全代码就能造出裸指针", isinstance(p, RawPtr))
    check("3.2 const 指针不能写", not RawPtr(id(cell), target=cell, mutable=False).mutable)
    try:
        p.write(42, in_unsafe=False)
        raise AssertionError("3.3 应当被拦")
    except CompileError:
        pass
    p.write(42, in_unsafe=True)
    check("3.4 unsafe 里写入生效", cell.value == 42, str(cell.value))

    dangling = RawPtr(id(cell), target=None, mutable=True)
    try:
        dangling.deref(in_unsafe=True)
        raise AssertionError("3.5 悬垂指针应 UB")
    except UB as e:
        check("3.5 悬垂解引用 = UB（编译器不保证拦下）", "dangling" in str(e), str(e))
    nullp = RawPtr(0, target=None, mutable=False)
    try:
        nullp.deref(in_unsafe=True)
        raise AssertionError("3.6 空指针应 UB")
    except UB:
        pass

    # ---- 4. UnsafeCell：只解除 &T 的不可变保证
    plain = Cell(1, interior=False)
    ucell = Cell(1, interior=True)
    try:
        plain.write_through_shared(2, in_unsafe=True)
        raise AssertionError("4.1 普通 &T 改写应 UB")
    except UB as e:
        check("4.1 无 UnsafeCell 的 &T 改写 = UB", "UnsafeCell" in str(e), str(e))
    check("4.2 有 UnsafeCell 就可以改（内部可变性）",
          ucell.write_through_shared(2, in_unsafe=True) == 2)
    try:
        plain.write_through_shared(3, in_unsafe=False)
        raise AssertionError("4.3 应当被拦")
    except CompileError:
        pass
    for c in (plain, ucell):
        try:
            c.aliasing_mut()
            raise AssertionError("4.4 交叠 &mut 应永远 UB")
        except UB:
            pass
    check("4.5 UnsafeCell::get() 给出的是可写裸指针",
          ucell.get().mutable is True)

    # ---- 5. transmute：唯一编译期检查是尺寸
    try:
        transmute(4, 8)
        raise AssertionError("5.1 尺寸不同应编译失败")
    except CompileError as e:
        check("5.1 尺寸不同 = 编译错误（官方：唯一 restriction）",
              "different sizes" in str(e), str(e))
    check("5.2 repr(C)：尺寸同 + 布局有定义 → 可以", transmute(4, 4) == "ok")
    try:
        transmute(4, 4, layout_defined=False)
        raise AssertionError("5.3 repr(Rust) 布局无保证应 UB")
    except UB as e:
        check("5.3 repr(Rust) 布局无保证 = UB", "repr(Rust)" in str(e), str(e))
    try:
        transmute(1, 1, creates_invalid_value=True)     # nomicon: 不要把 3 转成 bool
        raise AssertionError("5.4 造出非法值应 UB")
    except UB:
        pass
    try:
        transmute(8, 8, ref_to_mut=True)
        raise AssertionError("5.5 & → &mut 应 UB")
    except UB as e:
        check("5.5 & → &mut 永远是 UB", "always" in str(e), str(e))
    try:
        transmute(8, 8, unbounded_lifetime=True)
        raise AssertionError("5.6 未标注生命周期应 UB")
    except UB:
        pass

    # ---- 6. safe abstraction：split_at_mut 把 UB 变成 panic
    data = [1, 2, 3, 4, 5, 6]
    left, right = split_at_mut(data, 3)
    check("6.1 正常切分", left == [1, 2, 3] and right == [4, 5, 6], f"{left}/{right}")
    try:
        unsafe_split_impl(data, 99)
        raise AssertionError("6.2 裸实现越界应 UB")
    except UB:
        pass
    try:
        split_at_mut(data, 99)
        raise AssertionError("6.3 safe API 越界应 panic 而非 UB")
    except AssertionError as e:
        check("6.3 safe API 把 UB 变成 panic（安全边界的全部意义）",
              "out of bounds" in str(e), str(e))

    # ---- 7. 官方建议：unsafe 块要小 —— 用「superpower 计数」量化
    def audit(ops):
        """数一下一段代码里出现了几次 superpower。"""
        return sum(1 for o in ops if o in SUPERPOWERS)

    small = [DEREF_RAW]
    big = [DEREF_RAW, CALL_UNSAFE_FN, MUT_STATIC, UNION_FIELD]
    check("7.1 小 unsafe 块只做一件事", audit(small) == 1, str(audit(small)))
    check("7.2 大 unsafe 块的 superpower 数是它的 4 倍", audit(big) == 4 * audit(small),
          f"{audit(big)} vs {audit(small)}")

    print("unsafe_model: all assertions passed")


if __name__ == "__main__":
    run()
