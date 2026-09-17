# Kotlin 协程:上下文 / 调度器 / 结构化并发 / Flow

## 简介

协程把「挂起」变成语言级能力:函数可以暂停并让出线程,恢复时不必回到原线程。
在 Android 上它解决两个具体问题——**主线程安全**(网络/磁盘不能阻塞 UI 线程)与
**回调地狱**(异步结果的生命周期管理)。

关键概念:

| 概念 | 一句话解释 |
| --- | --- |
| `CoroutineContext` | 协程运行环境,是「元素集合」;主要元素有 `Job` 与 `CoroutineDispatcher` |
| `Job` | 协程句柄,构成父子树;取消父协程会取消整棵子树 |
| `Dispatchers.Default` | 面向 CPU 密集的调度器,**并行度上限 = CPU 核数** |
| `Dispatchers.IO` | 面向阻塞 I/O 的调度器,允许更大的并行度 |
| 冷流 / 热流 | 冷流每个收集者从零重跑;热流只把值送给当时的收集者 |

历史背景:结构化并发由 Roman Elizarov 在 2018 年正式提出并进入 kotlinx.coroutines 1.0,
核心主张是「协程的作用域必须显式嵌套」,从而让取消与异常传播有确定的边界。

## 原理详解

### 1. 上下文就是一个「键 → 元素」的集合

```text
val ctx = CoroutineName("root") + Dispatchers.Default + Job()
ctx[CoroutineName] = "root"; ctx[CoroutineDispatcher] = Dispatchers.Default
ctx.plus(other) → 同键**右侧覆盖**,未覆盖的键保留
```

`launch/async` 都可以接收一个 `CoroutineContext`:传了就覆盖继承来的同键元素,不传就继承父协程的
——所以「子协程跑在哪个线程」这件事,默认跟着父协程走。

### 2. 调度器决定「哪些线程」,挂起点决定「何时换线程」

- confined(受限)调度器:协程整个生命周期都待在该调度器的线程上(`Dispatchers.Main`、`Default`、`IO`)。
- `Dispatchers.Unconfined`:先在**调用者线程**执行,遇到第一个挂起点后,由挂起函数决定在哪个线程恢复。
  官方明确说它是「高级机制,不应在一般代码中使用」。
- `withContext(ctx)`:只把一小段代码切到另一个调度器,返回后回到原调度器,是「主安全」的标准写法。

### 3. 结构化并发:父协程必须等子协程

```text
parent (launch)
 ├── child A   delay(30)
 └── child B   delay(30)
父协程体在 1ms 就返回 → 但状态是 COMPLETING,直到 A、B 都结束才 COMPLETED
```

配套规则:

- 子协程抛异常 → **取消父协程与所有兄弟协程**,异常向上传到父协程;
- 想隔断这条传播链就用 `supervisorScope` / `SupervisorJob`;
- 协程被取消后不会「复活」——已取消的作用域里再 launch 会立刻被取消。

### 4. Flow:冷流是「配方」,不是「数据」

```text
flow { ... }     发射器(冷:不 collect 不执行)
  .map { }       中间算子(同样是冷的,collect 之前不处理任何值)
  .flowOn(IO)    只改变**它上游**的上下文
  .collect { }   收集者(终结算子),在收集者自己的上下文里执行
```

- **每个收集者触发一次全新执行**:两个 collector 各自把 builder 跑一遍,互不共享状态。
- **上下文保护**:官方规定不允许在另一个协程里发射值,违反会抛
  `IllegalStateException("Flow invariant is violated...")`;`flowOn` 就是给上游换上下文的唯一合法途径。
- 需要「一份数据多个观察者」时应改用热流(`StateFlow` / `SharedFlow`),但要注意晚订阅者拿不到历史值。

## 对比 / 选型

| 场景 | 选择 | 理由 |
| --- | --- | --- |
| 一次网络/磁盘请求 | `suspend fun` | 挂起函数只返回一个值,天然适配「请求-响应」 |
| 需要按需消费数据流 | 冷流 `flow {}` | 惰性、可重复收集,收集前零开销 |
| UI 状态可观察 | `StateFlow` | 热流 + 去重,总是有当前值,适合 `collectAsStateWithLifecycle` |
| CPU 密集计算 | `Dispatchers.Default` | 并行度对齐核数,不超发线程 |
| 阻塞调用(文件/JDBC/老 SDK) | `Dispatchers.IO` | 避免占满 Default 的核数 |

## 环境准备

- Kotlin + `kotlinx-coroutines-core`(Android 上另加 `kotlinx-coroutines-android` 提供 `Dispatchers.Main`)
- 自检模型:**Python 3.10+**,零第三方依赖

## 运行方式

### Python(模型 + 自检,可直接跑)

```bash
cd python
python3 coroutine_check.py    # 上下文 / 调度器 / 结构化并发:21 项断言
python3 flow_check.py         # 冷流 / flowOn / 上下文保护:18 项断言
```

### Kotlin

```bash
kotlinc CoroutineContextDemo.kt -cp kotlinx-coroutines-core.jar -include-runtime -d demo.jar
java -jar demo.jar
```

## 关键代码片段

```kotlin
// 1) 继承 vs 覆盖:不传 context 就继承父的调度器
coroutineScope {
    launch { /* 继承父协程的 dispatcher */ }
    launch(Dispatchers.IO + CoroutineName("child-io")) { /* 覆盖 */ }
}

// 2) 核数上限:8 个纯 CPU 任务在 Default(4) 上约需 2 倍单任务耗时
coroutineScope { repeat(8) { launch(Dispatchers.Default) { burnCpu(10) } } }

// 3) 结构化并发:join() 返回时,子协程一定都结束了
val child = scope.launch {
    launch { delay(30) }
    launch { delay(30) }
    delay(1)
}
child.join()

// 4) 子协程失败 → 父与兄弟一起被取消
runCatching { async { delay(5); throw IllegalStateException("crash") }.await() }

// 5) 冷流 + flowOn:上游算子跑 IO,下游算子在收集者上下文
flow {
    for (v in 1..3) emit(v)
}.map { it * 2 }.flowOn(Dispatchers.IO)
 .map { it + 1 }
 .collect { println(it) }
```

## 性能与边界

- `Dispatchers.Default` 的并行度上限是 CPU 核数(至少 2):把阻塞调用放进去会**占满核数**,
  使其它纯 CPU 协程排队;模型里 4 条线程被 4 个阻塞任务占满时,第 5 个小任务要等到 50 ms 之后。
- `Dispatchers.IO` 允许更大并行度,因此「多少个阻塞任务在飞」不会直接换算成墙钟时间。
- 冷流每次收集都重跑 builder:昂贵的数据源(网络/大查询)要注意重复执行成本。
- 取消是**协作式**的:协程必须经过挂起点(或显式检查 `isActive`)才会真正停下来;
  模型里被取消的协程若再次被调度,不会继续执行,但真实世界里纯 CPU 死循环仍然能顶住取消。

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| 子协程在 `join()` 之后还在跑 | 用了 `GlobalScope` 或自建 `CoroutineScope`,脱离了当前作用域 | 用 `coroutineScope` / `supervisorScope` / `viewModelScope` |
| 一个子协程失败,整屏都不刷新了 | 结构化并发默认向上传播取消 | 需要隔离就用 `supervisorScope` 或给子协程加 `SupervisorJob` |
| `flowOn` 之后算子还跑在 IO 上 | 记错方向:`flowOn` 只影响它**上游** | 把 `flowOn` 放在需要下沉的那段算子之后 |
| 在 `flow {}` 里换协程发值报错 | 违反上下文保护(官方要求在同一协程内发射) | 用 `flowOn`;确实要换协程用 `channelFlow` |
| 收集者拿到的是过期 UI 状态 | 用了 `StateFlow` 却按冷流思路理解 | 热流只发给当时在场的收集者,新订阅者从「当前值」开始 |
| Unconfined 下线程乱跳 | 它只在首个挂起点之前待在调用者线程 | 一般代码不要用 Unconfined |

## 参考资料(实际阅读过的权威来源)

- [Kotlin 官方文档《Coroutine context and dispatchers》](https://kotlinlang.org/docs/coroutine-context-and-dispatchers.html)
  —— 上下文元素、Dispatchers 与线程、Unconfined vs confined、`withContext`、Job 与父子关系
- [Kotlin 官方文档《Coroutines basics》](https://kotlinlang.org/docs/coroutines-basics.html)
  —— 挂起函数、`runBlocking` 的定位、结构化并发与协程作用域
- [Kotlin 官方文档《Flows》](https://kotlinlang.org/docs/coroutines-flow.html)
  —— 冷流/热流定义、中间算子的惰性、`flowOn` 语义、上下文保护与异常透明
- 本目录 `python/coroutine_runtime.py` + 两个自检脚本的实跑输出(39 项断言全绿)
