# Python · 迭代器协议与 `itertools` / `functools`

> 迭代是 Python 最基础的协议,而 `itertools` / `functools` 是这条协议上长出的两棵大树。
> 本 demo 把协议与库两侧都**手写一遍**,再与 CPython 官方实现逐条对拍 ——
> 对拍出来的"反直觉"结果(如 `lru_cache` 单参数时 `True` 命中 `1.0` 的缓存)全部保留为断言。

## 一、简介

- **迭代器协议**:`__iter__()` 返回迭代器,迭代器再实现 `__next__()` 与 `StopIteration`。
- **`itertools`**:一组用 C 实现的、内存占用恒定的迭代器构造/消费工具。
- **`functools`**:高阶函数工具箱(`lru_cache` / `singledispatch` / `reduce` / `partial` / `cached_property`)。

三者关系:`itertools` 吃迭代器也产迭代器,`functools.lru_cache` 可以对产出的函数做记忆化。

## 二、原理详解

### 1. 容器 vs 迭代器(最容易混的一点)

| | 容器 (`list`, 自定义 `Count`) | 迭代器 (`CountIter`, `tee` 结果) |
| --- | --- | --- |
| `__iter__` | 每次**造一个新的**迭代器 | 返回 `self` |
| 可否重复遍历 | ✅ 可以 | ❌ 一次性 |
| 典型判别 | `iter(x) is not x` | `iter(x) is x` |

`for` 循环只要求**可迭代**(`__iter__` 存在即可),不要求是迭代器 —— 所以列表能反复 `for`,
而文件对象、`zip`、`map` 的结果只能 `for` 一次。

### 2. `iter(callable, sentinel)`:两参数形式的坑

```python
list(iter(lambda: read(2), b""))   # 读到 b"" 就停,且这个 b"" 不会被产出
```
哨兵值是**终止信号**,不是数据。用它读 socket / 文件块时,最后那个空块不会混进结果里。

### 3. `groupby`:只切"连续"相同键,且分组与源共享

官方文档明确:`groupby()` 类似 Unix 的 `uniq` —— **每当键变化就开新组**,
因此通常要求输入先按同一 key 排好序;这与 SQL 的 `GROUP BY`(无视顺序聚合)不同。
更隐蔽的是:返回的分组是**与主迭代器共享底层 iterable** 的迭代器,
一旦主迭代器前进,上一个分组就被抽干:

```python
it = groupby("AAAABBB")
k, g = next(it)      # g 是 A 组
next(it)             # 主迭代器前进
list(g)              # [] —— A 组没了
```
所以官方示例一律 `groups.append(list(g))` 立刻固化。

### 4. `tee`:一条链表多个游标 + "展平"

`tee` 不是把数据复制 N 份,而是让 N 个游标共享一条**单向链表**:
谁先读谁负责从上游 `next()`,落后的游标沿着链表照样能取到旧值(代价是旧值一直占内存)。
文档还给了两个易漏的性质:

- **展平(flattening)**:输入已经是 `tee` 迭代器时,新的 `tee` 会复用上游链,
  于是嵌套 `tee` 不会退化成"链中链",并因此得到 `lookahead`(peek)能力。
- **非线程安全**:同一个 `tee()` 的多个分支并发使用可能抛 `RuntimeError`。
- 若一个分支会吃掉绝大部分数据,官方建议直接用 `list()` 而不是 `tee()`。

### 5. `lru_cache` 的键:默认 `maxsize=128`,且构造键的方式很特别

源码 `Lib/functools.py` 的 `_make_key` 有三条会咬人的规则:

1. **默认 `maxsize=128`**(`def lru_cache(maxsize=128, typed=False)`),不是无限;
   想要无界要用 `functools.cache`,它就是 `lru_cache(maxsize=None)`。
2. **`typed=False`** 时类型不参与键 —— 但有个**非对称**后果:
   `fasttypes = {int, str}`,当**只有一个参数且类型命中**时,键被摊平成裸值。
   于是 `f(1)` 的键是 `1`,而 `f(1.0)` 的键是 `(1.0,)` —— 两者**不相等**;
   可 `(True,)` 与 `(1.0,)` 相等,于是 **`f(True)` 会命中 `f(1.0)` 的条目**,
   返回的是 `f(1.0)` 算出的结果。这是本 demo 实测出来的真实行为(Python 3.13)。
3. **关键字实参的顺序参与键**:`f(x=1, y=2)` 与 `f(y=2, x=1)` 是两条独立缓存
   (源码注释写得很清楚:不再 `sort()`,换来速度)。

### 6. `singledispatch`:沿 MRO 找最近实现

`_find_impl()` 用 `_compose_mro(cls, registry.keys())` 合成一条顺序,再挑第一个命中的注册类型。
两个后果:

- 未注册的类型会沿 MRO 上溯(`bool → int`)。
- 若一个类型同时**隐式**命中两个互不相干的 ABC(既不在 `cls.__mro__` 里、
  彼此也没有继承关系),`_find_impl` 会拒绝"猜",抛
  `RuntimeError: Ambiguous dispatch: ...`。

## 三、对比(同类机制在别的语言里长什么样)

| 需求 | Python | 其它语言 |
| --- | --- | --- |
| 惰性管道 | `itertools` 组合子 | Rust `Iterator` adaptor / Java Stream |
| 一次性 vs 可重入 | 迭代器一次性,容器可重入 | Rust 区分 `Iterator` 与 `IntoIterator` |
| 记忆化 | `@lru_cache` 一行 | 多需手写 `HashMap` + 淘汰 |
| 单分派 | `@singledispatch` 运行时按类型 | Java 靠重载(编译期)/ Clojure multimethod |

## 四、环境与运行

- Python 3.8+(本 demo 在 **3.13** 上实跑;`@lru_cache` 无括号写法需 3.8+)。
- 仅用标准库。

文件:`main.py` 是手写实现(迭代器协议 / `my_groupby` / `my_tee` / `my_lru_cache`),
`selfcheck_itertools.py` 是自检,负责把它与官方实现逐条对拍。

```bash
python selfcheck_itertools.py       # 断言实跑,末尾打印"共 N 项断言全部通过"
```

## 五、关键代码

```python
class _Tee:                       # tee 的一个游标
    def __next__(self):
        link = self.link
        if link[1] is None:
            link[0] = next(self.src)      # 谁先读,谁负责推进上游
            link[1] = [None, None]
        value, self.link = link           # 注意:self.link 前进到 link[1]
        return value
```

```python
def my_groupby(iterable, key=None):       # 分组与主迭代器共享底层 iterable
    ...
    yield target, grp
    if state["key"] == target:            # 调用方没消费完就把这一组抽干
        for _ in grp: pass
```

## 六、性能边界

- `tee` 的额外内存 = **最落后游标与最领先游标之间的元素数**,不是数据总量;
  但一个分支跑完全程时,这份额外内存就等于全量 —— 此时 `list()` 更快。
- `groupby` 是流式单遍,内存 O(组大小);但它**要求输入已排序**,排序本身的 O(n log n) 常常才是大头。
- `lru_cache` 的命中路径是 C 实现的字典查找 + 链表搬移,开销约等于一次函数调用的几倍;
  `maxsize=None` 时退化为"不淘汰",长跑进程有内存增长风险。
- `permutations` / `combinations` 是**组合爆炸**的:`permutations(range(10))` 有 360 万个,
  永远不要 `list()` 它,用 `islice` 截断。

## 七、注意事项与常见坑

1. **生成器里 `raise StopIteration` 会变成 `RuntimeError`**(PEP 479)。想结束就裸 `return`。
   这条会让"用 `StopIteration` 提前退出"的老代码在 3.7+ 直接炸。
2. `islice` **不接受负数**的 `start/stop/step`,给负值是 `ValueError`,不是切片语义。
3. `reduce` 不给 `initial` 时遇到空序列抛 `TypeError`。
4. `partial` 对象**没有 `__name__`**,直接塞给需要 `__name__` 的框架会出错(用 `functools.update_wrapper` 不生效,应改用 `wraps` 的普通包装函数)。
5. `cached_property` 把结果写进**实例 `__dict__`**,因此要求实例有 `__dict__`
   (带 `__slots__` 且没留 `__dict__` 的类会失败)。
6. `tee` 的分支并发使用会抛 `RuntimeError`,多线程下请各自 `tee` 或加锁。
7. 手写 `tee` 时,`_Tee` 必须定义在**模块层**:写在工厂函数里的话,每次调用都是一个新的类对象,
   嵌套 `tee` 的 `isinstance(it, _Tee)` 判断会恒为 `False`(本 demo 开发期真实踩到)。

## 八、参考资料(均为本 demo 实读)

- [itertools — 高效循环的迭代器函数](https://docs.python.org/3/library/itertools.html)
  (`groupby` / `tee` / `islice` / `accumulate` / `zip_longest` 各节与官方等价实现)
- [functools — 高阶函数](https://docs.python.org/3/library/functools.html)
- [Functional Programming HOWTO](https://docs.python.org/3/howto/functional.html)
- [PEP 479 — Change StopIteration handling inside generators](https://peps.python.org/pep-0479/)
- CPython 源码:
  [`Lib/functools.py`](https://github.com/python/cpython/blob/main/Lib/functools.py)
  (`_make_key` 的 `fasttypes` 捷径、`lru_cache` 默认 `maxsize=128`、`_find_impl` 的 Ambiguous 分支)、
  [`Modules/itertoolsmodule.c`](https://github.com/python/cpython/blob/main/Modules/itertoolsmodule.c)
  (`teedataobject` 链表)
