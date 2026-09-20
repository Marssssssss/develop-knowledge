# Python · MRO 与 C3 线性化

> 多继承里"该调谁的方法"由 **MRO(Method Resolution Order)** 决定。
> Python 2.3 起用 **C3** 算法:它同时保证**局部优先**(基类书写顺序)与**单调性**(子类不重排父类的顺序)。
> 本 demo 按 CPython `Objects/typeobject.c` 的 `pmerge()` 逐行重实现 C3,再用真实类的 `__mro__` 对拍。

## 一、简介

- `__mro__` 是一个元组,属性查找(`type.__getattribute__`)就是**从前往后**扫它。
- `super()` 不是"调父类",而是"**沿 `type(self)` 的 MRO 从 `__class__` 之后继续找**"。
- C3 由 Samuele Pedroni 引入,官方说明文档是
  [The Python 2.3 Method Resolution Order](https://www.python.org/download/releases/2.3/mro/)。

## 二、原理详解

### 1. C3 的定义

记号:`C1 C2 ... CN` 表示列表;`head` 是首元素,`tail` 是其余部分。

```
L[object] = object
L[C(B1 ... BN)] = C + merge(L[B1], ..., L[BN], B1...BN)
```

`merge` 的做法:

> 取第一个列表的头;**若它不在其它任何列表的 tail 中**,就把它加入结果并从所有列表中删除;
> 否则看下一个列表的头。重复直到全部取完 —— 若找不到"好头",则无法构造 MRO。

注意配方里有一个容易被忽略的输入:**除了各基类的线性化,还要并入基类元组本身 `B1...BN`**。
CPython 也正是这么做的(`mro_implementation_unlocked` 里 `to_merge[n] = bases`)。没有它,
"局部优先"就无从表达。

### 2. CPython 的实现细节(读源码得到)

| 源码位置 | 结论 |
| --- | --- |
| `mro_implementation_unlocked` | 单基类走 **fast path**:直接 `[type] + L[base]` |
| 同上 | `to_merge` 的最后一项是**基类元组**,不是某个 MRO |
| `tail_contains(tuple, whence, o)` | 只从 `whence+1` 开始找 —— **已经取走的头不再阻拦** |
| `pmerge` 的 `skip` | 一轮只取一个候选,取到就 `goto again` 从头重新扫描 |
| `check_duplicates` | 同一基类出现两次 → `TypeError: duplicate base class` |
| `mro_check` | 自定义 `mro()` 的返回值**只校验"元素是类 + 布局兼容"**,不校验顺序 |

`tail_contains` 只看"剩余 tail"这一点很关键:已经被取走的头不会再去挡别人,
否则 `merge(AXYO, BYXO, AB)` 之类的情形会过早失败。

### 3. 三条性质

1. **局部优先(local precedence ordering)**:基类书写顺序在 MRO 中被保持 ——
   `class P(X, Y)` 得 `P X Y O`,`class Q(Y, X)` 得 `Q Y X O`。
2. **单调性(monotonicity)**:任一基类的 MRO 都是子类 MRO 的**子序列**(只插入、不重排)。
   本 demo 用 `is_subsequence` 属性在 5 个类上验证。
3. **一致性**:不是所有层次都有解。`X, Y, A(X,Y), B(Y,X), C(A,B)` 会失败:
   ```
   L[C] = C + merge(AXYO, BYXO, AB)
        = C + A + B + merge(XYO, YXO)      # X 在 YXO 的尾,Y 在 XYO 的尾 → 没有好头
   ```
   Python 抛 `TypeError: Cannot create a consistent method resolution order (MRO) for bases X, Y`。

### 4. `super()` 的真实语义

```python
class A2(Base):  ...
class B2(Base):  ...
class D2(A2, B2): pass
super(A2, D2()).who()      # → B2 ! 不是 Base
```
`super(X, obj)` 的落点由 **`type(obj)` 的 MRO** 决定,与 `X` 的父类无关。
这正是合作式多继承能工作的原因:

```python
class Child(Left, Right):
    def who(self): return "Child+" + super().who()
# Child().who() == "Child+Left+Right+Base"
```
`Left.who` 里的 `super().who()` 会走到 `Right`,而不是直接跳到 `Base` ——
因为 `type(self)` 是 `Child`,其 MRO 是 `Child Left Right Base`。

## 三、对比

| 语言 | 多继承方法查找 |
| --- | --- |
| Python | C3 线性化,运行时算出 `__mro__`,`super()` 沿此链接力 |
| C++ | 无线性化,靠作用域限定 `A::f()`,菱形继承需虚继承 |
| Java 8+ | 接口默认方法冲突必须**显式** `X.super.f()` 解决,不做自动线性化 |
| Rust | 无继承,trait 方法冲突同样要求显式消歧 |

## 四、环境与运行

- Python 3.x(本 demo 在 **3.13** 实跑),仅用标准库。

```bash
python main.py
```

## 五、关键代码

```python
def pmerge(to_merge):                     # 复刻 typeobject.c 的 pmerge
    acc, remain = [], [0] * len(to_merge)
    while True:
        empty_cnt, progress = 0, False
        for i, cur in enumerate(to_merge):
            if remain[i] >= len(cur):
                empty_cnt += 1
                continue
            candidate = cur[remain[i]]
            if any(tail_contains(l, remain[j], candidate)
                   for j, l in enumerate(to_merge)):
                continue                  # 是别人 tail 里的元素 → 跳过
            acc.append(candidate)
            ...                           # 从各列表头部删除它
            progress = True
            break
        if progress:
            continue
        if empty_cnt != len(to_merge):
            raise MROError(...)
        return acc
```

```python
def linearize(cls):                       # L[C] = C + merge(...)
    bases = cls.__bases__
    if not bases:            return [cls]
    if len(bases) == 1:      return [cls] + linearize(bases[0])
    return [cls] + pmerge([linearize(b) for b in bases] + [list(bases)])
```

## 六、性能边界

- MRO 只在**建类时**算一次并缓存进 `tp_mro`;改 `__bases__` 会触发 `type_mro_modified` 向下游子类重算。
- 层次很深很宽时 `pmerge` 是 O(n²) 量级(`empty_cnt` 扫描 + 每个候选扫所有列表),
  但 n 是**祖先数量**,实践中远小于实例数,不是热点。
- 属性查找本身靠 `tp_mro` 元组顺序扫描 + 方法缓存(method cache),命中缓存时接近 O(1)。

## 七、注意事项与常见坑

1. **`super()` ≠ 父类**。它是"MRO 中 `__class__` 之后的那个类",在菱形里常常是**兄弟类**。
2. 零参 `super()` 依赖编译器塞进方法里的 **`__class__` 单元变量**。
   把方法搬到类体之外(或用 `exec` 拼出来的函数)就会得到
   `RuntimeError: super(): __class__ cell not found`。
3. `super(X, obj)` 要求 `isinstance(obj, X)`,否则
   `TypeError: super(type, obj): obj must be an instance or subtype of type`。
   第二参数传**类**时得到 unbound super(类方法里用),此时是 `__self_class__` 而非 `__self__`。
4. 元类自定义 `mro()` **会被无条件采纳**(`mro_check` 不校验顺序,甚至不校验是否包含基类)。
   本 demo 里把基类从 MRO 中剔除后,`hasattr(cls, "who")` 直接变 `False` 且不报错 —— 极难排查。
5. 合作式多继承要求**每一层都调用 `super()`**:任何一层漏掉,链条就断了,右边的类被静默跳过。
6. 不同层的签名要兼容:MRO 上各实现接受的参数不一致时,链条只能靠 `*args/**kwargs` 兜住。

## 八、参考资料(均为本 demo 实读)

- [The Python 2.3 Method Resolution Order](https://www.python.org/download/releases/2.3/mro/)
  —— C3 的定义、`merge` 的配方、顺序冲突与"坏 MRO"三个例子均出自此文
- [Python 语言参考 · 数据模型](https://docs.python.org/3/reference/datamodel.html)
  —— 自定义类 / 特殊方法查找 / `object.__mro__`
- CPython [`Objects/typeobject.c`](https://github.com/python/cpython/blob/main/Objects/typeobject.c)
  —— `tail_contains` / `pmerge` / `mro_implementation_unlocked` / `mro_check` / `check_duplicates` /
  `solid_base` 布局校验
