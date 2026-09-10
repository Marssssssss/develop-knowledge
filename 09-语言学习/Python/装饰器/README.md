# Python · 装饰器(decorator)

> 装饰器是 Python 中最常见的"语言特性"之一:
> **不修改原函数代码,就能给函数加上额外行为**(日志、计时、权限校验、缓存……)。

## 一、简介

`@decorator` 语法糖的等价写法:

```python
@decorator
def func(): ...
# 等价于
def func(): ...
func = decorator(func)
```

**核心三件套**:

1. **函数是一等对象**(可赋值、可传参、可返回)
2. **闭包**(内层函数引用外层函数的变量)
3. **`*args` / `**kwargs`**(把参数透传给原函数)

任何能"接受函数 + 返回函数"的可调用对象,都能当装饰器用 —— 所以**类装饰器**也成立(实现 `__call__`)。

## 二、对比:为什么 Python 有,别的语言没这么自然?

| 语言 | 等价方案 | 痛点 |
| --- | --- | --- |
| **Python** | `@decorator` 一行 | — |
| Java | 注解 + 反射 / 动态代理 | 注解只是元数据,真正生效要靠 AOP 框架(Spring AOP) |
| JavaScript | 高阶函数 / `Proxy` | 写法像,但没有语法糖 |
| C / C++ | 宏 / 函数指针 | 不能优雅地"包住"任意函数 |
| Go | 函数是一等公民,但**没有** `@decorator` 语法 | 需要手写 wrapper,`func = decorate(func)` 模式 |
| Rust | trait + 宏 / 中间件模式 | 强类型下要写更多样板 |

Python 的优势:**语法糖 + 动态类型**让装饰器既短又能用在任意函数上。代价:**类型系统看不到装饰器做了什么**(运行时才生效)。

## 三、环境准备

| 项 | 要求 |
| --- | --- |
| Python | ≥ 3.6(typing 注解部分需要 3.10+) |
| 依赖 | 仅标准库,无第三方包 |

## 四、运行方式

```bash
cd 09-语言学习/Python/装饰器
python3 basic.py
python3 with_args.py
```

预期输出(均带耗时打印,便于直观看到装饰器生效):

```
[basic.py]
=== 基础装饰器:日志 + 计时 ===
[LOG] call: greet(name='Alice')
[LOG] done : greet -> 'Hello, Alice!' (took 0.10s)
[LOG] call: greet(name='Bob')
[LOG] done : greet -> 'Hello, Bob!' (took 0.10s)

[with_args.py]
=== 带参数的装饰器:retry ===
[RETRY] attempt 1/3 ...
[RETRY] attempt 2/3 ...
flaky() 成功于第 2 次
```

## 五、关键代码片段

### 1. 基础装饰器(`basic.py`)

```python
import functools
import time

def logger(func):
    @functools.wraps(func)        # 保留原函数 __name__ / __doc__
    def wrapper(*args, **kwargs):
        print(f"[LOG] call: {func.__name__}({args=}, {kwargs=})")
        t0 = time.perf_counter()
        try:
            result = func(*args, **kwargs)
        finally:
            dt = time.perf_counter() - t0
            print(f"[LOG] done : {func.__name__} -> {result!r} (took {dt:.2f}s)")
        return result
    return wrapper

@logger
def greet(name: str) -> str:
    """Say hello."""
    time.sleep(0.1)
    return f"Hello, {name}!"
```

要点:
- `wrapper(*args, **kwargs)` —— 透传任意参数
- `functools.wraps(func)` —— **强烈推荐**,否则 `greet.__name__` 会被改成 `wrapper`
- `try/finally` —— 即使原函数抛异常,日志也能打完

### 2. 带参数的装饰器(`with_args.py`)

```python
import functools
import random

def retry(max_attempts: int = 3):
    """装饰器工厂:retry 是返回装饰器的函数"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    print(f"[RETRY] attempt {attempt}/{max_attempts} failed: {e!r}")
                    if attempt == max_attempts:
                        raise
        return wrapper
    return decorator

@retry(max_attempts=3)
def flaky():
    if random.random() < 0.5:
        raise RuntimeError("随机失败")
    return "成功"
```

经典**三层闭包**:`retry(参数) → decorator(函数) → wrapper(调用)`。

等价写法(不用语法糖):

```python
def flaky(): ...
flaky = retry(max_attempts=3)(flaky)
```

### 3. 一行对比

| 写法 | 适用场景 |
| --- | --- |
| `@logger` | 无参装饰器 |
| `@retry(max_attempts=3)` | 带参装饰器(其实是装饰器工厂) |
| 类装饰器 | 装饰器自身需要状态(如 Flask 的 `@app.route`) |

## 六、注意事项

1. **`functools.wraps` 不要省** —— 否则 `help(greet)` 会显示 `wrapper`,调试和文档都炸。
2. **保留返回值的时机**:本例用 `try/finally` 打日志,把 `return result` 放在 `finally` 之外 —— 如果放在 `finally` 里,异常会吞掉返回值(虽然本来就异常,这里只是强调)。
3. **装饰器顺序有讲究**:`@A @B def f` 等价于 `f = A(B(f))`,**最上面的最先包、最里面的最后被调用**。
4. **类装饰器**:`retry` 也可以写成类,只需实现 `__call__(self, func)`,状态(如调用次数)自然存到 `self`。
5. **装饰器的副作用**:被装饰后函数签名变了(变成 `wrapper`),用 `inspect.signature` 取参数会取错 —— 高级场景用 `wraps` 或第三方库(例如 `wrapt`)。
6. **不要在模块导入时执行装饰器逻辑** —— 装饰器里的代码应该只定义行为,**不在 import 时跑业务**。这里的所有 print 都只在调用时执行,符合规范。

## 七、参考资料

- [Python 官方文档:装饰器](https://docs.python.org/3/glossary.html#term-decorator)
- [PEP 318 — Decorators for Functions and Methods](https://peps.python.org/pep-0318/)
- [PEP 3129 — Class Decorators](https://peps.python.org/pep-3129/)
- [functools.wraps](https://docs.python.org/3/library/functools.html#functools.wraps)
- [Real Python: Python Decorators](https://realpython.com/primer-on-python-decorators/)

## 八、相关 demo(同目录)

- ⏳ `生成器/` —— `yield` / `yield from` / 生成器表达式
- ⏳ `上下文管理器/` —— `with` 语句与 `contextlib`
- ⏳ `类型提示/` —— `typing` / `Protocol` / `Generic`