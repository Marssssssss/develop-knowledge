# Python · 参数绑定与调用约定

> `def f(a, b, /, c, d=4, *args, e, **kw)` 这一行里藏着三种参数语义。
> 本 demo 把**写法**(PEP 570 的 `/`、PEP 3102 的 `*`)、**模型**(`inspect.Parameter` 的五种 kind)、
> **算法**(实参如何填进形参)串起来,并手写一遍绑定算法与 `inspect` 对拍。

## 一、简介

| 记号 | 含义 | 引入 |
| --- | --- | --- |
| `def f(a, /)` | `a` 是 **positional-only**,不能按名字传 | PEP 570(3.8) |
| `def f(a)` | positional-or-keyword | 一直有 |
| `def f(*args)` | 收集剩余位置实参 | 一直有 |
| `def f(*, a)` / `def f(*args, a)` | `a` 是 **keyword-only** | PEP 3102(3.0) |
| `def f(**kw)` | 收集剩余关键字实参 | 一直有 |

## 二、原理详解

### 1. 五种 kind 与真实的内建签名

`inspect.Parameter.kind` 有五种:`POSITIONAL_ONLY` / `POSITIONAL_OR_KEYWORD` /
`VAR_POSITIONAL` / `KEYWORD_ONLY` / `VAR_KEYWORD`。实读(Python 3.13):

```python
len           (obj, /)
divmod        (x, y, /)
sorted        (iterable, /, *, key=None, reverse=False)
map           (function, iterable, /, *iterables)      # / 之后还能接 *args
str.replace   (self, old, new, /, count=-1)
print         (*args, sep=' ', end='\n', file=None, flush=False)   # PEP 3102 的产物
```

`map` 那一行最能说明问题:`/` 只标记**它左边**的参数,不表示"到此为止"。

### 2. PEP 570:`/` 的语法约束

规范列出的**合法**形式:

```python
def f(p1, p2, /, p_or_kw, *, kw)
def f(p1, p2=None, /, p_or_kw=None, *, kw)
def f(p1, p2=None, /, *, kw)          #  / 左边有默认,右边只剩 keyword-only
def f(p1, p2=None, /)
def f(p1, p2, /)
```

**非法**形式(本 demo 逐条 `compile()` 验证为 `SyntaxError`):

```python
def f(p1, p2=None, /, p_or_kw, *, kw)      # 有默认之后不能再出现无默认的
def f(p1=None, p2, /, p_or_kw=None, *, kw)
def f(p1=None, p2, /)
```

### 3. PEP 570 的语义角落(最有价值的一条)

```python
def plain(name, **kwds):      return "name" in kwds
def posonly(name, /, **kwds): return "name" in kwds

plain(1, **{"name": 2})    # TypeError: got multiple values for argument 'name'
posonly(1, **{"name": 2})  # True
```

`plain` 里 **`'name' in kwds` 永远不可能为 `True`** —— 关键字 `name` 会被形参吃掉。
加上 `/` 之后,`name` 这个名字就"腾出来"了,调用方可以安全地把 `name` 当普通字典键传进来。
规范明说这个好处属于 `dict()` 与 `dict.update()` —— 否则 `dict(a=1)` 之外永远没法构造键为 `"self"` 的字典。

### 4. PEP 3102:keyword-only 的动机

在没有 keyword-only 的年代,要写"接受任意个位置参数 + 若干选项"只能 `def f(*args, **kw)` 再手工从
`kw` 里抠。PEP 3102 允许:

```python
def sortwords(*wordlist, case_sensitive=False): ...   # *args 之后
def compare(a, b, *, key=None): ...                   # 裸 *
```

且**无默认值的 keyword-only 是必填的**:只能按名字给,所以必须由调用方显式提供。

### 5. 绑定算法(手写版与 `inspect` 对拍)

顺序是这样的:

1. 位置实参按形参顺序填 `POSITIONAL_ONLY` 与 `POSITIONAL_OR_KEYWORD`,遇到 `KEYWORD_ONLY` 就停止;
   填不完且没有 `*args` → `TypeError: too many positional arguments`。
2. 剩余位置实参进 `VAR_POSITIONAL`(**为空时不进结果**)。
3. 关键字实参按名字匹配:
   - 名字不在形参表里 → 进 `VAR_KEYWORD`,没有则 `TypeError: unexpected keyword argument`;
   - 名字是 `POSITIONAL_ONLY` → **不**匹配形参,而是落进 `**kw`(没有 `**kw` 才报错);
   - 名字已被位置实参占掉 → `TypeError: multiple values`。
4. 还有没填到的必填形参 → `TypeError: missing`(这一步 `bind_partial` 会跳过)。

本 demo 的 `my_bind()` 与 `inspect.Signature.bind()` 在 5 组正常用例 + 3 组错误用例上逐组比对,完全一致。

### 6. `BoundArguments` 的两个细节

- `bind()` 的结果**不含默认值**,要 `apply_defaults()` 才补齐。
- `Signature` 与 `Parameter` 都**不可变**,要用 `Signature.replace()` / `Parameter.replace()` 造新对象。

## 三、对比

| 语言 | positional-only | keyword / 命名参数 |
| --- | --- | --- |
| Python | `def f(a, /)` | `def f(*, a)` |
| C++ / Java | 全部按位置(无命名实参) | 无 |
| Kotlin / Swift | 无 positional-only | 命名参数 + 默认值,顺序可打乱 |
| Ruby 2.x | 无 | 关键字参数(3.0 起与 hash 分离) |

## 四、环境与运行

- Python **3.8+**(`/` 语法依赖 PEP 570);本 demo 在 **3.13** 实跑,仅用标准库。

```bash
python main.py
```

## 五、关键代码

```python
def my_bind(params, args, kwargs, partial=False):
    ...
    for name, kind, _ in slots:
        if not pos or kind == "KEYWORD_ONLY":
            break
        bound[name] = pos.pop(0)
    ...
    for k, v in kwargs.items():
        if k not in by_name:
            if var_kw is None: raise TypeError(f"unexpected keyword argument {k!r}")
            rest[k] = v; continue
        if by_name[k] == "POSITIONAL_ONLY":     # 名字被"让"给了 **kw
            if var_kw is None: raise TypeError(f"{k!r} is positional-only")
            rest[k] = v
        elif k in bound:       raise TypeError(f"multiple values for {k!r}")
        else:                  bound[k] = v
```

```python
def posonly(name, /, **kwds): return "name" in kwds   # PEP 570 的语义角落
```

## 六、性能边界

- PEP 570 的动机之一就是**性能**:全是 positional-only 的 C 函数可以走 `METH_FASTCALL`,
  省掉处理空关键字字典的开销;纯 Python 函数同样能省掉建 frame 时的部分工作。
- `inspect.signature()` 需要解析 `__text_signature__` 或源码,**比直接调用慢几个数量级**,
  别放在热路径上;要重复用就先缓存 `Signature` 对象。
- 手写绑定只是教学模型;真实调用走 `vectorcall` 协议,不会构造中间的 `BoundArguments`。

## 七、注意事项与常见坑

1. **`pow()` 的文档里画了 `/`,但 `inspect.signature(pow)` 是 `(base, exp, mod=None)`**
   —— 文档里的 `/` 只是**约定**,真正生效要看签名对象。
2. 给 positional-only 参数按名字传会报
   `TypeError: f() got some positional-only arguments passed as keyword arguments`。
3. `def f(a, **kw)` 里 `a` 这个名字被形参"占住",调用方传 `a=` 只能喂给形参,**进不了 `kw`**;
   想让调用方能传任意键(如 `dict.update`),就得用 `/`。
4. 无默认值的 keyword-only 参数是**必填**的,漏给会报 `missing a required keyword-only argument`(不同版本措辞略有差异)。
5. `Signature.bind()` 比 `bind_partial()` 严格:前者要求**所有**必填参数都到位。
6. 空的 `*args` / `**kw` 不会出现在 `BoundArguments.arguments` 里(本 demo 开发期第一版就在这里与 `inspect` 对不上)。
7. 子类重写方法时随意改参数名会破坏按关键字调用;这正是库作者偏爱 positional-only 的原因之一
   (PEP 570 里"designing for subclassing"一节)。

## 八、参考资料(均为本 demo 实读)

- [PEP 570 — Python Positional-Only Parameters](https://peps.python.org/pep-0570/)
  (语法与语义、合法/非法形式清单、`name` + `**kwds` 的语义角落、性能优化动机)
- [PEP 3102 — Keyword-Only Arguments](https://peps.python.org/pep-3102/)
  (`*args` 之后的参数、裸 `*`,以及"keyword-only 可以没有默认值即必填")
- [inspect — 检查活跃对象](https://docs.python.org/3/library/inspect.html)
  (`Parameter.kind`、`Signature.bind` / `bind_partial`、`BoundArguments.apply_defaults`、不可变性)
