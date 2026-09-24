# contextvars 与异步上下文传播(PEP 567)

> `threading.local` 的状态绑在**物理线程**上;协程切换不换线程,谁的值跟谁走就成了问题。
> PEP 567 的答案是:把"当前上下文"变成显式对象(`Context`),变量(`ContextVar`)的值挂在它上面,
> 协程创建/回调调度时**拷贝快照**——逻辑执行流自带环境,物理线程只是载体。

## 1. 三个原语

```python
v = contextvars.ContextVar("v", default="d")   # 声明(建议放模块顶层)
tok = v.set(new_value)      # 返回 Token
v.reset(tok)                # 回滚到 set 之前
ctx = contextvars.copy_context()   # 当前上下文的浅拷贝
ctx.run(func, *args)        # 进入 ctx 执行,结束自动弹出
```

- `get` 三级回退:**方法参数 > 声明默认 > LookupError**;
- ContextVar 可哈希、有 `.name`,是实现 Mapping 键的基础。

## 2. Token:一次性回滚凭据

| 行为 | 结果 |
| --- | --- |
| `tok.old_value` | set 之前的值;当时未设置 → `Token.MISSING` 哨兵 |
| `v.reset(tok)` 二次使用 | `RuntimeError: ... already been used once` |
| 拿 B 变量的 token reset A | `ValueError: ... different ContextVar` |
| `with tok:` | **3.14 才有**,3.12 及以下没有 `__enter__` |

## 3. Context:快照 + 隔离边界

- `copy_context()` 是**浅拷贝快照**:拷贝之后外部再 `set`,快照看不见;
- `ctx.run()` 里的 `set` 记在 `ctx` 上,**退出即弹出**——外层值原样;
- 想再看这些修改?保存 `ctx` 再 `run` 一次(可反复进入);
- **同一 Context 不能嵌套进入自己**(`RuntimeError: cannot enter context ... already entered`),
  但退出后顺序重入完全合法;
- Context 实现 `Mapping`:`len / in / [] / 迭代 / keys / items`,未设置取 `[]` 抛 `KeyError`。

## 4. 线程边界:不继承、不串扰

每个线程有**自己独立的上下文栈**,新线程从全新空上下文开始:

- 主线程 set 的值,子线程里 `get` 直接 LookupError(不继承);
- 子线程里的 set 也不回传主线程。

## 5. 为什么不用 threading.local:池化串扰

```text
物理线程 T1:  处理请求A(tl.val="A") → 归还线程池 → 处理请求B(读到 tl.val="A" ✗)
```

`threading.local` 绑物理线程,池化场景上一个请求的残留会被下一个请求读到。
`ContextVar` 的值挂在**上下文快照**上:每个请求开始时 `copy_context()`(asyncio 自动做),
新请求新快照,天然干净。框架中间件(request id / 认证信息 / 链路追踪)应当用它。

## 6. asyncio 的两个快照时机(对照本机 3.12 源码)

| 时机 | 位置 | 快照点 | 后果 |
| --- | --- | --- | --- |
| Task 创建 | `asyncio/tasks.py:122` `Task.__init__` 里 `copy_context()` | **创建瞬间** | 任务里看到的是创建时的值;任务里的 set 全被含住,不影响外层 |
| 回调调度 | `asyncio/events.py:34` `Handle.__init__` 里 `copy_context()` | **调度瞬间** | `call_soon` 之后再改值,回调看到的仍是旧值 |

每次任务被事件循环重新唤醒(`__wakeup`/`__step`),都是带着**同一个**快照 `ctx.run(...)` 进去
(tasks.py:267)——所以协程挂起/恢复期间上下文不丢、不混。

## 7. 使用铁律

1. ContextVar **声明在模块顶层**,别在闭包/函数里创建(Context 持强引用,影响 GC);
2. 不要自己缓存 Context 跨线程乱用——并发进入同一 Context 直接 RuntimeError;
3. 中间件里 `set` 后要恢复,用 `token` 回滚而不是再 set 一次旧值(old_value 可能是 MISSING);
4. 多线程任务池(如 `run_in_executor`)想传递上下文,显式 `ctx = copy_context()` 后
   `ctx.run(fn)` 带过去——executor 的线程不会自动继承。

## 自检

`python main.py` —— 20 项断言:get 三级回退 / Token 一次性与跨变量校验 /
快照进出隔离 / Mapping 接口与重入规则 / 线程不继承不串扰 /
threading.local 池化串扰对照 / asyncio 两个快照时机。

## 参考资料(实读)

- [PEP 567 — Context Variables](https://peps.python.org/pep-0567/)
- [contextvars — Context Variables](https://docs.python.org/3/library/contextvars.html)
- 本机 CPython 3.12 `Lib/asyncio/tasks.py`(122 行 Task 上下文拷贝、267 行 __step 的 ctx.run)
- 本机 CPython 3.12 `Lib/asyncio/events.py`(34 行 Handle 上下文拷贝)
