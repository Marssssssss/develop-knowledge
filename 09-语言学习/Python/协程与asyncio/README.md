# Python · 协程与 `asyncio`

> Python 的 `async` / `await` 不是"绿色线程",而是**单线程协作式调度**的事件循环:
> 一个 event loop 一次只跑一个 Task,**只在 `await` 处切**。这让 IO 密集型程序
> 可以用同步代码的写法,做到"几万 socket 在一个线程里同时等"。

## 一、简介

**核心概念**:

1. **coroutine function** —— `async def` 定义的函数
2. **coroutine object** —— 调用 coroutine function 返回的对象(尚未执行)
3. **Event Loop** —— 协作式调度器,单线程一次跑一个 Task
4. **Task** —— `asyncio.create_task()` 把 coroutine 包装成可被调度的对象
5. **Future** —— 低层 awaitable,代表"将来某个时刻有结果"
6. **Awaitable** —— 可被 `await` 的对象三类: coroutine / Task / Future

历史:`async` / `await` 在 PEP 492(Python 3.5)引入,统一替代 `@asyncio.coroutine` + `yield from`;
`TaskGroup` 在 PEP 654 / Python 3.11 引入;`asyncio.timeout` 上下文管理器在 3.11。

## 二、原理详解

### 1. `async def` 不会自动执行

```python
async def main(): ...
main()        # <coroutine object main at 0x...>  ← 没跑!
asyncio.run(main())    # 真正执行
```

启动入口三选一:

| 方式 | 用途 |
|------|------|
| `asyncio.run(main())` | **顶层入口**(标准做法) |
| `await coroutine` | 在另一个协程里等 |
| `asyncio.create_task(coro)` | **并发**调度 |

### 2. 三种 Awaitable

| 类型 | 来源 | 何时用 |
|------|------|--------|
| Coroutine | `async def` 函数返回值 | `await` 一个异步函数 |
| Task | `asyncio.create_task()` | 并发调度,自动 run soon |
| Future | `loop.create_future()` | 低层 API(应用层极少用) |

### 3. Task 必须持有强引用

```python
background_tasks = set()                 # ← 关键:set 而不是裸变量
for i in range(10):
    task = asyncio.create_task(some_coro(i))
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
```

否则事件循环只持有弱引用,任务可能在跑完前被 GC 掉。

### 4. 并发原语对比

| 原语 | 行为 | 取消策略 | 异常处理 |
|------|------|---------|---------|
| `gather(*aws)` | 并发 + 按入参顺序收集结果 | 自身被取消 → 全部取消 | 首个异常立即抛 |
| `TaskGroup` (3.11+) | 结构化并发,强安全保证 | 任一失败 → 取消剩余 | 聚合为 `ExceptionGroup` |
| `wait()` | 返回 `(done, pending)` | 自身被取消 → **不取消**子任务 | 通过 `return_when` 控制 |
| `as_completed()` | 按完成顺序迭代 | 超时**不取消**剩余 | 通过迭代自然传播 |

`TaskGroup` vs `gather` 的官方对比:

> TaskGroup provides stronger safety guarantees than gather for scheduling a nesting of subtasks:
> if a task (or a subtask, a task scheduled by a task) raises an exception, TaskGroup will,
> while gather will not, cancel the remaining scheduled tasks.

### 5. 超时控制(3.11+ 推荐 `asyncio.timeout()`)

```python
async with asyncio.timeout(10):
    await long_op()              # 超时 → 内部转 CancelledError → 抛 TimeoutError
```

`asyncio.wait_for(aw, timeout)` 是函数式写法(3.11 起也基于 `asyncio.timeout()` 实现)。
**3.11 行为变更**:统一抛 `TimeoutError`,不再用 `asyncio.TimeoutError`。

### 6. `to_thread` 包装阻塞 IO

```python
await asyncio.gather(
    asyncio.to_thread(blocking_io),     # 线程池里跑,事件循环不阻塞
    asyncio.sleep(1),
)
```

事件循环单线程,**任何同步阻塞 IO(`time.sleep` / `requests.get` / `open().read()`)** 都会卡住整个 loop。
**必须**用 `to_thread` / `loop.run_in_executor` 隔离。

## 三、对比:Python 协程 vs 其他并发模型

| 维度 | Python `asyncio` | Go goroutine | Rust tokio | Node.js |
|------|------------------|--------------|------------|---------|
| 调度模型 | 单线程协作 | M:N 抢占 | 多线程协作 | 单线程事件循环 |
| 启动开销 | 几 µs | 几 µs | 几 µs | 几 µs |
| 真并行 | ❌(需多进程) | ✅(调度到多核) | ✅(多线程) | ❌(同 Python) |
| 取消原语 | `Task.cancel()` | context.Context | join_handle.cancel() | AbortController |
| 同步阻塞 IO | 必须 to_thread | 会阻塞 | 必须 spawn_blocking | 会阻塞 event loop |
| CPU 密集 | 必须多进程 | 可以跑 | 可以跑 | 必须 Worker Thread |

## 四、环境与运行

```bash
cd 09-语言学习/Python/协程与asyncio
python3 coroutine.py
```

预期输出(关键行):

```
=== 第 1 节:async def + await 基础 ===
  [00:00:00] hello
  [00:00:00] world
  → 串行两个 50ms 任务 = 100 ms
  ⚠ 单纯调用 main() 不执行任何东西

=== 第 3 节:asyncio.gather 并发收集 ===
  Task C: Compute factorial(4), i=2...
  Task C: Compute factorial(4), i=3...
  Task C: factorial(4) = 24
  ...
  results = [2, 6, 24]    总耗时 ≈ 150 ms  (并发而非 250ms 串行)
```

## 五、关键代码片段

```python
# 并发运行 + 按完成顺序处理
async def main():
    tasks = [asyncio.create_task(slow_io(i)) for i in range(3)]
    async for earliest in asyncio.as_completed(tasks):       # 谁先完成谁先出来
        result = await earliest
        handle(result)

# 结构化并发(3.11+,推荐)
async def main():
    async with asyncio.TaskGroup() as tg:
        tg.create_task(fetch(url_a))
        tg.create_task(fetch(url_b))
    # 退出 with → 全部完成;任一失败 → 自动取消剩余 + ExceptionGroup

# 超时控制
async with asyncio.timeout(10):
    await long_op()                   # 超时 → TimeoutError,任务被取消
```

## 六、性能与边界

- **Task 创建开销**:几 µs(3.11+ 后显著降低)
- **每个 Task 占用约 8 KB**(`greenlet` 帧 + 控制块)—— 10 万 Task ≈ 800 MB
- **`gather` 的语义依赖入参顺序** —— 不保证按完成顺序返回
- **`as_completed` 内部用堆排序** —— O(N log N) 维护
- **事件循环是单线程的** —— 同步 CPU 密集代码会**完全卡住**整个程序

## 七、注意事项与常见坑

1. **`async def` 函数不能直接调用** —— 必须 `await` / `asyncio.run` / `create_task`
2. **`CancelledError` 是 `BaseException` 子类**(非 `Exception`) —— 不要写 `except Exception:` 吞掉
3. **同步阻塞 IO 严禁直接在协程里调** —— 必须 `asyncio.to_thread` 包装
4. **`TaskGroup` 退出时若还有未完成 task** —— 自动取消 + 抛 `ExceptionGroup`
5. **`asyncio.run()` 只能调一次** —— 会创建/关闭事件循环;重复调抛 `RuntimeError`
6. **嵌套 `asyncio.run()`** —— 不支持,会抛 `RuntimeError: asyncio.run() cannot be called from a running event loop`
7. **`gather` 被取消会取消所有子任务,但第一个异常不会取消未开始的** —— 用 `TaskGroup` 替代可避免
8. **异步生成器必须显式 `aclose()`** —— 不能依赖 GC,见相邻「生成器」demo

## 八、参考资料

- [Python Library — asyncio](https://docs.python.org/3/library/asyncio.html)
- [Python Library — asyncio-task](https://docs.python.org/3/library/asyncio-task.html)(coroutine / Task / gather / wait / as_completed)
- [Python Library — asyncio-sync](https://docs.python.org/3/library/asyncio-sync.html)(Lock / Event / Semaphore / Condition)
- [PEP 492 — Coroutines with async and await syntax](https://peps.python.org/pep-0492/)
- [PEP 525 — Asynchronous Generators](https://peps.python.org/pep-0525/)
- [PEP 654 — Exception Groups and except\*](https://peps.python.org/pep-0654/)(3.11+,`TaskGroup` 的异常聚合机制)
- [PEP 678 — Enriching Exceptions with Notes](https://peps.python.org/pep-0678/)
- [asyncio 官方 HOWTO](https://docs.python.org/3/howto/aio.html)

## 九、相关 demo

- 上一个:[`../描述符/`](../描述符/) —— `property` 是数据描述符
- 下一个:[`../元类/`](../元类/) —— 元类与 `__init_subclass__` 的取舍