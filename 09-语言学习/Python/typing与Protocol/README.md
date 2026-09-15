# typing 类型系统:`Protocol` 结构化子类型与 PEP 695 类型参数语法

## 简介

- Python 的类型提示本质是**注解**:默认不施加任何运行时语义,检查交给第三方检查器
  (mypy/pyright)。但有两块在运行时**真实存在**:`typing.Protocol` 的鸭子类型检查,
  以及 PEP 695 落地后 `type` 语句 / 泛型语法生成的真实对象。
- 关键概念:
  - **名义子类型**:必须显式继承(ABC 模式),关系写在 MRO 里
  - **结构化子类型**:结构兼容即子类型(PEP 544,Protocol = 静态鸭子类型)
  - **data / non-data protocol**:含非方法成员(`x: int`)的叫 data protocol
  - **`type` 语句**(PEP 695,3.12):软关键字,运行时生成 `TypeAliasType` 实例
  - **类型参数作用域**:`class C[T]` / `def f[T]` 引入的新型"覆盖层"词法作用域

## 原理详解

### 1. Protocol:结构化子类型判定

子类型三原则(PEP 544 原文):

1. 具体 `X` ⊑ protocol `P` ⟺ `X` 以兼容类型实现 `P` 的全部成员
2. protocol `P1` ⊑ `P2` ⟺ `P1` 以兼容类型定义 `P2` 的全部成员
3. 泛型 protocol 遵循常规方差规则(方法返回值协变、参数逆变、**可变属性强制不变**)

可变属性强制不变的安全论证(原文示例):`P.x: float` 的 protocol 若允许协变接受
`C.x: int` 的类,`f(arg: P)` 里 `arg.x = 0.42` 会把 `int` 属性写坏。

### 2. `@runtime_checkable` 的边界

- 默认 `isinstance`/`issubclass` 对 protocol 直接 `TypeError`(protocol 主要面向静态检查)。
- 装饰后 `isinstance(x, P)` ≈ `hasattr(x, 成员...)`——**只查存在性,不查签名**
  (本 demo 实测:签名完全错误的 `__len__(self, extra)` 仍通过 `isinstance`)。
- `issubclass` 仅对 **non-data protocol**(只有方法)有效;data protocol 报
  `Protocols with non-method members don't support issubclass()`(3.13 实测报错文本)。
- 下标化的泛型 protocol 的运行时检查恒 `TypeError`(运行时给不出可靠答案)。

### 3. 默认实现:显式继承才可用

- protocol 方法体 = 默认实现,但**只有显式继承**(protocol 出现在 MRO 中)才能使用;
  隐式结构子类型没有基类,`super()` 调不到默认实现。
- 继承 protocol **不会自动成为 protocol**——基类列表必须再显式写 `Protocol`。
- 基类列表含 `Protocol` 时,其余基类必须全是 protocol;不能继承普通类(保传递性)。

### 4. PEP 695(3.12)三段新语法

| 语法 | 运行时产物 | 替代的旧写法 |
| --- | --- | --- |
| `type X = int \| str` | `TypeAliasType`(`__name__`/`__value__`/`__type_params__`) | `X: TypeAlias = ...`(PEP 613,已废弃) |
| `class Foo[T: bound]` | 类型参数入 `Foo.__type_params__`,`Generic` 隐式进 MRO/`__orig_bases__` | `Generic[T]` 显式基类 |
| `def f[T](x: T) -> T` | `f.__type_params__` | 模块级 `T = TypeVar("T")` |

- **惰性求值**:TypeVar 的上界/约束、别名值保存为代码对象,属性首次访问才求值并缓存
  → 递归别名 `type Recursive[T] = T | list[Recursive[T]]` 免引号。
- **自动命名与方差推断**:`T` 只写一遍;新式参数 `__infer_variance__` 恒 `True`,
  旧式 `TypeVar` 默认 `False`(可传 `infer_variance=True` 体验同一行为)。
- **作用域(覆盖层语义)**:类型参数对类体/基类列表/注解可见,对**参数默认值和装饰器不可见**
  (默认值求值时触发 `NameError`,本 demo 实测);参数符号**不出现**在 `globals()`/类命名空间。
- **新旧不能混用在同一声明**(类型检查器报错);`typing.TypeAlias` 正式废弃。

## 对比 / 选型

| 维度 | ABC(名义) | Protocol(结构) |
| --- | --- | --- |
| 判定依据 | 显式继承 / `register()` | 结构兼容(无需改第三方类) |
| 运行时检查 | 原生支持 | 需 `@runtime_checkable` 且只查存在性 |
| 默认实现 | 继承即得 | 显式继承才得,隐式无 |
| 典型用途 | 框架内部契约 | 对外开放的"能力"接口(Iterable 等) |

## 环境准备

- OS:任意(纯标准库)
- Python:**3.12+**(PEP 695 语法);本 demo 实测 3.13.14
- 依赖:无(静态检查建议另装 mypy/pyright,不在本 demo 范围)

## 运行方式

```bash
python3 main.py
```

## 关键代码片段

```python
@runtime_checkable
class Sized2(Protocol):
    def __len__(self) -> int: ...

class WrongSignature:                # 签名错误但方法存在
    def __len__(self, extra): return 1

assert isinstance(WrongSignature(), Sized2)   # 通过!只查存在性

type Recursive[T] = T | list[Recursive[T]]    # 惰性求值 → 递归免引号

class Pair[T]:                               # Generic 隐式入 MRO
    def __init__(self, a: T, b: T): self.a, self.b = a, b
```

## 性能与边界

- `isinstance(x, Protocol)` 退化为 `hasattr` 链:成员越多越慢,且对下标化泛型不可用。
- PEP 695 惰性求值的副作用:属性值可能取决于**访问时机**(原文示例 `X` 改值前后
  读 `__bound__` 结果不同)——避免在运行期依赖类型参数的求值结果。
- 方差推断由类型检查器执行,运行时不参与;勿在运行期模仿。

## 注意事项与常见坑

- **坑:以为 `runtime_checkable` 会校验签名** —— 不会;静态检查器也无法仅凭运行时检查保证。
- **坑:隐式结构子类型里 `super().default_impl()`** —— 隐式没有基类,PEP 544 明确报错。
- **坑:子类化 protocol 想继续当 protocol** —— 基类列表必须再写 `Protocol`,否则降级为普通 ABC。
- **坑:同一声明混用新旧语法**(`class A[V](dict[K, V])`,`K` 是旧式 TypeVar)——检查器报错。
- **坑:`def f[T](a=list[T])`** —— 默认值不在覆盖层作用域,运行期 `NameError`(本 demo 实测)。

## 参考资料(实际阅读过的权威来源)

- [PEP 544 – Protocols: Structural subtyping (static duck typing)](https://peps.python.org/pep-0544/) —
  结构子类型三原则、`runtime_checkable` 限制、可变属性不变性论证、默认实现语义。
- [PEP 695 – Type Parameter Syntax](https://peps.python.org/pep-0695/) —
  `type` 语句 / 类与函数类型参数、惰性求值、覆盖层作用域、方差推断算法全文。
