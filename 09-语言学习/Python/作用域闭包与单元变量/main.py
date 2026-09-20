# -*- coding: utf-8 -*-
"""
Python · 作用域、闭包与单元变量

把"一个名字属于哪里"这件事从源码一路追到字节码:
  块(block)→ 作用域(scope)→ 自由变量(free variable)→ 单元变量(cell)→ LOAD_DEREF。

参考(实读):
  - https://docs.python.org/3/reference/executionmodel.html
    (块、绑定操作、作用域、global/nonlocal、类作用域与推导式的例外、注解作用域)
  - https://peps.python.org/pep-0227/  (静态嵌套作用域)
"""
import dis

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


def opnames(fn):
    return [i.opname for i in dis.get_instructions(fn)]


# ------------------------------------------------- 1. 局部性:整块扫描
def demo_unbound_local():
    def f():
        try:
            return x          # 后面出现了 x = 1,于是整个块里 x 都是局部的
        except UnboundLocalError as e:
            return f"UnboundLocalError: {e}"
        x = 1

    assert "UnboundLocalError" in f(), f()
    assert "local variable 'x'" in f()

    def g():
        return GV             # 没有绑定 → 自由变量 → 去全局找

    assert g() == "global-y"
    ok("局部性判定:块内**任何位置**出现绑定,该名字全块为局部 —— 先读后赋会 UnboundLocalError")


GV = "global-y"


# ------------------------------------------------- 2. global 与 nonlocal
def demo_global_nonlocal():
    counter = []

    def outer():
        n = 0

        def bump():
            nonlocal n
            n += 1
            return n

        counter.append(bump)
        return bump

    bump = outer()
    assert bump() == 1 and bump() == 2, "nonlocal 让内层函数改写外层的 n"

    def compile_or_error(src):
        try:
            compile(src, "<s>", "exec")
            return None
        except SyntaxError as e:
            return e

    # nonlocal 是**编译期**检查:外层函数作用域里没有这个名字就直接 SyntaxError,
    # 因此只能靠 compile() 触发,不能 try/except 包住调用。
    e = compile_or_error(
        "def outer():\n"
        "    def inner():\n"
        "        nonlocal absent\n"
        "        absent = 1\n")
    assert e is not None and "no binding for nonlocal" in str(e), f"实为 {e}"

    e = compile_or_error(
        "def f():\n"
        "    class K:\n"
        "        y = 1\n"
        "        def m(self):\n"
        "            nonlocal y\n"          # 类作用域不算"外层函数作用域"
        "            return y\n")
    assert e is not None and "no binding for nonlocal" in str(e), f"实为 {e}"
    ok("nonlocal:只认外层**函数**作用域,编译期查不到就 SyntaxError;类作用域不算")


# ------------------------------------------------- 3. 单元变量与字节码
def make_adder(n):
    def add(x):
        return x + n          # n 是自由变量
    return add


def demo_cell():
    add = make_adder(10)
    assert add(5) == 15
    assert add.__closure__ is not None and len(add.__closure__) == 1
    cell = add.__closure__[0]
    assert cell.cell_contents == 10, "单元变量里存的是外层那个 n 的值"
    assert add.__code__.co_freevars == ("n",)
    assert make_adder.__code__.co_cellvars == ("n",), "外层函数身上叫 cellvars"
    ops = opnames(add)
    assert "LOAD_DEREF" in ops and "LOAD_FAST" in ops, ops
    assert "LOAD_GLOBAL" not in ops, f"n 不走全局查找: {ops}"
    plain = lambda x: x + 1
    assert "LOAD_FAST" in opnames(plain) and "LOAD_DEREF" not in opnames(plain)
    ok("单元变量:外层 co_cellvars / 内层 co_freevars,内层取用走 LOAD_DEREF 而非 LOAD_GLOBAL")


def demo_cell_is_shared():
    """多个闭包共享**同一个**单元变量,而不是各自复制一份。"""
    makers = []

    def outer():
        v = 0

        def getter():
            return v

        def setter(x):
            nonlocal v
            v = x

        makers.extend([getter, setter])
        return getter, setter

    getter, setter = outer()
    setter(7)
    assert getter() == 7, "getter 看到的单元变量被 setter 改了"
    assert getter.__closure__[0] is setter.__closure__[0], "是同一个 cell 对象"
    ok("闭包共享同一个 cell 对象:一个改了另一个立刻看见(这就是闭包能当'私有状态'的原因)")


# ------------------------------------------------- 4. 迟绑定
def demo_late_binding():
    fns = [lambda: i for i in range(3)]
    assert [f() for f in fns] == [2, 2, 2], "经典陷阱:三个闭包捕获的是同一个 i"
    fixed = [lambda i=i: i for i in range(3)]
    assert [f() for f in fixed] == [0, 1, 2], "默认参数在**定义时**求值,固化了当时的值"

    from functools import partial
    assert [f() for f in [partial(lambda i: i, i) for i in range(3)]] == [0, 1, 2]
    ok("迟绑定:闭包存的是变量不是值;修法是默认参数(或 partial)在定义时取值")


# ------------------------------------------------- 5. 推导式的隐式函数作用域
def demo_comprehension_scope():
    ns = {}
    exec("r = [i * 2 for i in range(3)]", ns)
    assert ns["r"] == [0, 2, 4]
    assert "i" not in ns, "Python 3 的推导式不把循环变量泄漏到外层"

    def f():
        got = [(w := i) for i in range(3)]     # 海象绑定到**包含作用域**
        return got, w

    got, w = f()
    assert got == [0, 1, 2] and w == 2, "海象的目标落在函数作用域,推导式结束后仍可见"
    assert "w" not in globals(), "但不会继续漏到模块层"
    ok("推导式是隐式函数作用域:循环变量不外泄;海象运算符的目标却绑定在包含作用域里")


G = 100


def demo_class_scope_gotcha():
    """类作用域**不**延伸到推导式/生成器表达式 —— 于是会静默退回全局。"""
    class C:
        G = 1                                   # 类级同名变量
        got = list(G + i for i in range(2))     # 用的是哪个 G?

    assert C.got == [100, 101], f"实为 {C.got} —— 用的是全局 G,不是类里的 G"
    assert C.G == 1

    err = None
    try:
        class D:
            v = 42
            got = list(v + i for i in range(2))
    except NameError as e:
        err = e
    assert err is not None and "name 'v'" in str(err), f"没有同名全局时才 NameError: {err}"
    ok("类作用域的坑:推导式看不见类里的名字 —— 有同名全局就**静默用全局**,否则 NameError")


def demo_annotation_scope():
    """PEP 695 的注解作用域**能**访问类命名空间(与类里的普通函数相反)。"""
    class A:
        class Nested:
            pass

        type Alias = Nested                    # 惰性求值,但可访问类作用域

    assert A.Alias.__value__ is A.Nested


def demo_method_cannot_see_class_scope():
    class A:
        N = 10

        def m(self):
            return N                            # 方法体看不见类作用域

    try:
        A().m()
        raise AssertionError("方法里直接引用类名应 NameError")
    except NameError:
        pass
    ok("对照:类里的普通方法看不见类作用域,注解作用域却可以(PEP 695 / 3.12+)")


def main():
    print("1. 名字绑定")
    demo_unbound_local()
    print("2. global / nonlocal")
    demo_global_nonlocal()
    print("3. 单元变量")
    demo_cell()
    demo_cell_is_shared()
    print("4. 迟绑定")
    demo_late_binding()
    print("5. 推导式与类作用域")
    demo_comprehension_scope()
    demo_class_scope_gotcha()
    demo_annotation_scope()
    demo_method_cannot_see_class_scope()
    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
