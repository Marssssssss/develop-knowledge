# Python · 作用域、闭包与单元变量

> "这个名字到底在哪儿?"——Python 的回答分三层:**块**(文本)、**作用域**(可见性)、**单元变量**(运行时容器)。
> 本 demo 从 `UnboundLocalError` 一路追到 `LOAD_DEREF` 字节码,并复现两个静默出错的经典场景。

## 一、简介

- **块(block)**:模块、函数体、类定义各自是一个块。
- **绑定操作**:形参、赋值、`import`、`for` 目标、`with ... as`、`except as`、`type` 语句、类型参数列表、
  **赋值表达式(`:=`)**。
- **作用域**:名字在某个块里被绑定,它就是该块的局部变量;在函数里绑定则向内延伸到嵌套块,
  除非内层块又引入同名绑定。

## 二、原理详解

### 1. 局部性是"整块扫描"出来的

> 若一个名字在块内**任何位置**出现绑定操作,则该名字在**整个块**内都视为当前块的局部名字。

所以先读后赋会炸:

```python
def f():
    print(x)      # UnboundLocalError
    x = 1
```
这条规则让"局部 / 自由变量"的判定完全在**编译期**完成,不需要运行时查字典 —— 这也是
`LOAD_FAST` 比 `LOAD_GLOBAL` 快的原因。

### 2. `global` 与 `nonlocal`

- `global` 把名字指向模块命名空间,且必须出现在所有使用之前。
- `nonlocal` 让名字指向**最近的、外层函数作用域**里已绑定的变量。
- `nonlocal` 的绑定检查在**编译期**完成:外层函数作用域里查不到就是
  `SyntaxError: no binding for nonlocal 'x' found`,**无法用 `try/except` 在运行时捕获**
  (本 demo 用 `compile()` 触发它)。
- **类作用域不算"外层函数作用域"**,所以不能在方法里 `nonlocal` 一个类属性。

### 3. 单元变量(cell):闭包的实现

外层的局部变量如果被内层函数引用,就不再放在栈槽里,而是装进一个 **cell 对象**:

```python
def make_adder(n):
    def add(x): return x + n     # n 是自由变量
    return add

add = make_adder(10)
add.__code__.co_freevars      # ('n',)
make_adder.__code__.co_cellvars  # ('n',)   ← 同一个 cell 的两种叫法
add.__closure__[0].cell_contents # 10
```

字节码上,内层取用 `n` 走 `LOAD_DEREF`(先解引用 cell),而不是 `LOAD_FAST` / `LOAD_GLOBAL`。
**多个闭包共享同一个 cell 对象** —— 一个改了另一个立刻看见,这就是"闭包当私有状态"的实现基础。

### 4. 迟绑定(late binding)

闭包捕获的是**变量**而不是**值**:

```python
fns = [lambda: i for i in range(3)]
[f() for f in fns]        # [2, 2, 2]  ← 三个闭包共享同一个 i
fns = [lambda i=i: i for i in range(3)]   # [0, 1, 2]
```
默认参数在**函数定义时**求值,于是把当时的值固化了下来;`functools.partial` 同理。

### 5. 推导式 = 隐式函数作用域

- 推导式 / 生成器表达式会创建一个隐式函数作用域,**循环变量不会泄漏**到外层(Python 3 起)。
- 但**海象运算符的目标绑定在包含作用域**:`[(w := i) for i in range(3)]` 之后,
  `w` 在外层函数里仍然可见(值为 2),只是不会继续漏到模块层。

### 6. 类作用域的静默陷阱

> 类定义的命名空间不延伸到方法;**推导式与生成器表达式也在此列**。

于是:

```python
G = 100
class C:
    G = 1
    got = list(G + i for i in range(2))     # C.got == [100, 101] ← 用的是全局 G!
```
类里的 `G = 1` 对生成器表达式**不可见**,解释器一路查到全局的 `G = 100` —— **不报错,静默取错值**。
只有当没有同名全局时才会 `NameError: name 'v' is not defined`。
注意:推导式的**最外层可迭代对象**(`range(2)`)是在类作用域里立即求值的,所以只有表达式体受影响。

### 7. 注解作用域是例外

规范明确:注解作用域(PEP 695,3.12 引入;3.14 起按 PEP 649/749 也用于注解)
**可以访问它所在的类命名空间** —— 与普通方法相反:

```python
class A:
    class Nested: pass
    type Alias = Nested        # OK,惰性求值但能看见类作用域

A.Alias.__value__ is A.Nested  # True
```
另外注解作用域内**不允许** `yield` / `await` / `:=`。

## 三、对比

| 语言 | 作用域单位 | 闭包捕获 |
| --- | --- | --- |
| Python | 函数(类与推导式都是特例) | 捕获**变量**(cell 共享) |
| JavaScript | 函数 / 块(`let`) | `var` 是函数作用域,`let` 是块作用域 |
| C++ | 块 | 显式指定捕获值还是引用 |
| Rust | 块 | 借用检查器决定 move / borrow |

"捕获变量而非值"这一点,Python 与 JS 的 `var` 一致;与 C++ 需要显式 `[=]` / `[&]` 不同。

## 四、环境与运行

- Python **3.12+**(`type X = ...` 需要 PEP 695);本 demo 在 **3.13** 实跑,仅用标准库。
- 若跑在 3.11 及更早,`demo_annotation_scope` 会因 `type` 语句语法不支持而失败。

```bash
python main.py
```

## 五、关键代码

```python
def make_adder(n):
    def add(x): return x + n
    return add

add = make_adder(10)
add.__code__.co_freevars            # ('n',)
add.__closure__[0].cell_contents    # 10
dis.get_instructions(add)           # 取 n 走 LOAD_DEREF
```

```python
# 编译期错误必须靠 compile() 触发,try/except 包住调用是抓不到的
compile("def f():\n def g():\n  nonlocal x\n  x=1\n", "<s>", "exec")
# SyntaxError: no binding for nonlocal 'x' found
```

```python
G = 100
class C:
    G = 1
    got = list(G + i for i in range(2))     # [100, 101] —— 静默用了全局 G
```

## 六、性能边界

- `LOAD_FAST`(局部)是数组下标访问,最快;`LOAD_DEREF`(闭包)多一次 cell 解引用;
  `LOAD_GLOBAL`(全局)与 `LOAD_NAME`(类体/模块动态查找)最慢 ——
  类体里的名字查找走 `LOAD_NAME`,是类体代码偏慢的原因之一。
- 闭包会让被捕获的对象**活得更久**(cell 被 function 持有),可能延长大对象的生命周期。
- 每个 cell 只在需要时创建(有内层函数引用才升级为 cell),不影响没有闭包的函数。

## 七、注意事项与常见坑

1. `UnboundLocalError` 的根因几乎总是"后面又赋了值" —— 想读全局就 `global`,想读外层就别在本层赋值。
2. `nonlocal` 的绑定检查在**编译期**,只能用 `compile()` / 导入时触发,运行时 `try/except` 抓不到。
3. 类作用域**不**被推导式继承 —— 同名全局会被静默使用,是最难排查的一类 bug。
4. 循环里造闭包要小心迟绑定;修法用默认参数或 `partial`。
5. `lambda` 里的默认参数在**定义时**求值,不是为了"延迟";需要在调用时求值得写 `lambda x=None: (x or compute())`。
6. 用 `exec` / `eval` 执行的代码有自己的命名空间,不会自动拿到调用方的局部变量
   (要显式传 `globals()` / `locals()` 两个字典)。
7. 闭包持有 cell 会阻止被捕获对象被回收 —— 长生命周期的闭包里别捕获大对象。

## 八、参考资料(均为本 demo 实读)

- [Python 语言参考 · 4. 执行模型](https://docs.python.org/3/reference/executionmodel.html)
  —— 块的定义、绑定操作清单、作用域与"整块扫描"规则、`global` / `nonlocal`、
  类作用域与推导式的例外、注解作用域(3.12/3.13/3.14 的变更)、惰性求值
- [PEP 227 — Statically Nested Scopes](https://peps.python.org/pep-0227/)
  —— 从 Python 2.0 的三命名空间到词法作用域的变更说明
- [PEP 695 — Type Parameter Syntax](https://peps.python.org/pep-0695/)(注解作用域的由来)
