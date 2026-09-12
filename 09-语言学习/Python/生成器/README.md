# Python · 生成器(generator)与 `yield`

> Python 函数体中只要出现 `yield` 表达式,该函数就**自动**变成生成器函数;
> 调用它得到的是一个**生成器迭代器**(generator-iterator),遵循迭代协议,
> 同时自带 `send` / `throw` / `close` 三个方法,可被双向通信。

## 一、简介

生成器把"**惰性求值**"和"**协程式暂停/恢复**"两种能力缝在一起:

- **惰性**:值只在 `next()` 时才计算,适合无限流、大文件、按需计算
- **协程式**:可在 `yield` 处挂起并保留全部局部状态(指令指针、栈、变量绑定、异常上下文)
- **协议合一**:任何实现了 `__iter__` / `__next__` 的对象都可被 `for` 消费,生成器自动满足

历史:`yield` 在 PEP 255(Python 2.2)引入;`yield from` 在 PEP 380(Python 3.3)引入;
`async` 生成器在 PEP 525(Python 3.6)引入。

## 二、原理详解

### 1. 挂起/恢复语义

调用生成器函数返回**生成器迭代器**(generator-iterator),**函数体尚未执行**。
首次调用 `__next__()`(或 `send(None)`)才推进到第一个 `yield`,挂起并返回该值。
后续每次 `__next__()` / `send(v)` 都从挂起点恢复,把 `v` 作为 `yield` 表达式的当前值。

### 2. `send` / `throw` / `close` 三方法

| 方法 | 行为 |
|------|------|
| `gen.send(value)` | 恢复执行,`value` 作为当前 `yield` 表达式的值;**首次必须传 `None`** |
| `gen.throw(exc)` | 在挂起点**抛**入异常,生成器内部 `except` 可捕获 |
| `gen.close()` | 在挂起点抛 `GeneratorExit`;若生成器又 `yield` 了一次值 → `RuntimeError` |

`close()` 后行为速查(官方原文):

| 状态 | 行为 |
|------|------|
| 已正常退出 / 因异常退出 | `close()` 返回 `None`,无其他效果 |
| 不捕获 `GeneratorExit`(默认传播) | 返回 `None` |
| 捕获后又 `yield` 一个值 | **抛 `RuntimeError`** |
| 捕获并 `return value`(3.13+) | 返回该 `value` |
| 抛出其他异常 | 该异常传播给调用者 |

> 在生成器**正在执行中**调用以上任一方法 → 抛 `ValueError`。

### 3. `yield from <iterable>` 委派语义(PEP 380)

`yield from` 把外层生成器的 `__next__` / `send` / `throw` / `close` **全部自动转发**给子迭代器,
并把子生成器的 `return value` 变成 `yield from` 表达式的值(经 `StopIteration.value`)。

```python
def outer():
    result = yield from inner()   # result = inner 函数的返回值
    yield result
```

**括号可省**:`yield from <expr>` 在赋值语句右侧时,**当**它是唯一表达式,括号可省。

### 4. 异步生成器差异(PEP 525)

- 必须在 `async def` 函数体中 `yield`
- `__anext__` / `asend` / `athrow` / `aclose` 都是 **awaitable**
- 耗尽抛 `StopAsyncIteration` 而非 `StopIteration`
- 提前退出时调用方**必须** `await aclose()`,否则 finally 块可能在事件循环关闭时跑

## 三、对比:为什么 Python 的生成器独特?

| 维度 | Python 生成器 | JavaScript generator | Go goroutine | C# iterator |
|------|--------------|---------------------|--------------|-------------|
| 双向通信(`send`) | ✅ 内置 | ❌(只有 `next`) | ❌(用 channel) | ❌ |
| 异常注入(`throw`) | ✅ 内置 | ❌ | ❌ | ❌ |
| 委派语法(`yield from`) | ✅ PEP 380 | ❌(需手写 `yield*`) | ❌ | ❌ |
| 异步版 | ✅ PEP 525(`async` 生成器) | ❌ | — | `IAsyncEnumerable` |

## 四、环境与运行

```bash
cd 09-语言学习/Python/生成器
python3 generator.py
```

预期输出(关键行):

```
=== 第 1 节:基础生成器 ===
  next(g) = 1
  next(g) = 2
  next(g) → StopIteration(value=None)

=== 第 2 节:send / throw / close 三方法 ===
  next(gen)        = 1
  next(gen)        = None
  gen.send(2)      = 2
  gen.throw(...)   = demo-error()
  [echo] Don't forget to clean up when 'close()' is called.

=== 第 3 节:yield from 委派 ===
  next(o)          = 'A'
  o.send('hello')  = 'B'
  next(o)          → StopIteration(value='outer_done')
```

## 五、关键代码片段

```python
def echo(value=None):
    try:
        while True:
            try:
                value = (yield value)   # send(v) 把 v 注入这里
            except Exception as e:
                value = e               # throw(exc) 被 except 捕获
    finally:
        print("close() 触发的清理")    # close() 抛 GeneratorExit,finally 执行

gen = echo(1)
next(gen)         # 启动 → 走到 (yield 1) → 返回 1
gen.send(42)      # (yield 1) → value = 42,继续到下一个 (yield value) → 返回 42
gen.throw(ValueError, "boom")  # 注入异常,except 捕获后 value = exception
gen.close()       # 触发 finally,生成器终态
```

## 六、性能与边界

- **时间复杂度**:每 `next()` 是函数调用的固定开销(几十 ns),无额外锁/同步
- **内存**:常量 O(1),即使表达"无限序列"(对比 `list(range(10**8))` OOM)
- **取舍**:无随机访问(不能 `gen[5]`);只能单向推进
- **状态保存**:挂起时整个 frame 被保留,但**只占一个 frame 的栈空间**

## 七、注意事项与常见坑

1. **首次启动必须 `next()` 或 `send(None)`** —— 否则 `send(value)` 抛 `TypeError`
2. **`yield` 不能在推导式/生成器表达式内部使用** —— 隐式作用域不允许
3. **生成器只能消费一次** —— 遍历完后内部状态耗尽,再 `next()` 永远 `StopIteration`
4. **`close()` 后再 `next()`** → `StopIteration`(已终态)
5. **不要在 `finally` 里 `yield`**(除非你明确要触发 `RuntimeError`)—— 容易误导
6. **`@contextmanager` 依赖生成器协议**(见相邻 demo「上下文管理器」)
7. **异步生成器清理责任在调用方** —— 必须显式 `aclose()`,不能依赖 GC

## 八、参考资料

- [Python Reference — Yield expressions](https://docs.python.org/3/reference/expressions.html#yield-expressions)(6.2.10 全部小节)
- [Python Reference — Yield from](https://docs.python.org/3/reference/expressions.html#yield-expressions) §6.2.10 / PEP 380
- [PEP 255 — Simple Generators](https://peps.python.org/pep-0255/)
- [PEP 380 — Syntax for Delegating to a Subgenerator](https://peps.python.org/pep-0380/)(`yield from` 完整委派语义)
- [PEP 525 — Asynchronous Generators](https://peps.python.org/pep-0525/)(`async def` + `yield`)
- [PEP 479 — StopIteration handling inside generators](https://peps.python.org/pep-0479/)
- [Fluent Python(2nd) — Chapter 17: Iterators, Generators, and Classic Coroutines](https://www.fluentpython.com/)

## 九、相关 demo

- 上一个:[`../装饰器/`](../装饰器/) —— `@contextmanager` 装饰器内部就是一个生成器
- 下一个:[`../上下文管理器/`](../上下文管理器/) —— 详解 `@contextmanager` + `ExitStack`