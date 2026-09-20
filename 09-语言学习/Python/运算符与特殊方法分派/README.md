# Python · 运算符与特殊方法分派

> `a + b` 到底调谁的 `__add__`?`==` 会不会反过来调?`__iadd__` 缺失时会发生什么?
> 这些规则全写在语言参考的**数据模型**一章里,但散落在正文与脚注中。
> 本 demo 把它们做成可执行的调用日志,一条条钉死。

## 一、简介

数据模型规定了每个运算符到特殊方法的映射。真正容易记错的是三条**元规则**:

1. 方法返回 `NotImplemented` 表示"我不会",解释器继续找下家;
2. **右操作数是左操作数的子类**时,反射方法**优先于**左操作数的方法;
3. **同类型**时不尝试反射方法。

## 二、原理详解

### 1. `NotImplemented` 是"我不会",不是"结果是假"

它是 `NotImplementedType` 的单例,`repr` 就是 `NotImplemented`。
把它当布尔值用在 3.9 起发 `DeprecationWarning`,**3.14 起直接 `TypeError`** ——
因为 `NotImplemented` 的真值是 `True`,`if x.__eq__(y) is not NotImplemented:` 才是正确写法
(不过更常见的是写 `if x.__eq__(y) is NotImplemented: return NotImplemented`)。

### 2. 二元算术的分派顺序

以 `x - y` 为例(规范原文):

> `type(y).__rsub__(y, x)` 在 **`type(x).__sub__(x, y)` 返回 `NotImplemented`**
> **或者 `type(y)` 是 `type(x)` 的子类**时被调用。

也就是说顺序不是固定的"先左后右":

| 情形 | 调用顺序 |
| --- | --- |
| 左返回 `NotImplemented`,右是无关类型 | `L.__add__` → `R.__radd__` |
| 右是左的子类且改写了反射方法 | **`R.__radd__`**(左的 `__add__` 根本没被调用) |
| 左右同类型 | 只调 `L.__add__`;失败即 `TypeError`,**不试反射**(脚注 [4]) |

第二条的意义(脚注 [5]):它让子类能覆盖祖先的运算。

### 3. 一个反直觉的坑:`__add__ = None` 是**阻断**

规范脚注 [3] 明确写道:不要把方法设成 `None` 来"迫使回退到右操作数的反射方法" ——
那会**相反地显式阻断**回退。本 demo 用 `Blocked.__add__ = None` + 一个带 `__radd__` 的
`Provider` 验证:`Blocked() + Provider()` 直接 `TypeError`,`Provider.__radd__` 没有机会执行。

### 4. 增强赋值 `x += y` 的回退链

```
x.__iadd__(y)  →  缺失或返回 NotImplemented  →  x.__add__(y) → y.__radd__(x)
```
注意回退后 `x = <结果>`,名字被**重新绑定**;只有 `__iadd__` 真正原地改了 `self` 时才是原地。
这也解释了官方 FAQ 里
`a_tuple[i] += ['item']` 为什么"加法成功了却抛异常":`list.__iadd__` 原地改完返回自身,
随后解释器又去执行 `tup[i] = <结果>`,这一步对元组非法 —— 两步都真发生了。

### 5. 比较运算符

- 反射关系:`__lt__` ↔ `__gt__`,`__le__` ↔ `__ge__`,**`__eq__` 与 `__ne__` 各自是自己的反射**。
- 子类优先规则同样适用(但规范强调:**虚拟子类 `ABC.register` 不算**)。
- `object.__eq__` 默认实现:`True if x is y else NotImplemented`;
  `object.__ne__` **委托给 `__eq__` 并取反**(除非结果是 `NotImplemented`)。
- 若**没有任何**方法返回非 `NotImplemented` 的值,`==` 退化为 `is`,`!=` 退化为 `is not`。
- 规范强调:除了上面这条默认,**比较运算符之间没有其他隐含关系**
  —— 定义了 `__lt__` 不代表 `<` 的反面能用,`<=` 要自己写或用 `functools.total_ordering`。

### 6. `__hash__` 契约

> 重写 `__eq__` 而不定义 `__hash__` 的类,其 `__hash__` 会被**隐式置为 `None`**,
> 实例不可哈希。

想保留父类的哈希,必须**显式**告诉解释器:`__hash__ = <ParentClass>.__hash__`。
契约本身:相等的对象必须有相等的哈希值,否则它们进 `set` 后会被当成两个元素
(本 demo 用 `Point` 验证了 `len({Point(1), Point(1)}) == 1`)。

### 7. 真值测试

- 有 `__bool__` 用 `__bool__`,没有则回退 `__len__`(0 为假),再没有则恒为真。
- `__bool__` **必须返回 `bool`**,返回 `1` 会 `TypeError`。
- `__len__` 返回负数会 `ValueError`。

## 三、对比

| 语言 | 运算符重载 |
| --- | --- |
| Python | 特殊方法 + `NotImplemented` 回退 + 子类优先 |
| C++ | 自由函数 / 成员函数重载,编译期解析,无运行时回退 |
| Rust | `trait Add` + 关联类型 `Output`,编译期解析 |
| Ruby | 方法即运算符(`def +`),右操作数无法参与分派 |

`NotImplemented` 这套"运行时协商"是 Python 能用同一套语法粘合 `decimal.Decimal`、
`numpy.ndarray`、自定义类的关键,代价是每次运算多一次属性查找。

## 四、环境与运行

- Python 3.x(本 demo 在 **3.13** 实跑;`NotImplemented` 布尔求值的报错行为 3.14 才变)。
- 仅用标准库。

文件:`main.py` 里是一组"会把调用记进 `LOG`"的类,`selfcheck_dunders.py` 读 `LOG` 判断
到底调了谁、按什么顺序调。

```bash
python selfcheck_dunders.py
```

## 五、关键代码

```python
class Lhs:
    def __add__(self, other):
        LOG.append("Lhs.__add__"); return NotImplemented   # "我不会"

class RhsSub(Lhs):                       # 子类 + 改写反射方法
    def __radd__(self, other):
        LOG.append("RhsSub.__radd__"); return "sub"

Lhs() + RhsSub()      # LOG == ["RhsSub.__radd__"] —— 左操作数的方法没被调用
```

```python
class Blocked:
    __add__ = None                       # 阻断回退,不是促成回退
```

```python
class KeepHash(NoHash):
    __hash__ = object.__hash__           # 必须显式写,否则继承到的是 None
```

## 六、性能边界

- 每次运算符调用都要做一次 `type(x).__add__` 的类型槽查找 + 可能的 `type(y).__radd__` 查找,
  比 C++ 的编译期绑定慢一个量级;热循环里靠 C 层特化(如 `BINARY_OP` 内联缓存)缓解。
- `__eq__` 里做深比较 + `__hash__` 里遍历大对象,会让 dict/set 操作退化;
  惯例是把不可变字段做成元组再 `hash(tuple)`。
- 自定义 `__eq__` 但不自定义 `__hash__` 时,实例进不了 dict/set —— 这常被误判为"业务逻辑 bug"。

## 七、注意事项与常见坑

1. **`return NotImplemented` 与 `return None` 完全不同**:后者会被当成"结果是 `None`"。
2. **`__add__ = None` 阻断回退**(脚注 [3]),别用它"委托给右边"。
3. 同类型运算**不会**尝试反射方法 —— 想让同类型的两种实例互相协作,要在 `__add__` 里自己判别。
4. `x += y` 不一定是原地:回退分支会重新绑定名字。可变默认参数 / 元组元素这类场景最容易踩。
5. 重写 `__eq__` 会**静默**让类变成不可哈希;若类本应不可变,记得同时写 `__hash__`。
6. `__ne__` 的默认实现是对 `__eq__` 取反,**只在 `__eq__` 不返回 `NotImplemented` 时**才成立。
7. 比较运算符之间没有隐含关系:只写 `__lt__` 的话,`>` 靠反射能用,但 `<=` / `>=` 不会自动出现。
8. `__bool__` 必须返回真正的 `bool`;`__len__` 不能返回负数。

## 八、参考资料(均为本 demo 实读)

- [Python 语言参考 · 3. 数据模型](https://docs.python.org/3/reference/datamodel.html)
  —— 3.2.2 `NotImplemented`;`object.__lt__` 等比较方法与其反射规则;
  `__radd__` 系列与脚注 [3][4][5];`__iadd__` 系列与其回退;
  `__hash__` 契约与"隐式置 None";真值测试
- 官方 FAQ:[Why does `a_tuple[i] += ['item']` raise an exception when the addition works?](https://docs.python.org/3/faq/programming.html#why-does-a-tuple-i-item-raise-an-exception-when-the-addition-works)
