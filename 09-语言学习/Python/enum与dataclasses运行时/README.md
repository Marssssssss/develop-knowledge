# enum 与 dataclasses 的运行时行为

> 两个都是"让解释器替你写类"的机制,但**改写时机**完全不同:
> `@dataclass` 是**装饰器**——类已经创建完(含 `__init_subclass__` 已触发)才开始生成方法;
> `Enum` 靠**元类 EnumType**——类创建之中就把成员变成实例。时机差一个身位,钩子里能看见的东西就不同。

## 1. dataclass 的 hash 矩阵

| eq | frozen | `__hash__` | 语义 |
| --- | --- | --- | --- |
| True | False | **`None`(不可哈希!)** | 可变值对象,禁止进 set/dict 键 |
| True | True | 自动生成(按字段元组) | 不可变值对象,`hash(B(1))==hash(B(1))` |
| False | × | 不动(退回 object 身份哈希) | `EqOff(1) != EqOff(1)` |

- `frozen=True` 生成 `__setattr__` 抛 `FrozenInstanceError`;生成的 `__init__` 内部用
  `object.__setattr__` 绕行,所以初始化能赋值、之后不能;
- **可变默认值在装饰时 `ValueError`**(判据是"不可哈希",3.11 起不再枚举 list/dict/set);
  正确写法 `field(default_factory=list)`,每次实例化新建。

## 2. 字段合并与三个补算钩子

- 字段序 = **基类在前**;子类重声明同名字段**占住原位**,只覆盖默认值;
- 子类重声明**不带默认** → **继承基类默认**(实测 3.12,不是丢失默认);
- `field(init=False)`:不进 `__init__` 签名,`__post_init__` 里补算;
- `replace(obj, x=5)`:用新值**重跑** `__init__` + `__post_init__`;
- `asdict()` 递归 **deepcopy**——拿到的嵌套 dict/list 怎么改都不回灌实例。

## 3. InitVar 的三个坑(高频翻车点)

```python
@dataclass
class HasInitVar:
    x: int = 0
    calc: InitVar[int] = 5          # 只进 __init__ 与 __post_init__
    y: int = field(init=False, default=0)
    def __post_init__(self, calc):
        self.y = self.x + calc
```

1. InitVar **不进** `fields()`,也**不进**实例 `__dict__`;
2. 但 `iv.calc` 仍可访问——读到的是**类属性 5**,你传的 `calc=100` 早没了;
3. `__post_init__` 的签名必须收下 InitVar,否则 TypeError。

## 4. `__init_subclass__` 时序:装饰器 vs 元类

- `@dataclass`:钩子触发时 `hasattr(cls, "__dataclass_fields__")` 为 **False**——
  装饰器还没跑,想在钩子里读子类字段是徒劳(基类的可以);
- `Enum`:EnumType 在**类创建之中**完成成员收集与冻结,所以反面规则成立:
  **父枚举已有成员时,定义新成员的子类直接 `TypeError: cannot extend`**
  (空枚举当 mixin 基类则合法)。

## 5. 枚举成员的运行时不变量

- 成员是**单例**:`Color.RED is Color["RED"] is Color(1)` 三条路同归;
- **别名**:`ALIAS = 1`(与 X 同值)→ `ALIAS is X`;迭代只出正主,
  `__members__`(含别名)才看得到全名单;
- `EnumType.__setattr__` 拦截重赋值(`cannot reassign member`),运行期不可篡改;
- pickle 按 `枚举名.成员名` 序列化——枚举改名会破坏旧流,但**值永远不会错绑**;
- `IntEnum` 混入 int:成员间可比较、与裸 int 相等(`Level(20) == 20`)。

## 6. `_missing_` 与宽松 `in`(版本敏感)

- `Color(value)` 找不到值时先问 `_missing_`:返回成员则收下(大小写归一化的惯用法),
  返回 None 才抛 `ValueError`;
- **3.12 实测**:`1 in Color` 为 **True**(按值宽松包含,`"X" in Color` 才是 False);
  新版本对非成员改抛 TypeError——生产代码不要依赖;
- 函数式 API `Enum("N", "A B C", start=10)`:`auto()` 从 start 起步,名字按空白切分。

## 自检

`python main.py` —— 22 项断言:hash 矩阵 / 冻结与可变默认 / 字段合并与重声明继承 /
InitVar 三坑 / `__init_subclass__` 时序对比 / 单例别名不可篡改 / pickle 按名 /
`_missing_` 与宽松 in / start 起值。

## 参考资料(实读)

- [dataclasses — Data Classes](https://docs.python.org/3/library/dataclasses.html)
- [enum — Enumerations](https://docs.python.org/3/library/enum.html)
- [PEP 557 — Data Classes](https://peps.python.org/pep-0557/)
- 本机 CPython 3.12 `Lib/dataclasses.py`、`Lib/enum.py`(EnumType 实现)
