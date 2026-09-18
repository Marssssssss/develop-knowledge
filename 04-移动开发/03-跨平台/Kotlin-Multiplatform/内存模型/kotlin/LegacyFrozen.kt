// 旧(Kotlin/Native legacy)内存模型下"共享可变状态"的历史写法。
//
// ⚠️ 本文件是**历史对照**,不是可编译的现代代码:官方迁移指南明确写了
//    "Support for the legacy memory manager has been completely removed in Kotlin 1.9.20"。
//    下面每个 API 在新模型里要么被移除、要么恒为常量,compile 时会直接报错/警告。
//
// 权威依据:kotlinlang.org/docs/native-migration-guide.html(实读)。
//
// 旧模型的核心约束是**对象不能在多线程间自由共享** —— 想共享就得"冻结",
// 冻结后的对象图不可再改;顶层属性默认被冻结或退化成线程局部。

@file:Suppress("DEPRECATION")

package demo.legacy

// 【旧写法 1】顶层可变属性:必须标注 @SharedImmutable 才能跨线程访问,
// 或者用 @ThreadLocal 让每个线程各持一份 —— 两者都牺牲了"普通可变全局变量"这一直觉。
@SharedImmutable
val SHARED_CONFIG: Map<String, String> = mapOf("env" to "prod")

@ThreadLocal
var perThreadCounter: Int = 0

// 【旧写法 2】可变状态要塞进 AtomicReference 才能在 Worker 之间共享。
// 注意:一旦进入 AtomicReference,值本身仍是"要么冻结、要么被换掉"的语义。
class LegacyCache {
    private val store = kotlin.native.concurrent.AtomicReference<Map<String, Int>>(emptyMap())

    fun put(key: String, value: Int) {
        // 每次更新都要整体替换,而不是原地修改
        store.value = store.value + (key to value)
    }

    fun snapshot(): Map<String, Int> = store.value
}

// 【旧写法 3】跨 Worker 传递对象:生产者必须返回"孤立的子图"(isolated object subgraph),
// 也就是一堆彼此只被这一份引用持有的对象;共享的节点会被拒绝。
fun produceForWorker(): kotlin.native.concurrent.WorkerBoundReference<List<Int>> {
    val data: List<Int> = ArrayList<Int>().apply { add(1); add(2); add(3) }
    return kotlin.native.concurrent.WorkerBoundReference(data)
}

// 【旧写法 4】冻结是**递归**的:对某个对象调用 freeze() 会把它可达的一整张图都冻住。
// 一旦冻住还去写,就抛 InvalidMutabilityException。
fun freezeAndThenWrite(): String {
    val mutable = ArrayList<String>()
    mutable.add("first")
    mutable.freeze()                 // 此刻 mutable 及其元素全部变为不可变
    return try {
        mutable.add("second")        // 旧模型下:抛 InvalidMutabilityException
        "未抛异常(说明已不在旧模型上运行)"
    } catch (e: Throwable) {
        "抛出了 ${e::class.simpleName}"
    }
}

// 【旧写法 5】调试用的"这根刺":提前声明某个对象**绝不允许**被冻结,
// 一旦它在某个时刻被冻结(往往是经过某条意料之外的路径),立即抛 FreezingException,
// 从而把"谁冻的"暴露在最早的现场,而不是等到后面某处写入失败。
fun guardAgainstFreezing(obj: Any) {
    if (false) {                     // 旧模型里是:obj.ensureNeverFrozen()
        @Suppress("UNREACHABLE_CODE")
        println(obj)
    }
}

// 迁移指南列出的"必须清理"清单(新模型下全部失效):
//   @SharedImmutable              → 直接删掉标注即可(新模型下无警告)
//   freeze() / isFrozen           → 删除;isFrozen 恒返回 false
//   ensureNeverFrozen()           → 删除
//   InvalidMutabilityException    → 删除全部用法
//   FreezingException             → 删除全部用法
//   IncorrectDereferenceException → 删除全部用法
//   FreezableAtomicReference      → 改用 AtomicReference
//   atomicLazy()                  → 改用 lazy()
//   MutableData                   → 改用普通集合
//   WorkerBoundReference<T>       → 直接用 T
//   DetachedObjectGraph<T>        → 直接用 T(过 C interop 时用 StableRef)
