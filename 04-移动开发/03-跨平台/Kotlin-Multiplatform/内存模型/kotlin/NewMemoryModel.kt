// 新内存模型(Kotlin 1.7.20 起默认,1.9.20 起旧模型完全移除)下的写法。
//
// 权威依据:kotlinlang.org/docs/native-memory-manager.html 与 native-migration-guide.html(实读)。
//
// 一句话概括变化:**对象放在共享堆里、任何线程都能访问**,不再需要冻结;
// 回收交给"周期性追踪式 GC",从 root(局部变量、全局变量)出发标记不可达对象。
//
// 本机无 Kotlin/Native 工具链,不做编译,供人工审查。

package demo.memory

import kotlin.native.concurrent.AtomicReference
import kotlin.native.concurrent.Worker
import kotlin.native.internal.GC

// 【新写法 1】顶层属性:任何线程都能读写,不需要 @SharedImmutable。
// 但要注意初始化**时机**变了:全局属性改为"所属文件首次被访问时"惰性初始化
// (旧模型是程序启动时)。想在启动时就初始化,标 @EagerInitialization。
@OptIn(ExperimentalStdlibApi::class)
@EagerInitialization
val BOOT_MARKER: String = "initialized-at-startup"

val LAZY_DEFAULT_TIMEOUT: Int = 30        // 首次访问本文件时才初始化

// 【新写法 2】共享可变状态:普通类即可,字段随便改。
class SharedCache {
    private val store = LinkedHashMap<String, Int>()

    @Synchronized                            // 共享堆 != 无数据竞争,该加锁还是要加
    fun put(key: String, value: Int) {
        store[key] = value
    }

    @Synchronized
    fun snapshot(): Map<String, Int> = HashMap(store)
}

// 需要无锁时,AtomicReference 依然可用(但含义变了:它不再是"跨线程共享的唯一手段")。
class AtomicFlag {
    private val flag = AtomicReference(false)

    fun set(value: Boolean) {
        flag.value = value
    }

    fun get(): Boolean = flag.value
}

// 【新写法 3】跨 Worker 传递:不再要求"孤立的子图",直接把对象传过去。
// 官方原话:"Worker.execute no longer requires producers to return an isolated object subgraph".
fun runOnWorker(cache: SharedCache) {
    val worker = Worker.start()
    worker.execute(TransferMode.SAFE, { cache }) { shared ->
        shared.put("from-worker", 1)         // 直接改同一个对象,无需冻结
    }.result
    worker.requestTermination().result
}

// 【新写法 4】GC 是可观测、可手动触发的:
//   GC.collect()       强制发起一次回收并等待完成
//   GC.lastGCInfo()    上一次 GC 的统计(排查全局变量导致的内存泄漏时很有用)
@OptIn(ExperimentalStdlibApi::class)
fun heapBytesAfterFullGc(): Long {
    GC.collect()
    return GC.lastGCInfo!!.memoryUsageAfter["heap"]!!.totalObjectsSizeBytes
}

// 【新写法 5】与 Swift/ObjC ARC 的集成要点(来自 native-arc-integration.html):
//   * 对象**只在 GC 时**才被回收 —— 跨 interop 边界的 Swift/ObjC 对象也不例外,
//     所以它们的 deinit 可能比你预期的晚;
//   * deinit 在哪个线程取决于对象是"在哪条线程传进 Kotlin 的":
//     主线程传入 → 主线程 deinit;其他线程传入、或主队列没被处理 → 特殊 GC 线程
//     (该线程带 run loop 并会排空 autorelease pool);
//   * 长循环里每轮都造跨边界临时对象时,GC 日志里 "stable refs in the root set" 会持续增长,
//     对策是把循环体包进 `autoreleasepool { ... }`(本 demo 的 python 侧量化了这一点);
//   * 从 Swift 调 Kotlin 挂起函数时,completion handler 可能回到**非主线程**。
fun interopHints() {
    // Kotlin 侧写 autoreleasepool 需要平台库,这里只留说明。
}

// 【编译/运行时开关】文档给出的可调项(全部写进 gradle.properties):
//   kotlin.native.binary.gc=pmcs                  回退到 parallel mark concurrent sweep
//   kotlin.native.binary.gc=noop                  关闭 GC(内存只涨不回收,仅测试用)
//   kotlin.native.binary.gcMarkSingleThreaded=true 标记阶段单线程(大堆上停顿变长)
//   kotlin.native.binary.pagedAllocator=false     关掉分配分页(按对象预留,启动内存更低)
//   kotlin.native.binary.objcDisposeOnMain=false  让 deinit 一律走特殊 GC 线程
//   -Xruntime-logs=gc=info                        打开 GC 日志(只写 stderr)
//
// 分配器方面:内存被切成页,连续排列以便顺序清扫;每个分配是页内的一个块,页记录块大小;
// 不同页类型针对不同分配尺寸优化;线程按分配尺寸找合适的页,页不够时向共享分配空间要
// (可能已有可用页、可能需要先清扫、也可能要新建)。分配忽然暴涨而 GC 跟不上时,
// 分配器会强制一段 stop-the-world 直到这一轮迭代完成。
