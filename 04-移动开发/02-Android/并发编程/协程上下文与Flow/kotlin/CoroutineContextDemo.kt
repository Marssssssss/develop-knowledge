package demo.coroutines

import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineName
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlin.system.measureTimeMillis

/**
 * 协程上下文 / 调度器 / 结构化并发 / Flow(官方 API 用法)。
 *
 * 依赖:org.jetbrains.kotlinx:kotlinx-coroutines-core(Android 上再加 -android 提供 Dispatchers.Main)。
 *
 * 三条最容易被忽略的事实:
 *  1) builder 不传 context 时,子协程继承父协程的调度器;传了就覆盖。
 *  2) Dispatchers.Default 的并行度上限 = CPU 核数,阻塞调用会占满它 → 用 Dispatchers.IO。
 *  3) 冷流每次 collect 都从零重跑;flowOn 只改变**它上游**的上下文。
 */
object CoroutineDemo {

    /** 1. 上下文合并:同键右侧覆盖;子协程未指定 dispatcher 时继承父的。 */
    fun contextMerge() = runBlocking(CoroutineName("root")) {
        println("parent ctx = $coroutineContext")
        launch {                                   // 不传 → 继承 root 的名字与调度器
            println("child inherited = $coroutineContext")
        }
        launch(Dispatchers.IO + CoroutineName("child-io")) {   // 显式覆盖
            println("child overridden = $coroutineContext")
        }
    }

    /** 2. 核数上限:Default 上跑 8 个各 10ms 的纯 CPU 任务,总耗时约为 2 倍单任务耗时。 */
    suspend fun cpuBoundShowsCoreLimit() {
        val elapsed = measureTimeMillis {
            coroutineScope {
                repeat(8) { launch(Dispatchers.Default) { burnCpu(10) } }
            }
        }
        println("8 x burnCpu(10ms) on Default(${Runtime.getRuntime().availableProcessors()}) = $elapsed ms")
    }

    /** 3. 结构化并发:父协程必须等子协程;父作用域被取消时子协程一起取消。 */
    suspend fun structuredConcurrency(): Long {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        val started = System.currentTimeMillis()
        val child: Job = scope.launch {
            launch { delay(30) }
            launch { delay(30) }
            delay(1)                               // 体很快就结束,但要等两个子协程
        }
        child.join()                               // join() 返回时所有子协程已完成
        return System.currentTimeMillis() - started
    }

    /** 4. 子协程失败会取消父与兄弟(supervisorScope 则会隔断这条传播链)。 */
    fun failurePropagation() = runBlocking {
        val results = coroutineScope {
            val a = async { delay(50); "A done" }
            val b = async { delay(5); throw IllegalStateException("worker crashed") }
            runCatching { a.await() } to runCatching { b.await() }
        }
        println("a=${results.first}, b=${results.second}")
    }

    /** 5. 冷流:不 collect 不执行;每个收集者触发一次全新执行;flowOn 只改上游上下文。 */
    fun coldFlowAndFlowOn(): Flow<Int> = flow {
        println("emitter in ${currentDispatcherName()}")
        for (v in 1..3) emit(v)
    }.map { v ->
        println("map-before-flowOn in ${currentDispatcherName()}")
        v * 2
    }.flowOn(Dispatchers.IO)                    // 上面两个算子在 IO 跑
        .map { v ->
            println("map-after-flowOn in ${currentDispatcherName()}")
            v + 1
        }

    /** 6. 热流:StateFlow 只把值送给「当前」的收集者,晚订阅者拿不到历史值。 */
    class CounterRepository {
        private val _count = MutableStateFlow(0)
        val count = _count.asStateFlow()
        fun bump() {
            _count.value += 1
        }
    }

    fun hotFlowSharing(repo: CounterRepository, scope: CoroutineScope) =
        repo.count
            .filter { it > 0 }
            .onEach { println("observed $it") }
            .catch { e -> println("upstream failed: ${e.message}") }
            .stateIn(scope, SharingStarted.WhileSubscribed(5_000), initialValue = 0)

    /** 7. 主安全:withContext 把一次切换收窄到最小范围,返回后仍在原调度器。 */
    suspend fun mainSafety(cpu: CoroutineDispatcher = Dispatchers.Default) = withContext(Dispatchers.IO) {
        println("io section in ${currentDispatcherName()}")
        "payload"
    }.let { payload ->
        println("back on ${currentDispatcherName()}, payload=$payload")
        payload
    }

    private suspend fun currentDispatcherName(): String =
        kotlin.coroutines.coroutineContext[CoroutineDispatcher]?.toString() ?: "unknown"

    private suspend fun burnCpu(ms: Long) {
        val deadline = System.nanoTime() + ms * 1_000_000
        var acc = 0L
        while (System.nanoTime() < deadline) acc += 1
        check(acc > 0)
    }
}

fun main() = runBlocking {
    CoroutineDemo.contextMerge()
    CoroutineDemo.cpuBoundShowsCoreLimit()
    println("structured concurrency took ${CoroutineDemo.structuredConcurrency()} ms")
    CoroutineDemo.failurePropagation()
    val flow = CoroutineDemo.coldFlowAndFlowOn()
    println("collected #1 = ${flow.collect { }}")
    println("collected #2 = ${flow.collect { }}")
}
