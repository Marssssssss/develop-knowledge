# 属性查找全链路:数据描述符 > 实例 dict > 非数据描述符 > __getattr__

> `obj.x` 一行代码背后是 CPython `PyObject_GenericGetAttr` 的四级流水线。
> 把它翻成 30 行纯 Python(`main.py` 里的 `generic_getattr`/`py_getattr`),
> 与真实访问对拍,就能精确回答"property 为什么永远赢""方法为什么能被实例属性遮蔽"这类问题。

## 1. 读属性:四级优先级(CPython 语义复刻)

```text
obj.x 的查找顺序:
1. type(obj) 沿 MRO 找 x → 是数据描述符(有 __set__/__delete__)?→ 调 __get__,结束
2. obj.__dict__ 里有 x?→ 返回实例值
3. 第 1 步找到的是非数据描述符(只有 __get__)?→ 调 __get__
4. 返回第 1 步找到的裸类属性;都没找到 → AttributeError → __getattr__ 兜底
```

| 事实 | 原因 |
| --- | --- |
| property 永远压过实例 `__dict__` | property 带 `__set__`,是**数据**描述符(第 1 级) |
| 实例属性能遮蔽方法 | 函数只有 `__get__`,是**非数据**描述符,输给第 2 级 |
| 遮蔽的方法调用时报 `'str' is not callable` | 遮蔽发生在查找层,调用层才发现取到的是字符串 |
| slot 空槽读值抛 AttributeError | `member_descriptor.__get__` 对未赋值槽抛异常——**空槽 ≠ 没有这个属性** |

## 2. `__get__` 的三种绑定(obj 参数决定)

| 声明 | 经实例访问 `a.m` | 经类访问 `A.m` |
| --- | --- | --- |
| 普通函数 | `__get__(a, A)` → bound method(`__self__ is a`) | `__get__(None, A)` → 裸函数(没有 `__func__` 可剥) |
| `@staticmethod` | 原样返回函数 | 同左 |
| `@classmethod` | `__get__` 绑定**类** | 同左 |

## 3. `__getattr__` vs `__getattribute__`

- `__getattribute__`:**每次**属性访问都过(universal 拦截器,性能敏感);
- `__getattr__`:只在**查找失败**(AttributeError)时兜底——包括 `__getattribute__` 内部主动抛的;
- 都定义时:先 `__getattribute__`,它抛了才轮到 `__getattr__`。

## 4. 写与删:同构路由

`obj.x = v`:`type(obj)` MRO 里有数据描述符 → 调它的 `__set__`(property 的 setter、
描述符的拦截点);否则直接写 `obj.__dict__`。`del obj.x` 同理走 `__delete__`。

连基础设施自己都在这套规则里:

- `obj.__dict__` / `obj.__weakref__` 本身是 **getset_descriptor**;
- `__slots__` 生成的 **member_descriptor 带 `__set__`** → 数据描述符 → 这就是 slots
  "挡住"同名实例属性、省掉每实例 dict 的机制根源。

## 5. 特殊方法通道:解释器不看实例

`len(obj)` / `obj[i]` / `with obj` 走**类型槽**(tp_ 函数表),实例属性里放一个
`__len__` 完全被忽略——`len(li)` 照样 TypeError,`li.__len__` 手动调用却正常。
这是 CPython 的性能取舍:特殊方法查找不进属性查找流水线,只查 `type(obj)`。

## 6. 模块级 `__getattr__`(PEP 562)

模块属性:先查模块 `__dict__`,`__getattr__` 函数兜底——大型包**惰性导出**的官方姿势
(避免 import 时把重量级子模块全拉起来)。

## 自检

`python main.py` —— 14 项断言:四级优先级对拍(含 property 压 dict、dict 遮方法、
MRO 就近)/ 三种绑定 / 双拦截器分工 / 空槽异常 / 写删路由 / getset 与 member 描述符 /
特殊方法只看类 / 模块 getattr。

## 参考资料(实读)

- [Descriptor HowTo Guide — Python documentation](https://docs.python.org/3/howto/descriptor.html)
- [Data model — Customizing attribute access](https://docs.python.org/3/reference/datamodel.html#customizing-attribute-access)
- [PEP 562 — Module `__getattr__` and `__dir__`](https://peps.python.org/pep-0562/)
- CPython `Objects/typeobject.c`:`PyObject_GenericGetAttr` / `slot_tp_getattro`(语义出处,按 howto 归纳复刻)
