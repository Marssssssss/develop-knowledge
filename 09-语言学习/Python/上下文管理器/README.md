# Python · 上下文管理器(context manager)

> `with` 语句让"获取资源 / 出错释放"的样板代码消失,Python 标准库 95% 的
> I/O 对象都是上下文管理器(`open()` / `threading.Lock()` / `subprocess.Popen`...)。
> 理解 `__enter__` / `__exit__` 协议是写出**资源安全**(Resource-Acquisition-Is-Initialization)
> 代码的基础。

## 一、简介

**核心三件套**:

1. `__enter__(self)` —— 进入 `with` 块时调用,返回绑定到 `as` 变量的对象
2. `__exit__(self, exc_type, exc_val, exc_tb)` —— 退出 `with` 块时调用,**无论是否异常**
3. `__exit__` 返回 **`True`** → 抑制异常(异常不冒到外层);**`None`/`False`** → 让异常继续传播

历史:`with` 在 PEP 343(Python 2.5)引入;`@contextmanager` 同期;
`@asynccontextmanager` 在 Python 3.7;`suppress`/`ExitStack` 在 Python 3.3-3.4;
`BaseExceptionGroup` 与 `suppress` 的交互(PEP 654)在 Python 3.11/3.12。

## 二、原理详解

### 1. `with` 语句等价形式

```python
with EXPR as VAR:
    BLOCK
```

等价于:

```python
mgr = (EXPR)
exit = type(mgr).__exit__              # 不存在就 AttributeError
value = type(mgr).__enter__(mgr)
exc = True
try:
    VAR = value
    try:
        BLOCK
    except:
        exc = False
        if not exit(mgr, *sys.exc_info()):
            raise
finally:
    if exc:
        exit(mgr, None, None, None)
```

**关键观察**:
- 正常退出:`__exit__(None, None, None)`
- 异常退出:`__exit__(exc_type, exc_val, exc_tb)` —— 其返回值决定是否吞掉异常

### 2. `@contextmanager` 的实现原理

`@contextmanager` 把一个**生成器函数**包装成上下文管理器:

```python
@contextlib.contextmanager
def managed_resource():
    resource = acquire()
    try:
        yield resource          # ← 这条 yield 把值绑定到 as
    finally:
        release(resource)       # ← cleanup,即使 BLOCK 抛异常也执行
```

底层:`__enter__` 推进生成器到第一个 `yield`,`__exit__(exc)` 把异常
`throw()` 回生成器内部,在 `yield` 处重新抛出 → `try/finally` 捕获并清理。

### 3. `__exit__` 返回值规则

| `__exit__` 返回值 | 效果 |
|-------------------|------|
| `True`(真值) | 异常被抑制,with 块后续继续 |
| `None` / `False`(假值) | 异常正常向上抛 |

> ⚠️ 用 `@contextmanager` 时若只想**记录日志**而非抑制,**必须重新 `raise` 该异常**,
> 否则 `@contextmanager` 会把它当作已处理,with 块后续代码会照常执行(违反直觉)。

### 4. `ExitStack` —— 动态上下文管理

适合**数量运行时才确定**的场景(打开一批文件、连接池等):

```python
with contextlib.ExitStack() as stack:
    files = [stack.enter_context(open(fname)) for fname in filenames]
    # 全部文件进栈,with 退出时逆序全部关闭
```

**关键能力**:
- `enter_context(cm)` —— 入栈 + 调 `__enter__`,返回 `__enter__` 的结果
- `push(exit)` —— 只入栈回调,不入上下文
- `callback(fn, *args)` —— 入栈普通回调(无法抑制异常)
- `pop_all()` —— 把栈转移到新 ExitStack,延后或转移清理时机

### 5. `suppress` / `closing` / `nullcontext`

| 工具 | 版本 | 用途 |
|------|------|------|
| `suppress(*excs)` | 3.4(组 3.12) | 抑制指定异常;**慎用**,只用于"已知继续执行是正确做法" |
| `closing(thing)` | 3.4 | 退出时调 `thing.close()` —— 给不支持协议的对象加尾巴 |
| `aclosing(thing)` | 3.10 | 异步版 closing,调 `await thing.aclose()` |
| `nullcontext(value=None)` | 3.7(异步 3.10) | 占位上下文,`with` 块时是 no-op |

## 三、对比:与其他语言的 RAII

| 维度 | Python `with` | C++ RAII / scope guard | Java try-with-resources | Rust `Drop` |
|------|---------------|------------------------|-------------------------|-------------|
| 触发点 | `__exit__` | 析构函数 / 显式 close | try 块结束(自动) | 作用域结束(自动) |
| 抑制异常 | `__exit__` 返 `True` | `noexcept` / catch + swallow | N/A | `Drop::drop` 不能 fail |
| 异步 | `@asynccontextmanager` 3.7+ | C++20 co_await? | N/A | `Drop` 是同步 |
| 动态组合 | `ExitStack` | 容器 + 自定义守卫 | N/A | `Drop` 链式 |

Python 的特殊之处:**`__exit__` 返回值动态决定异常处理**,这是其他语言很少见的"软控制"。

## 四、环境与运行

```bash
cd 09-语言学习/Python/上下文管理器
python3 context_manager.py
```

预期输出(关键行):

```
=== 第 1 节:class-based 实现 ===
  [DB] open  → <connection to orders_db>
  use → <connection to orders_db>
  [DB] commit → <connection to orders_db>
  [DB] open  → <connection to users_db>
  [DB] rollback (exc=RuntimeError: 模拟写库失败)

=== 第 3 节:__exit__ 返回值规则 ===
  [SuppressError] suppress  ValueError: 不该冒出来
  ✓ ValueError 被 SuppressError 完全吞掉,with 块后续代码照常运行
  这行仍然执行 —— 因为异常被吞了
  [PassThroughError] propagate  ValueError: 会冒出来
  ✓ PassThroughError 让异常正常传播: 会冒出来

=== 第 4 节:ExitStack 动态组合 ===
  opened 3 files; with 退出时全部自动关闭
```

## 五、关键代码片段

```python
@contextlib.contextmanager
def timer(label):
    t0 = time.perf_counter()
    try:
        yield t0                    # as timer() 拿到 t0
    finally:
        dt = time.perf_counter() - t0
        log.info(f"{label} took {dt*1000:.1f}ms")    # 必执行

# 动态组合多个文件
with contextlib.ExitStack() as stack:
    handles = [stack.enter_context(open(f, "w")) for f in filenames]
    for fh in handles:
        fh.write(content)
# with 退出 → 逆序关闭所有文件(哪怕中途抛异常)
```

## 六、性能与边界

- **进入/退出开销**:单次 `__enter__`/`__exit__` 调用,O(1) 纳秒级
- **`@contextmanager` 比 class-based 慢约 2-3 倍** —— 多一次生成器推进 + try/except 框架
- **`ExitStack`** 可处理任意数量,但清理是 O(N) 顺序执行
- **异步 `@asynccontextmanager`** 不能在同步 `with` 里用 —— 必须 `async with`

## 七、注意事项与常见坑

1. **`@contextmanager` 只能消费一次** —— 单次性对象;复用 → `RuntimeError: generator didn't yield`
2. **`__exit__` 抛了异常会替换原异常** —— 替代而非抑制
3. **`suppress` 不要捕获 `BaseException`** —— `KeyboardInterrupt` / `SystemExit` 也会被吞,变成死循环
4. **`ExitStack` 不是重入的** —— 同栈不可嵌套;需要嵌套场景用 `pop_all()` 转移
5. **异步生成器 cleanup 责任在调用方** —— 必须 `aclose()`,否则 finally 块可能在事件循环关闭时跑
6. **`@asynccontextmanager` 必须装饰 `async def` 函数** —— 否则抛 `TypeError`
7. **3.12 起 `suppress` 对 `BaseExceptionGroup` 智能剥离** —— 只会去掉被 suppress 的子异常,剩余用 `derive()` 重抛

## 八、参考资料

- [Python Library — contextlib](https://docs.python.org/3/library/contextlib.html)(官方,含全部工具)
- [Python Reference — The with statement](https://docs.python.org/3/reference/compound_stmts.html#the-with-statement)
- [Python Data Model — Context Managers](https://docs.python.org/3/reference/datamodel.html#context-managers)
- [PEP 343 — The "with" Statement](https://peps.python.org/pep-0343/)(`with` 引入)
- [PEP 654 — Exception Groups and except\*](https://peps.python.org/pep-0654/)(3.11+,`suppress` 与组的交互)
- [PEP 678 — Enriching Exceptions with Notes](https://peps.python.org/pep-0678/)

## 九、相关 demo

- 上一个:[`../生成器/`](../生成器/) —— `@contextmanager` 底层就是生成器协议
- 下一个:[`../描述符/`](../描述符/) —— `property` 是个数据描述符