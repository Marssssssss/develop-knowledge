# Kotlin/Native 内存模型:从"冻结"到共享堆 + 追踪式 GC

## 简介

Kotlin Multiplatform 的 native 目标(KMP 的 iOS/macOS 侧)不跑在 JVM 上,
它有自己的运行时、自己的分配器、自己的 GC,以及一套**与 JVM 完全不同**的对象共享规则。
这套规则在 1.7.20 发生了根本性变化,旧的"冻结(freezing)"模型在 1.9.20 被**完全移除**。

KMP 项目里最典型的翻车现场就发生在这一层:JVM 侧写得毫无问题的"顶层可变全局 +
跨线程共享对象",搬到 native 侧就会撞上 `InvalidMutabilityException`,或者在
Swift 侧看到"对象迟迟不 deinit / 内存一直涨"。

关键概念:

- **共享堆(shared heap)** —— 新模型下对象放在共享堆,任意线程可访问。
- **冻结(freezing)** —— 旧模型的核心机制,已废弃;`isFrozen` 现在恒返回 `false`。
- **CMS GC** —— concurrent mark and sweep,不分代,由内存压力启发式或定时器触发。
- **stable refs** —— GC 日志里"根集合中的稳定引用数",排查 interop 泄漏的关键指标。
- **ARC 集成** —— Kotlin 用追踪式 GC,Swift/ObjC 用引用计数,两者需要协作。

## 原理详解

### 1. 新模型的三条基线

官方对现模型的定义是"similar to the JVM, Go, and other mainstream technologies":

1. 对象存在**共享堆**里,任何线程都能访问;
2. 周期性执行**追踪式 GC**,回收"从 root(局部变量、全局变量)不可达"的对象;
3. GC 算法当前是 **concurrent mark and sweep**,**不把堆分成代**。

GC 在**独立线程**上运行,由内存压力启发式或定时器启动,也可以手动调
`kotlin.native.internal.GC.collect()`(会发起一次回收并**等待完成**)。
标记阶段默认与应用程序线程**并发**,并在多个线程上并行推进 —— 参与者包括
应用线程、GC 线程和可选的 marker 线程;`kotlin.native.binary.gcMarkSingleThreaded=true`
可以关掉并行标记,代价是大堆上的停顿变长。标记结束后处理弱引用,把指向未标记对象的
引用置空,默认同样并发。

### 2. 旧模型到底限制了什么(以及为什么废掉)

旧模型的核心矛盾是"对象不能自由跨线程共享",于是发明了冻结。迁移指南列出的
**具体限制解除清单**就是最好的说明:

| 旧模型的限制 | 新模型 |
| --- | --- |
| 顶层属性要 `@SharedImmutable` 才能跨线程访问 | 任何线程都能访问和修改,标注可直接删 |
| 经 interop 边界传入的对象必须先冻结 | 可直接访问与修改,无需冻结 |
| `Worker.executeAfter` 要求操作对象已冻结 | 不再要求 |
| `Worker.execute` 要求生产者返回**孤立的子图** | 不再要求 |
| 含 `AtomicReference` / `FreezableAtomicReference` 的**引用环会泄漏** | 环能被追踪式 GC 回收 |
| 冻结是递归的,写已冻对象抛 `InvalidMutabilityException` | 冻结始终禁用 |

一并被移除/替换的 API:`@SharedImmutable`、`freeze()`、`isFrozen`(恒 `false`)、
`ensureNeverFrozen()`、`FreezingException`、`InvalidMutabilityException`、
`IncorrectDereferenceException`、`FreezableAtomicReference`(→ `AtomicReference`)、
`atomicLazy()`(→ `lazy()`)、`MutableData`(→ 普通集合)、
`WorkerBoundReference<T>`(→ 直接 `T`)、`DetachedObjectGraph<T>`(→ 直接 `T`,
过 C interop 时用 `StableRef`)。

### 3. 一个容易忽略的行为变化:全局属性改成了惰性初始化

官方原文:*"Global properties are initialized lazily when the file they are defined in
is accessed first. Previously, global properties were initialized at the program startup."*

这不是"性能优化",而是**语义变化**:靠顶层 `val` 的初始化副作用(注册表登记、
单例构造、回调挂载)来"在启动时跑点代码"的写法,在新模型下不再可靠 —— 文件不被访问,
初始化就不发生。官方给的补救办法是给这些属性加 `@EagerInitialization`。

本 demo 的 `python/main.py` 把这个差异建模成可断言的实验:同一组 3 个全局属性,
legacy 路径在启动阶段就产生 3 条 init 记录,新模型路径启动阶段一条都没有,
直到真正访问了那个文件才初始化它自己的属性。

### 4. 引用环:为什么"引用计数思维"会误判

`AtomicReference` 构成的环(A ↔ B)在引用计数下**永远降不到 0**,而追踪式 GC 从 root
出发做可达性分析,整环一起回收。这正是官方把"含 `AtomicReference` 的引用环不再泄漏"
单列成一条改进的原因。

自检脚本里做了对照:同一套节点,无环链(A→B)上两种算法结论**完全一致**;
一旦成环(二元环、三元环),引用计数漏掉 100%,追踪式 GC 全部回收。
**差异只来自环** —— 这也解释了为什么这类泄漏往往在"对象图从树变成图"的那次重构后才出现。

### 5. 与 Swift/Objective-C ARC 的集成

Kotlin 有追踪式 GC,Swift/ObjC 有 ARC,官方说集成"usually seamless and generally
requires no additional work",但有四件事必须知道:

1. **对象只在 GC 时才被回收** —— 跨 interop 边界的 Swift/ObjC 对象也一样,
   所以 `deinit` 可能明显晚于"最后一次使用"。
2. **`deinit` 在哪个线程由传入线程决定**:从主线程传给 Kotlin → 主线程 deinit;
   从其他线程传入、或主队列没有被处理 → 由**特殊 GC 线程**执行
   (该线程带 run loop 并会排空 autorelease pool)。
   `kotlin.native.binary.objcDisposeOnMain=false` 可强制一律走 GC 线程。
3. **长循环 + interop = 根集合膨胀**:每轮都造临时跨边界对象时,GC 日志里的
   "number of stable refs in the root set" 会持续增长,官方给的解法是把循环体包进
   `autoreleasepool { ... }`。
4. **挂起函数的 completion handler 可能不在主线程**(官方示例里出现了编号 7 的线程),
   需要主线程就自己切。

第 3 条被建模成了本 demo 的量化实验:

| 场景 | 根集合峰值 | 排空次数 | 循环结束时占用 |
| --- | --- | --- | --- |
| 不包 `autoreleasepool`(1000 轮) | **1000** | 0 | 1000 |
| 每轮包 `autoreleasepool` | **1** | 1000 | 0 |

峰值相差 1000 倍,并且随循环长度**线性增长** —— 这就是"日志里的数一直涨"的量化形态。

## 对比 / 选型

| 维度 | 旧模型(≤1.6 时代,1.9.20 移除) | 新模型(1.7.20 起默认) |
| --- | --- | --- |
| 对象共享 | 需冻结 / 线程局部 | 共享堆,任意线程 |
| 顶层可变属性 | `@SharedImmutable` / `@ThreadLocal` | 直接写 |
| 跨 Worker 传值 | 必须孤立子图 | 直接传 |
| AtomicReference 环 | 泄漏 | 可回收 |
| 全局初始化时机 | 程序启动 | 首次访问所属文件 |
| 回收算法 | 冻结 + 引用计数式语义 | 并发标记清除(不分代) |

与 JVM 的差异:同样"任意线程访问共享堆",但 native 侧**没有分代假设**、
**没有 JVM 那套 `-Xmx`/GC 选择器**、GC 参数只能通过 `kotlin.native.binary.*` 二进制选项调;
另外 native 侧多了 ARC 集成这一层,这是 JVM 侧完全没有的问题。

## 环境准备

- 操作系统:任意(python 自检脚本纯标准库)
- Python 3.8+
- Kotlin / Kotlin-Native 或 KMP 工程:阅读代码用,本机无工具链,不做编译

## 运行方式

### Python(三个实验的自检)

```bash
cd python
python3 main.py     # 34 项断言
```

### Kotlin

`kotlin/LegacyFrozen.kt` 是**历史对照**(里面的 API 在 1.9.20 起已移除,不要照抄);
`kotlin/NewMemoryModel.kt` 是现模型的写法示例,需放进 KMP 工程的 native 源集编译。

## 关键代码片段

```kotlin
// 新模型:顶层可变属性直接写
val LAZY_DEFAULT_TIMEOUT: Int = 30            // 首次访问本文件时才初始化

@EagerInitialization
val BOOT_MARKER: String = "initialized-at-startup"   // 想在启动时就初始化,必须标注

// 跨 Worker 直接传对象,不再需要冻结 / 孤立子图
fun runOnWorker(cache: SharedCache) {
    val worker = Worker.start()
    worker.execute(TransferMode.SAFE, { cache }) { shared -> shared.put("k", 1) }.result
    worker.requestTermination().result
}

// GC 可观测:排查全局变量泄漏的标准手法
GC.collect()
val bytes = GC.lastGCInfo!!.memoryUsageAfter["heap"]!!.totalObjectsSizeBytes
```

## 性能与边界

- GC 是 **CMS 且不分代**:长生命周期对象多、短生命周期对象少的负载下,
  不像分代 GC 那样"只扫新生代便宜",代价与堆规模相关。
- 分配器按**页**组织,连续排列便于顺序清扫;不同页类型服务不同尺寸;
  分配突然暴涨时会强制 stop-the-world 直到本轮迭代完成。
- `kotlin.native.binary.pagedAllocator=false` 改成按对象预留,能压低启动内存,
  但代价是不能再在 Apple 平台上跟踪 Kotlin 的内存占用。
- 关掉 GC(`gc=noop`)只适合短命程序或测试,内存会一直涨。

## 注意事项与常见坑

1. **照抄旧文章写 `freeze()` / `@SharedImmutable`**:1.9.20 起这些 API 已移除,
   编译就过不去;老文章里的"必须冻结"结论全部过期。
2. **依赖顶层 `val` 的初始化副作用**:新模型下改成惰性,文件不被访问就不执行 ——
   需要启动即生效就加 `@EagerInitialization`。
3. **Kotlin 单测里用 `Dispatchers.Main`**:官方明确指出单测中没有任何东西处理主线程队列,
   要么 mock(`Dispatchers.setMain`),要么别用。
4. **把"共享堆"当作"不需要同步"**:共享堆解决的是**可见性/生命周期**问题,
   不解决数据竞争,并发写依然要 `@Synchronized` / 原子类型 / 通道。
5. **Swift 侧看到 deinit 迟迟不来就以为泄漏**:先看 GC 有没有跑;
   长循环场景按官方建议包 `autoreleasepool`。
6. **`kotlinx.coroutines` 用了带 `native-mt` 后缀的老版本**:迁移指南要求 ≥1.6.0
   且不要用 `native-mt` 后缀。

## 参考资料(实际阅读过的权威来源)

- [Kotlin/Native memory management](https://kotlinlang.org/docs/native-memory-manager.html)
  —— 共享堆、CMS、标记并行与开关、手动 collect、分配器分页、GC 日志与 signpost、内存消耗调节项
- [Migrate to the new memory manager](https://kotlinlang.org/docs/native-migration-guide.html)
  —— 新旧对比清单、全局属性惰性初始化、AtomicReference 环、废弃 API 对照表、协程依赖要求
- [Integration with Swift/Objective-C ARC](https://kotlinlang.org/docs/native-arc-integration.html)
  —— deinit 线程规则、只在 GC 时回收、stable refs 增长与 autoreleasepool、completion handler 线程
