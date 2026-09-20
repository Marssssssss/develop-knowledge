# -*- coding: utf-8 -*-
"""
Python · MRO 与 C3 线性化

按 CPython Objects/typeobject.c 的 pmerge() 逐行重实现 C3,
再拿真实类的 __mro__ 当 oracle 对拍;最后验证 super() 的"沿 MRO 向后找"语义。

参考(实读):
  - https://www.python.org/download/releases/2.3/mro/   (C3 原始论文式说明)
  - https://docs.python.org/3/reference/datamodel.html  (特殊方法查找 / super)
  - CPython Objects/typeobject.c: tail_contains / pmerge / mro_implementation / mro_check
"""

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


class MROError(TypeError):
    pass


def tail_contains(seq, whence, o):
    """typeobject.c: tail_contains —— 只看 whence 之后的**剩余**部分。"""
    return o in seq[whence + 1:]


def pmerge(to_merge):
    """typeobject.c: pmerge。to_merge = [L[B1], ..., L[BN], (B1..BN)]。"""
    acc = []
    remain = [0] * len(to_merge)
    while True:
        empty_cnt = 0
        progress = False
        for i, cur in enumerate(to_merge):
            if remain[i] >= len(cur):
                empty_cnt += 1
                continue
            candidate = cur[remain[i]]
            if any(tail_contains(lst, remain[j], candidate)
                   for j, lst in enumerate(to_merge)):
                continue
            acc.append(candidate)
            for j, lst in enumerate(to_merge):
                if remain[j] < len(lst) and lst[remain[j]] is candidate:
                    remain[j] += 1
            progress = True
            break                      # 每轮只取一个候选,然后重新扫描
        if progress:
            continue
        if empty_cnt != len(to_merge):
            raise MROError("Cannot create a consistent method resolution order")
        return acc


def linearize(cls):
    """L[C] = C + merge(L[B1] ... L[BN], B1 ... BN)"""
    bases = cls.__bases__
    if not bases:
        return [cls]
    if len(bases) == 1:                                # 源码里的 fast path
        return [cls] + linearize(bases[0])
    seqs = [linearize(b) for b in bases] + [list(bases)]
    return [cls] + pmerge(seqs)


def names(seq):
    return [c.__name__ for c in seq]


# ---------------------------------------------------------------- 1. 与 __mro__ 对拍
class O: pass
class F(O): pass
class E(O): pass
class D(O): pass
class C(D, F): pass
class B(D, E): pass
class A(B, C): pass


def demo_c3_against_python():
    for cls in (O, F, E, D, C, B, A):
        mine = linearize(cls)
        real = list(cls.__mro__)
        assert mine == real, f"{cls.__name__}: 手算 {names(mine)} != 官方 {names(real)}"
    assert names(A.__mro__) == ["A", "B", "C", "D", "E", "F", "O", "object"], names(A.__mro__)
    ok("C3 手算结果与 __mro__ 完全一致(含 论文里的 A(B,C) 七类层次)")

    class X(O): pass
    class Y(O): pass
    class P(X, Y): pass
    class Q(Y, X): pass
    assert names(P.__mro__) == ["P", "X", "Y", "O", "object"]
    assert names(Q.__mro__) == ["Q", "Y", "X", "O", "object"]
    ok("局部优先:基类书写顺序决定同层顺序(P(X,Y) 与 Q(Y,X) 相反)")


def demo_inconsistent():
    """论文里的 'serious order disagreement':X 在 YXO 的尾、Y 在 XYO 的尾 → 无好头。"""
    class X(O): pass
    class Y(O): pass
    class P(X, Y): pass
    class Q(Y, X): pass
    try:
        type("R", (P, Q), {})
        raise AssertionError("应当拒绝创建这个类")
    except TypeError as e:
        assert "consistent method resolution order" in str(e), f"实为 {e}"
    # 手算路径必须**独立**复现同一失败(不能靠上面那句 TypeError 顺带兜住)
    try:
        pmerge([names_seq(P), names_seq(Q), [P, Q]])
        raise AssertionError("手算 C3 也应失败")
    except MROError:
        pass
    ok("顺序冲突:C3 找不到好头 → 官方 TypeError,手算同样失败")


def names_seq(cls):
    return list(cls.__mro__)


def demo_duplicate_base():
    try:
        type("Dup", (O, O), {})
        raise AssertionError("重复基类应被拒")
    except TypeError as e:
        assert "duplicate base class" in str(e), f"实为 {e}"
    ok("check_duplicates:同一基类出现两次 → duplicate base class")


def demo_monotonicity():
    """单调性:父类的 MRO 必须是子类 MRO 的**子序列**(不重排)。"""
    def is_subsequence(sub, full):
        it = iter(full)
        return all(any(x is y for y in it) for x in sub)

    class L1(O): pass
    class L2(L1): pass
    class L3(O): pass
    class L4(L2, L3): pass
    class L5(L4, C): pass
    for cls in (L4, L5, A, C, B):
        mro = cls.__mro__
        for base in cls.__bases__:
            assert is_subsequence(base.__mro__, mro), \
                f"{base.__name__} 的 MRO 在 {cls.__name__} 中被重排了"
    ok("单调性:任一基类的 MRO 都是子类 MRO 的子序列(不重排)")


# ---------------------------------------------------------------- 2. super()
class Base:
    def who(self):
        return "Base"


class Left(Base):
    def who(self):
        return "Left+" + super().who()


class Right(Base):
    def who(self):
        return "Right+" + super().who()


class Child(Left, Right):
    def who(self):
        return "Child+" + super().who()


def demo_super_cooperative():
    assert Child().who() == "Child+Left+Right+Base"
    assert names(Child.__mro__)[:4] == ["Child", "Left", "Right", "Base"]
    ok("合作式多继承:super() 沿 type(self) 的 MRO 逐个接力,不是只调父类")


def demo_super_is_next_in_mro():
    class A2(Base):
        def who(self):
            return "A2"

    class B2(Base):
        def who(self):
            return "B2"

    class D2(A2, B2):
        pass

    d = D2()
    assert super(A2, d).who() == "B2", "super(A2, d) 找的是 D2 的 MRO 里 A2 之后的那一项"
    assert super(B2, d).who() == "Base"
    assert super(D2, d).who() == "A2"
    ok("super(X, obj) 的落点由 type(obj).__mro__ 决定,与 X 的父类无关")


def demo_super_errors():
    class S(Base):
        pass

    s = S()
    other = Base()
    try:
        super(S, other)                     # other 不是 S 的实例
        raise AssertionError("应当抛 TypeError")
    except TypeError as e:
        assert "super(type, obj)" in str(e) and "instance" in str(e), f"实为 {e}"

    # 类方法场景:第二参数传类型
    assert super(S, S).__self_class__ is S
    assert super(S, s).__self__ is s

    def outside(obj):
        return super().who()                # 函数不在类体内 → 没有 __class__ 单元变量

    try:
        outside(s)
        raise AssertionError("应当抛 RuntimeError")
    except RuntimeError as e:
        assert "__class__" in str(e), f"实为 {e}"
    ok("super:type/obj 不匹配抛 TypeError;脱离类体用零参 super() 抛 __class__ cell 未找到")


def demo_custom_mro():
    """mro_check 只校验「元素是类且布局兼容」,并不要求把自己放在首位 —— 自定义 mro() 真的是最终裁决者。"""
    class Meta(type):
        def mro(cls):
            return [cls, Base, object]

    class Weird(metaclass=Meta):
        pass

    assert names(Weird.__mro__) == ["Weird", "Base", "object"]
    assert Weird().who() == "Base", "自定义 MRO 生效:方法按新顺序查到 Base"

    # 反例:把自己的基类从 MRO 里剔除,方法会凭空消失 —— 官方照样接受
    class Meta2(type):
        def mro(cls):
            return [cls, object]

    class Weird2(Base, metaclass=Meta2):
        pass

    assert names(Weird2.__mro__) == ["Weird2", "object"]
    assert not hasattr(Weird2, "who"), "MRO 里没有 Base,who 就查不到了"
    ok("元类自定义 mro() 直接成为最终顺序(mro_check 只查『是类且布局兼容』,不查是否含自身基类)")


def main():
    print("1. C3 线性化")
    demo_c3_against_python()
    demo_inconsistent()
    demo_duplicate_base()
    demo_monotonicity()
    print("2. super()")
    demo_super_cooperative()
    demo_super_is_next_in_mro()
    demo_super_errors()
    demo_custom_mro()
    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
