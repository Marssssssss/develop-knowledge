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
| 装饰器(`@decorator`) | ✅ | **本目录第一个 demo** |
| 生成器(`yield`) | ⏳ 计划 | 惰性序列 |
| 异常处理(`try/except/else/finally`) | ✅ | 支持 `else` 和 `finally` |
| 类型提示(`typing`) | ⏳ 计划 | 大型项目必学 |
| 描述符(`__get__`) | 进阶 | 理解 `@property` / ORM |
| 元类(`metaclass`) | 高阶 | ORM / 框架常用 |
| `async` / `await` | ⏳ 计划 | asyncio 模型 |
| GIL 与多进程 | ⏳ 计划 | CPU 密集场景 |
| 协变逆变 / `Protocol` | 进阶 | typing 高级用法 |

## 三、已完成的 demo

| 主题 | 路径 |
| --- | --- |
| 装饰器 | [装饰器/](./装饰器/) |

## 四、待研究清单(按"教学价值"排序)

1. 生成器与 `yield from`
2. 上下文管理器(`contextlib`)
3. 异常链(`raise from`)与 `except*`(Python 3.11+)
4. 类型提示(`typing` / `Protocol` / `Generic`)
5. 描述符与 `property`
6. `dataclasses` 与 `attrs`
7. `async`/`await` 与 asyncio
8. GIL、多进程与 `concurrent.futures`
9. 迭代器协议与 `itertools`
10. 元类(ORM 怎么用)

## 五、参考资料

- [Python 官方教程](https://docs.python.org/3/tutorial/index.html)
- [Python 语言参考](https://docs.python.org/3/reference/index.html)
- [PEP 8 — 代码风格](https://peps.python.org/pep-0008/)
- [Fluent Python(经典书)](https://www.fluentpython.com/)