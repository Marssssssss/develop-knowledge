# Python

> 一门强调**可读性**和**开发效率**的多范式语言:
> - 面向对象(几乎一切皆对象)
> - 函数式(一等函数、lambda、map/reduce、生成器)
> - 动态类型 + 鸭子类型,但可选 typing 注解
> - 解释执行(CPython),有 GIL,适合 IO 密集型与胶水场景

## 一、Python 关键差异(对比主流语言)

| 维度 | Python | C / Go / Rust | Java / Kotlin |
| --- | --- | --- | --- |
| 类型系统 | 动态 + 可选静态注解 | 静态 | 静态 |
| 并发模型 | GIL + asyncio / 多进程 | 真并行(线程/协程) | 线程 + 协程 |
| 函数 | 一等公民 | 一等 / 普通 | 普通(Java) / 一等(Kotlin) |
| 语法缩进 | 强制缩进 | 花括号 / 关键字 | 花括号 |
| 运行时 | 解释(字节码) | 编译 | 编译(JVM) |
| 性能 | 慢(纯 Python) | 快 | 中 |

## 二、Python 独有/标志性的语言特性

按学习路径排序,前半段几乎所有 Python 代码都会用到:

| 特性 | 必学? | 备注 |
| --- | --- | --- |
| 缩进语法 | ✅ | Python 强制 4 空格缩进 |
| 一等函数 | ✅ | 函数可赋值、可传参、可返回 |
| 列表推导 / 字典推导 | ✅ | 比 `map`/`filter` 更 Pythonic |
| 切片(slice) | ✅ | `a[1:5:2]` 步长语法 |
| 解包(unpacking) | ✅ | `a, b, *rest = lst` |
| 上下文管理器(`with`) | ✅ | 资源自动释放 |
| 装饰器(`@decorator`) | ✅ | 见 [装饰器/](./装饰器/) |
| 生成器(`yield`) | ✅ | 见 [生成器/](./生成器/) |
| 异常处理(`try/except/else/finally`) | ✅ | 支持 `else` 和 `finally` |
| 结构化模式匹配(`match`/`case`) | ✅ | 见 [结构化模式匹配/](./结构化模式匹配/) |
| 类型提示(`typing`) | ⏳ 计划 | 大型项目必学 |
| 描述符(`__get__`) | ✅ | 见 [描述符/](./描述符/) |
| 元类(`metaclass`) | ✅ | 见 [元类/](./元类/) |
| `async` / `await` | ✅ | 见 [协程与asyncio/](./协程与asyncio/) |
| GIL 与多进程 | ✅ | 见 [GIL与自由线程/](./GIL与自由线程/) |
| 引用计数与 GC | ✅ | 见 [引用计数与GC/](./引用计数与GC/) |
| 导入机制(`importlib`) | ✅ | 见 [导入机制/](./导入机制/) |
| 协变逆变 / `Protocol` | 进阶 | typing 高级用法 |

## 三、已完成的 demo

| 主题 | 路径 |
| --- | --- |
| 装饰器 | [装饰器/](./装饰器/) |
| 生成器与 yield | [生成器/](./生成器/) |
| 上下文管理器 | [上下文管理器/](./上下文管理器/) |
| 描述符协议 | [描述符/](./描述符/) |
| 协程与 asyncio | [协程与asyncio/](./协程与asyncio/) |
| 元类与 `__init_subclass__` | [元类/](./元类/) |
| 字节码与自适应解释器(PEP 659) | [字节码与自适应解释器/](./字节码与自适应解释器/) |
| 结构化模式匹配(`match`/`case`) | [结构化模式匹配/](./结构化模式匹配/) |
| 引用计数、分代 GC 与 weakref | [引用计数与GC/](./引用计数与GC/) |
| 导入机制(`importlib` / `sys.meta_path`) | [导入机制/](./导入机制/) |
| GIL 与自由线程(PEP 703) | [GIL与自由线程/](./GIL与自由线程/) |

## 四、待研究清单(按"教学价值"排序)

1. ~~生成器与 `yield from`~~ ✅
2. ~~上下文管理器(`contextlib`)~~ ✅
3. 异常链(`raise from`)与 `except*`(Python 3.11+)
4. 类型提示(`typing` / `Protocol` / `Generic` / PEP 695)
5. ~~描述符与 `property`~~ ✅
6. `dataclasses` / `attrs` / `__slots__` 与对象内存布局
7. ~~`async`/`await` 与 asyncio~~ ✅
8. ~~GIL、多进程与 `concurrent.futures`~~ ✅
9. 迭代器协议与 `itertools` / `functools`
10. ~~元类(ORM 怎么用)~~ ✅
11. ~~字节码、`dis` 与自适应解释器(PEP 659)~~ ✅
12. ~~结构化模式匹配(PEP 634)~~ ✅
13. ~~引用计数、分代 GC 与 `weakref`~~ ✅
14. ~~导入系统(module spec / `sys.meta_path` / 命名空间包)~~ ✅
15. 字符串与编码(PEP 393 柔性表示 / UTF-8 模式)
16. 数值类型与 `decimal` / `fractions` 的精度模型

## 五、参考资料

- [Python 官方教程](https://docs.python.org/3/tutorial/index.html)
- [Python 语言参考](https://docs.python.org/3/reference/index.html)
- [PEP 8 — 代码风格](https://peps.python.org/pep-0008/)
- [Fluent Python(经典书)](https://www.fluentpython.com/)