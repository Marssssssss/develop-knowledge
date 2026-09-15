# `__slots__` 与对象内存布局

## 简介

- 默认情况下,类实例把属性放进每实例一个的 `__dict__`——灵活但费内存(本 demo 实测:
  两个属性的普通实例 ≈ **344 B**,其中 dict 占大头)。
- `__slots__` 在类上声明实例变量名 → 解释器为每个名字在**类级别创建描述符**、
  在实例里预留定长槽位,并**阻止 `__dict__`/`__weakref__` 自动创建**(实测同结构只要 **48 B**)。
- 关键概念:
  - **slot 描述符**:slots 在类层级以描述符实现(与 `property` 同族,见描述符 demo)
  - **布局(layout)**:槽位在 C 结构体里的排布;多继承按布局判冲突
  - **穿透**:父类链上任何类没有 `__slots__`,实例的 `__dict__` 就会回来
  - `dataclass(slots=True)`:3.10+ 由装饰器自动生成 slots(返回**新类**)

## 原理详解

### 1. 声明的效果(datamodel 原文语义)

- `__slots__` 可赋值:字符串 / 可迭代对象 / 字符串序列 / **字典**(键=名字,值=docstring)。
- 声明后:实例**不能再有未列出的新变量**(赋值报 `AttributeError`);
  想保留动态属性可把 `'__dict__'` 加进 slots。
- `__slots__` 声明的作用**不限于定义它的类**——父类声明的槽子类也能用;
  但**子类若不自己声明 `__slots__`,实例会重新拿到 `__dict__` 和 `__weakref__`**
  (子类声明时只写**新增**名字,不要重复父类)。

### 2. 类层级描述符 → 默认值的坑

slots 在类级别为每个名字创建描述符。因此**不能用类属性给 slot 设默认值**——
否则类属性会**覆盖描述符**,slot 从此失效(赋值因无 `__dict__` 可存而报错)。
默认值应放 `__init__`(或交给 dataclass)。

### 3. 布局冲突与遮蔽

- 多继承多个带 slots 的父类:**只允许一个父类拥有非空 slot 布局**,其余必须空布局
  (`__slots__ = ()`),否则 `TypeError: multiple bases have instance lay-out conflict`。
- 子类定义与基类**同名** slot → 基类那个实例变量不可访问(只能从基类直接取描述符)。
- 变长内建类型(`int`/`bytes`/`tuple`)派生 + 非空 slots → `TypeError`。
- `__class__` 赋值只在**两个类 slots 相同**时允许。

### 4. dataclass 的 slots 支持(3.10+)

- `@dataclass(slots=True)`:按字段生成 `__slots__`,**返回新类**(而非原类)——
  这是 dataclass 装饰器唯一创建新类的情形;已手写 `__slots__` 则报 `TypeError`。
- 查询字段名应该用 `fields()`,**不要**读 `__slots__`(官方文档明示)。
- `frozen=True` 的实现:生成 `__setattr__`/`__delattr__` 抛 `FrozenInstanceError`,
  生成的 `__init__` 内部走 `object.__setattr__` 绕过冻结(有性能代价)。
- `weakref_slot=True` 恢复弱引用支持(必须搭配 `slots=True`)。

## 对比 / 选型

| 维度 | 普通 `__dict__` | `__slots__` |
| --- | --- | --- |
| 每实例内存(2 属性,3.13 实测) | ≈ 344 B | 48 B |
| 动态新属性 | 支持 | `AttributeError`(除非加 `'__dict__'`) |
| 弱引用 | 支持 | 默认不支持(需 `'__weakref__'`) |
| 多继承 | 自由 | 布局约束(单非空布局) |
| 适用 | 原型/动态场景 | 海量小对象、属性集固定 |

## 环境准备

- OS:任意(纯标准库)
- Python:3.10+(demo 用到 `dataclass(slots=True)`);实测 3.13.14
- 依赖:无

## 运行方式

```bash
python3 main.py
```

## 关键代码片段

```python
class Slotted:
    __slots__ = ("x", "y")            # 无 __dict__;未列出属性赋值报错

WithDefault.__slots__ = ...          # 注意:类属性默认值会覆盖 slot 描述符
WithDefault.x = 5                     # slot 失效;c.x = 6 报 AttributeError

class A: __slots__ = ("a",)
class B: __slots__ = ("b",)
class C(A, B): ...                    # TypeError: multiple bases have
                                      # instance lay-out conflict
```

## 性能与边界

- 省内存的原因:去掉每实例 dict(约 184 B 起)+ 槽位定长内联;本 demo 100 万实例估算
  普通 ≈ 344 MB vs slots ≈ 48 MB(**~7 倍**)。
- 属性查找更快(描述符直查,免 dict 哈希);官方原文:"The space saved over using
  `__dict__` can be significant. Attribute lookup speed can be significantly improved as well."
- 边界:slots 无法与变长内建类型组合;`__class__` 换类型受布局限制;
  与 `dataclass` 组合时装饰器会重建类(引用旧类的代码会失联)。

## 注意事项与常见坑

- **坑:子类忘了声明 `__slots__`** → `__dict__` 静默回归,优化全部失效。
- **坑:多继承两个非空 slots 基类** → 运行时 `TypeError`(布局冲突)。
- **坑:类属性默认值覆盖 slot 描述符** → slot 哑掉,赋值报错;默认值放 `__init__`。
- **坑:给 slots 类建 `weakref.ref`** → `TypeError`,需显式 `'__weakref__'`。
- **坑:迭代器赋 `__slots__`** → 描述符正常创建,但 `__slots__` 本身变成**空迭代器**
  (本 demo 实测 `list(It.__slots__) == []`),后续代码读不到名字列表。
- **坑:slots 不预置默认值**——未赋值的槽位直接读取就是 `AttributeError`。

## 参考资料(实际阅读过的权威来源)

- [The Python Language Reference §3.3.2.4 `__slots__` + Notes on using `__slots__`](https://docs.python.org/3/reference/datamodel.html#slots) —
  本文全部布局/继承/冲突规则的原文(注:页面超长,WebFetch 截断后改以 curl 下载 HTML
  精读该节)。
- [dataclasses — Data Classes(官方库文档)](https://docs.python.org/3/library/dataclasses.html) —
  `slots=True` 返回新类、`frozen` 的 `object.__setattr__` 绕过、`fields()` 惯例。
- [PEP 557 – Data Classes](https://peps.python.org/pep-0557/) — dataclass 设计动机(关联阅读)。
