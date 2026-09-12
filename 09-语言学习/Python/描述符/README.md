# Python · 描述符协议(Descriptor Protocol)

> Python 属性访问(`obj.attr`)的**底层心脏**:所有看似"魔法"的特性
> (`@property` / 绑定方法 / `@staticmethod` / `@classmethod` / `__slots__` /
> ORM 字段代理)都建立在三个魔术方法之上 —— `__get__` / `__set__` / `__delete__`。

## 一、简介

**协议三件套**:任何对象只要定义了 `__get__` / `__set__` / `__delete__` 之一,
就成为**描述符**,可介入"属性访问"的查找流程,覆盖默认行为。

核心分类:

| 类型 | 定义 | 优先级 |
|------|------|--------|
| **数据描述符** (data descriptor) | 同时定义了 `__set__` 或 `__delete__` | **最高**(高于实例字典) |
| **非数据描述符** (non-data descriptor) | 只定义 `__get__` | 低于实例字典 |

历史:`property` 是 Python 2.2 就有的内置;**描述符指南**(Descriptor HowTo)是
"官方 HowTo",由导论性 `__get__`/`__set__` 到 ORM 实战逐层展开;`__set_name__` 在
PEP 487(Python 3.6)引入,解决描述符无法自动获知属性名的痛点。

## 二、原理详解

### 1. 属性查找的优先级链(`object_getattribute` 的等价实现)

```python
def object_getattribute(obj, name):
    null = object()
    objtype = type(obj)
    cls_var = find_name_in_mro(objtype, name, null)
    descr_get = getattr(type(cls_var), '__get__', null)
    if descr_get is not null:
        if (hasattr(type(cls_var), '__set__')
            or hasattr(type(cls_var), '__delete__')):
            return descr_get(cls_var, obj, objtype)       # 1. 数据描述符
    if hasattr(obj, '__dict__') and name in vars(obj):
        return vars(obj)[name]                             # 2. 实例字典
    if descr_get is not null:
        return descr_get(cls_var, obj, objtype)           # 3. 非数据描述符
    if cls_var is not null:
        return cls_var                                      # 4. 普通类变量
    raise AttributeError(name)                             # 5. __getattr__ 兜底
```

**优先级总结**(从高到低):

1. **数据描述符**(`__get__` + `__set__`/`__delete__`)
2. **实例字典**(`vars(obj)`)
3. **非数据描述符**(只有 `__get__`)
4. **普通类变量**
5. `__getattr__()` 兜底

### 2. `property` 是数据描述符

`property` 同时定义 `__get__` + `__set__`,所以:
- **实例字典无法覆盖 `@property`** —— 这就是为什么不能写 `obj.x = 5` 然后直接 `obj.__dict__['x']`
- 想要只读数据描述符:同时定义 `__get__` 和 `__set__`,让 `__set__` 抛 `AttributeError`

### 3. 函数是非数据描述符 —— 绑定方法的来源

```python
class Function:
    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return MethodType(self, obj)        # 返回绑定方法
```

| 访问形式 | 返回 |
|----------|------|
| `D.__dict__['f']` | 原始函数 |
| `D.f` | 原始函数(`obj is None`) |
| `d.f`(实例) | **绑定方法** |

正因函数是非数据描述符,**实例字典可以覆盖类方法**(`obj.method = lambda: ...` 会隐藏类方法)。

### 4. `@staticmethod` 与 `@classmethod` 的描述符差异

```python
class StaticMethod:
    def __init__(self, f): self.f = f
    def __get__(self, obj, objtype=None):
        return self.f                       # 不绑定

class ClassMethod:
    def __init__(self, f): self.f = f
    def __get__(self, obj, cls=None):
        if cls is None: cls = type(obj)
        return MethodType(self.f, cls)      # 绑定 cls
```

| 装饰器 | `obj.f(...)` 等价于 | `C.f(...)` 等价于 |
|--------|---------------------|-------------------|
| (无) | `f(obj, ...)` | `f(...)` |
| `@staticmethod` | `f(...)` | `f(...)` |
| `@classmethod` | `f(type(obj), ...)` | `f(C, ...)` |

### 5. `__set_name__`(PEP 487)

类创建时 `type.__new__` 扫描类字典,若发现某属性是描述符且定义了 `__set_name__`,
就**自动**用 `(owner, name)` 调用它 —— 描述符无需硬编码属性名。

> 限制:**只在类创建时触发一次**;之后动态 `__set_name__()` 需要手动调用。

### 6. 典型应用

- **动态计算属性**(`DirectorySize`)—— 每次访问重新计算
- **Validator** —— 赋值时类型/范围校验(本文档示例)
- **ORM 字段代理**(`Field`)—— `instance.attr` 透明代理到数据库
- **`__slots__`** —— 用描述符替换实例字典,节省内存 + 防拼写错误

## 三、对比:为什么 Python 的描述符独此一家

| 维度 | Python 描述符 | C# Property | Java Annotation + Reflection | Rust Deref/Trait |
|------|--------------|-------------|-------------------------------|-------------------|
| 自定义查找 | ✅ `__get__` | 部分(get-only) | 需反射 / 字节码增强 | 编译期 |
| 区分数据/非数据 | ✅ | ❌ | ❌ | ❌ |
| 函数也是描述符 | ✅ | ❌ | ❌ | ❌ |
| 自动获知属性名 | ✅ PEP 487 | ❌ | ❌ | ❌ |

## 四、环境与运行

```bash
cd 09-语言学习/Python/描述符
python3 descriptor.py
```

预期输出(关键行):

```
=== 第 1 节:数据 vs 非数据描述符的优先级 ===
  w.non_data         = '<NonDataDescriptor on alpha>'
  设了实例字典后:w.non_data = '实例字典里的同名属性'
  → 非数据描述符被实例字典『覆盖』了
  w.data = 'DATA-value' → w.data = 'DATA-value'
  设了实例字典后:w.data = 'DATA-value'
  → 数据描述符没被覆盖

=== 第 6 节:Validator 描述符 ===
  Order('shipped', 5)   → OK
  Order('invalid-status', 5) → ValueError: Expected 'invalid-status' to be one of {...}
```

## 五、关键代码片段

```python
class Validator(abc.ABC):
    def __set_name__(self, owner, name):     # PEP 487 —— 自动获知属性名
        self.private_name = '_' + name

    def __get__(self, obj, objtype=None):
        return getattr(obj, self.private_name)

    def __set__(self, obj, value):            # 数据描述符:赋值被拦截
        self.validate(value)                  # 校验失败抛异常,不会写入实例
        setattr(obj, self.private_name, value)

    @abc.abstractmethod
    def validate(self, value): ...

class OneOf(Validator):
    def __init__(self, *options): self.options = set(options)
    def validate(self, value):
        if value not in self.options:
            raise ValueError(f"Expected {value!r} to be one of {self.options!r}")

class Order:
    status = OneOf('pending', 'shipped', 'delivered')  # 描述符 → __set_name__ 自动绑定
```

## 六、性能与边界

- **每次属性访问都要查 MRO + `__get__`** —— 数据描述符比直接 `__dict__` 访问慢约 5-10 倍
- **`__slots__` 通过描述符替换 `__dict__`** —— 节省 60%+ 内存(实例数 ≫ 10⁵ 时差距明显)
- **数据描述符不能被 `vars(obj)` 覆盖** —— 反序列化时需用 `__dict__[name] = value` 直接绕过
- **`__set_name__` 只在类创建时触发** —— 后续动态加的描述符需手动调用
- **Cython 优化**:频繁访问的描述符可绑定 `descriptor.__get__` 到局部变量

## 七、注意事项与常见坑

1. **数据描述符永远赢** —— 实例字典覆盖非数据描述符,但**不能**覆盖数据描述符
2. **`@property` 装饰顺序** —— 装饰器返回新对象;`@x.setter` 实际是给已有 property 加 fset
3. **方法不要作为默认参数存** —— `obj.method = self.method` 会丢掉 `self` 绑定
4. **`__slots__` 与描述符冲突** —— `__slots__` 实际就是描述符,与显式数据描述符不能同名
5. **`__set_name__` 只在类创建时触发** —— 之后加的描述符需手动 `desc.__set_name__(cls, name)`
6. **不要在描述符里 mutate `obj.__dict__`** —— 可能破坏其他描述符的预期

## 八、参考资料

- [Python Descriptor HowTo Guide(官方)](https://docs.python.org/3/howto/descriptor.html)—— 必读,含 property/function/staticmethod/classmethod 的纯 Python 等价实现
- [Python Data Model — Implementing Descriptors](https://docs.python.org/3/reference/datamodel.html#implementing-descriptors)
- [PEP 487 — Simpler customisation of class creation](https://peps.python.org/pep-0487/)(`__set_name__`)
- [Python Data Model — slots](https://docs.python.org/3/reference/datamodel.html#slots)
- [Raymond Hettinger — Python's Descriptor Protocol(EuroPython 2014)](https://www.youtube.com/watch?v=YASyZrZ7VhE)(演讲录像)
- [David Beazley — Python 3 Metaprogramming](https://www.dabeaz.com/generators/)(高级课程)

## 九、相关 demo

- 上一个:[`../上下文管理器/`](../上下文管理器/) —— RAII 资源管理
- 下一个:[`../协程与asyncio/`](../协程与asyncio/) —— 异步协程与事件循环