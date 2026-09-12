# Python · 元类(metaclass)与 `__init_subclass__`

> 元类是"类的类" —— Python 中**类本身也是对象**,而创建它的"工厂"就是元类。
> 默认元类是 `type`。`__init_subclass__`(PEP 487)是更轻量的替代,覆盖 80% 场景。

## 一、简介

**核心三件套**:1) `type` —— 所有类的默认元类;`type(name, bases, ns)` 是动态创建类的工厂;
2) `metaclass=` 关键字 —— 在类定义中声明使用哪个元类;
3) `__init_subclass__` —— 基类钩子,所有子类创建时自动调用。

历史:PEP 3115(Python 3.0)引入 `metaclass=`;PEP 487(Python 3.6)引入 `__init_subclass__` 与 `__set_name__`。

## 二、原理详解

### 1. `type` 的双重身份

```python
type(obj)              # 单参数 → 返回对象的类型
type(name, bases, ns)  # 三参数 → 动态创建类
```

后者等价于 `class name(bases): ...` 类体代码就是 ns 字典里的内容。`type` 本身继承 `object`,**它的类型**是 `type`(自指)。

### 2. 类创建的三步流程

```text
1. metaclass.__prepare__(name, bases, **kwds) → 返回"类字典"容器(默认 dict)
2. 执行类体语句,结果写入步骤 1 返回的命名空间
3. metaclass(name, bases, namespace) → 实际触发 __new__ → __init__
```

### 3. `metaclass=` 关键字与查找顺序

```python
class Foo(base1, base2, metaclass=mymeta, private=True):
    pass
```

- `metaclass=` 是 PEP 3115 引入的关键字参数,**取代** Python 2 的 `__metaclass__` 类属性
- 支持任意 keyword(类似 `**kwds`);查找顺序:子类显式 `metaclass=` > 基类的元类 > `type`
- **`metaclass=` 不可继承** —— 子类必须重新声明(避免隐性冲突)

### 4. 元类的三个钩子签名

```python
class MyMeta(type):
    def __prepare__(cls, name, bases, **kwds):
        """返回类体的命名空间;默认是 dict"""
        return {}

    def __new__(mcs, name, bases, namespace, **kwds):
        """创建类对象;可修改 namespace;返回 cls"""
        return super().__new__(mcs, name, bases, namespace, **kwds)

    def __init__(cls, name, bases, namespace, **kwds):
        """类对象创建后的初始化;默认是空操作"""
        super().__init__(name, bases, namespace, **kwds)
```

### 5. `__init_subclass__`(PEP 487)

```python
class Base:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)          # 合作式多继承
        # 在这里做任何"子类创建后"操作
```

- 隐式 `@classmethod`(无须显式装饰);由 `type.__new__` 在**所有**子类创建时自动调用
- 接受的 `**kwargs` 来自 `class Sub(Base, key=value):`

### 6. `__set_name__`(PEP 487) —— 描述符自动获知属性名

```python
class Field:
    def __set_name__(self, owner, name):
        self.public_name = name                      # 类创建时 type.__new__ 自动调

class Person:
    name = Field()                                   # __set_name__(Person, 'name') 自动触发
```

解决痛点:描述符以前要硬编码 `'_name'`,现在自动获得属性名。

### 7. 经典示例 —— OrderedClass(PEP 3115 原文)

```python
class member_table(dict):
    def __init__(self): super().__init__(); self.member_names = []
    def __setitem__(self, k, v):
        if k not in self: self.member_names.append(k)
        super().__setitem__(k, v)

class OrderedClass(type):
    @classmethod
    def __prepare__(mcs, name, bases):
        return member_table()
    def __new__(mcs, name, bases, classdict):
        result = type.__new__(mcs, name, bases, dict(classdict))
        result.member_names = classdict.member_names
        return result

class MyClass(metaclass=OrderedClass):
    z = 1              # 1st
    def m1(self): pass # 2nd
    y = 2              # 3rd
# MyClass.member_names == ['z', 'm1', 'y']
```

**保留声明顺序** —— ORM / dataclasses / attrs 都依赖这个机制。

## 三、对比:元类 vs `__init_subclass__` vs 类装饰器

| 维度 | 元类 | `__init_subclass__` | 类装饰器 |
|------|------|---------------------|----------|
| 触发时机 | 类对象**创建时** | 子类创建时 | 类对象**创建后** |
| 可修改 namespace | ✅(`__new__`) | ❌(已创建) | ❌(已创建) |
| 参与类型系统 | ✅(影响 `type(C)`) | ❌ | ❌ |
| 跨库冲突风险 | ⚠️ 元类冲突 | ✅ 不会 | ✅ 不会 |
| 多重叠加 | 难(需合并元类) | 易(继承链叠加) | 易(`@A @B class C`) |
| 适用场景 | `__prepare__` 改命名空间 | 子类注册 / 参数注入 | 单类装饰 |

**官方推荐**:PEP 487 直接说"绝大多数元类使用场景都可用 `__init_subclass__` 替代"——PEP 487 让元类变成"只在真正需要修改类创建早期阶段时才用"的工具。

## 四、环境与运行

```bash
cd 09-语言学习/Python/元类
python3 metaclass.py
```

预期输出(关键行):

```
=== 第 1 节:type() 三参数动态创建类 ===
  type(42)             = <class 'int'>
  DynamicClass.__name__ = 'DynamicClass'

=== 第 2 节:metaclass= 关键字 + 类创建钩子 ===
  [TracingMeta.__prepare__]  name='Traced', bases=[]
  [TracingMeta.__new__]     name='Traced', namespace keys=['__module__', ...]
  [TracingMeta.__init__]    name='Traced', __mro__=['Traced', 'object']
  Traced._created_by       = 'TracingMeta'

=== 第 3 节:OrderedClass 保留属性顺序 ===
  MyClass.member_names = ['z', 'method_first', 'y', 'method_second', 'x']
```

## 五、关键代码片段

```python
# 插件注册系统(PEP 487 风格,无元类)
class PluginBase:
    plugins: list = []
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.plugins.append(cls)

class CSVExporter(PluginBase): pass
class JSONExporter(PluginBase): pass
# PluginBase.plugins == [CSVExporter, JSONExporter]
```

```python
# 保留声明顺序(必须用元类,__init_subclass__ 做不到)
class OrderedClass(type):
    @classmethod
    def __prepare__(mcs, name, bases):
        return member_table()    # 自定义 dict 保留插入顺序
```

## 六、性能与边界

- **元类钩子**:每次类创建调用 3 次(`__prepare__` / `__new__` / `__init__`),总开销几 µs
- **`__init_subclass__`**:每次子类创建调用 1 次,比元类稍快
- **`type()` 三参数动态创建类**:几 µs,适合动态类工厂
- **`__prepare__` 返回 OrderedDict**:插入 O(1),迭代 O(N)
- **元类 + ABCMeta**:常见组合,抽象方法检查在子类 `__init__` 触发

## 七、注意事项与常见坑

1. **`metaclass=` 关键字无法继承** —— 子类必须重新声明
2. **元类冲突**:`class A(MetaA)` + `class B(MetaB)` + `class C(A, B)` 因 metaclass 不同 `TypeError`
3. **`__init_subclass__` 必须 `super().__init_subclass__(**kwargs)`** —— 否则合作式多继承的基类不会被初始化
4. **`__init_subclass__` 默认无关键字参数** —— 子类更特化的版本必须处理所有 kwarg
5. **`__set_name__` 只在类创建时触发一次** —— 之后动态添加的描述符需手动调用
6. **`type.__subclasses__()` 是 weakref 列表** —— 只保留强引用所指的子类
7. **元类 `__init__` 抛异常** 会替换原"类创建"过程,调试易混淆
8. **Cython / mypyc 对元类支持不完美** —— 生产代码优先用 `__init_subclass__`

## 八、参考资料

- [Python Reference — Class definitions](https://docs.python.org/3/reference/compound_stmts.html#class-definitions)(`metaclass=` 关键字)
- [Python Data Model — Metaclasses](https://docs.python.org/3/reference/datamodel.html#metaclasses)
- [Python Data Model — `__init_subclass__`](https://docs.python.org/3/reference/datamodel.html#object.__init_subclass__)
- [Python Data Model — Customizing class creation](https://docs.python.org/3/reference/datamodel.html#customizing-class-creation)
- [PEP 3115 — Metaclasses in Python 3000](https://peps.python.org/pep-3115/)
- [PEP 487 — Simpler customisation of class creation](https://peps.python.org/pep-0487/)(`__init_subclass__` + `__set_name__`)
## 九、相关 demo
上一个:[`../协程与asyncio/`](../协程与asyncio/);同目录:`装饰器/`、`生成器/`、`上下文管理器/`、`描述符/`