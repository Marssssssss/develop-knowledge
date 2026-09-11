# Jetpack Compose 重组(Recomposition)机制

## 简介

Jetpack Compose 是 Android 推出的现代 UI 框架,核心思想是"函数即 UI"——`@Composable` 函数描述 UI 结构,框架根据状态变化自动重新执行(重组,Recomposition)受影响的函数。本 demo 聚焦"如何最小化重组开销"这一关键性能问题。

**关键概念清单**:
- **Composable 函数**:带 `@Composable` 注解的 Kotlin 函数,可被 Compose 编译器插件处理;调用会被记录到"槽位表"(slot table)
- **重组(Recomposition)**:当某个 State 的值变化时,Compose 重新执行读取了该 State 的 Composable 函数
- **State**:通过 `mutableStateOf` / `mutableIntStateOf` 等创建的可观察值;写入触发重组
- **Recomposition Scope**:重组边界;Compose 智能识别到最小重组范围(默认是 composable 函数体)
- **Smart Recomposition(智能跳过)**:若 Composable 的输入参数未变化且内部读取的 State 也未变,Compose 自动跳过该 Composable 的执行
- **三阶段渲染**:Composition(组合) → Layout(布局) → Drawing(绘制);跳过 Composition 直接进入后续阶段可大幅优化

**历史背景**:2019 年 Google I/O 公布 Jetpack Compose 1.0(2019 年 alpha);2021 年稳定版发布;2022 年 Compose 1.2 加入 LazyColumn / derivedStateOf 改进;2024 年 Compose 1.6 支持 Compiler Gradle Plugin 完全独立(Kotlin 2.0+)。本 demo 假定 Compose 1.5+(BOM 2024.02+)。

## 原理详解

### 重组触发流程

```
state.value = newValue (主线程, Snapshot 系统记录写)
        │
        ▼
Snapshot 系统标记该 state 失效,通知所有监听者
        │
        ▼
Compose Recomposer 收到失效通知
        │
        ├─→ 找到读该 state 的 Recomposition Scope
        │
        ▼
对 scope 内 Composable 重新执行(若输入参数和读取的 state 都没变 → 跳过)
        │
        ▼
重新构造 Composition 树(更新 slot table)
        │
        ▼
Layout 阶段(从根向下测量 + 布局)
        │
        ▼
Draw 阶段(从根向下绘制到 RenderNode)
        │
        ▼
下一帧 vsync 信号触发提交到 GPU
```

### 重组粒度示意

```kotlin
@Composable
fun Parent() {
    var a by remember { mutableStateOf(0) }  // Parent scope
    ChildA()                                // 不读 a → a 变化不重组 ChildA
    ChildB(a)                               // 读 a → a 变化重组 ChildB
}

@Composable
fun ChildB(value: Int) {
    var b by remember { mutableStateOf(0) }  // ChildB scope
    Text("a=$value b=$b")                  // 读 a → a 变化重组 Text
}
```

变化 `a`:Parent 的 scope 标记 invalid → ChildB 被重组 → Text 被重组 → ChildA 不受影响。
变化 `b`:只有 ChildB 内 Text 重组 → Parent 和 ChildA 不变。

### 三阶段渲染与 lambda 修饰符

| 修饰符 | 触发阶段 | 适合场景 |
| --- | --- | --- |
| `Modifier.offset(x, y)` | Composition(必须先重组才能读 x, y) | 静态定位、偶尔变化 |
| `Modifier.offset { IntOffset(x, y) }` | Layout(在 layout 阶段才调用 lambda) | 滚动驱动高频更新 |
| `Modifier.background(color)` | Composition | 静态背景 |
| `Modifier.drawBehind { drawRect(color) }` | Drawing | 颜色快速变化(动画) |
| `Modifier.requiredWidth(width)` | Composition | 静态尺寸 |
| `Modifier.requiredWidth { width }` | Layout | 尺寸随父布局变化 |

**核心规律**:频繁变化的状态传入修饰符时,优先用 lambda 版本,直接进入 Layout/Draw 阶段而跳过 Composition。

### 核心 API 及关键参数

| 类 / 函数 | 签名 | 说明 |
| --- | --- | --- |
| `mutableStateOf<T>(initial)` | `@Composable fun <T> mutableStateOf(initial: T): MutableState<T>` | 创建可观察 State |
| `mutableIntStateOf(initial)` | `@Composable fun mutableIntStateOf(initial: Int): MutableIntState` | 装箱优化(避免装箱) |
| `remember<T>(key, calculation)` | `@Composable fun <T> remember(key1: Any?, ..., calculation: @DisallowComposableCalls () -> T): T` | 缓存计算结果,key 变化重算 |
| `derivedStateOf(calculation)` | `@Composable fun <T> derivedStateOf(calculation: () -> T): State<T>` | 仅在结果变化时通知下游 |
| `derivedStateOf(policy, calculation)` | 重载 | 指定 SnapshotMutationPolicy(structuralEqualityPolicy 等) |
| `Composable` | annotation | 标记可组合函数;编译器插件生成重组感知代码 |
| `Modifier.offset(block: Density.() -> IntOffset)` | 重载 | Layout 阶段读 lambda |
| `Modifier.drawBehind(block: DrawScope.() -> Unit)` | 函数 | Draw 阶段读 lambda |

### 底层发生了什么

- **Snapshot 系统**:Compose 用 MVCC 类似的多版本快照追踪 State 变化;每次 mutation 在一个 snapshot 中进行;读 snapshot 拿到一致的视图;commit 时触发 invalidation
- **Slot Table**:Composable 函数编译后生成"分组调用"代码,运行时把"槽位"存到 slot table(树形结构);重组通过更新 slot table 而非新建 View 实现
- **Smart Recomposition**:编译器生成"组 key"和"参数比较代码";重组时若输入参数结构相等且内部 state 未变,跳过函数执行
- **Recomposer**:`androidx.compose.runtime.Recomposer` 是协程;挂起在 `awaitFrame` 上,等 vsync 信号触发一次重组;失败时记录到 `currentCompositionErrors`

## 对比 / 选型

| 维度 | Compose | 传统 View + XML | DataBinding |
| --- | --- | --- | --- |
| 重组粒度 | 函数级 | View 级(findViewById 树) | View 级 |
| 重组开销 | 小(Slot Table 增量更新) | 大(re-measure + re-layout + re-draw) | 中(同 View) |
| 状态管理 | State + 重组 | findViewById + setText | ObservableField |
| 学习曲线 | 较陡(Kotlin + 函数式 + Compose API) | 陡(布局 + 生命周期 + 适配器) | 中 |
| 工具支持 | Layout Inspector / Recomposition 高亮 | Hierarchy Viewer | Data Binding Inspector |
| 推荐场景 | 新项目 / 新页面 | 维护旧代码 / 复杂自定义 View | 表单绑定场景 |

## 环境准备

- Android Studio Hedgehog(2023.1+)或更新
- minSdk 21,compileSdk 34,targetSdk 34
- Jetpack Compose BOM 2024.02.00+(androidx.compose.* 一组兼容版本)
- Kotlin 1.9.20+(Compose 编译器插件对 Kotlin 版本敏感)
- AGP 8.2.0+

## 运行方式

将 `kotlin/ComposeRecompositionDemo.kt` 放入现有 Android 项目的 `src/main/java/com/example/compose/`,在 `MainActivity.onCreate()` 中:

```kotlin
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            ComposeRecompositionDemo.demo1_basic()
        }
    }
}
```

需在 `build.gradle.kts` 添加 Compose 依赖:

```kotlin
dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.02.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.activity:activity-compose:1.8.2")
}
```

**注**:本机无 Kotlin/Android SDK 编译环境,代码走人工代码审查(对照官方文档与 Compose 编译器插件生成规则验证)。

## 关键代码片段

### derivedStateOf 限制重组频率(对应 Demo 3)

```kotlin
val listState = rememberLazyListState()
LazyColumn(state = listState) {
    items(100) { Text("Item $it") }
}
// 错误:每次 firstVisibleItemIndex 变化都触发下游重组
// val showButton = listState.firstVisibleItemIndex > 0
// 正确:仅 true/false 切换时触发
val showButton by remember {
    derivedStateOf { listState.firstVisibleItemIndex > 0 }
}
AnimatedVisibility(visible = showButton) { ScrollToTopButton() }
```

### lambda modifier 跳过 Composition 阶段(对应 Demo 4)

```kotlin
// 错误:每次 scroll 变化触发 Box 重组
// Box(Modifier.fillMaxSize().offset(y = scroll.value.toDp()))

// 正确:Layout 阶段才读 scroll → 跳过 Composition
@Composable
fun Title(scrollProvider: () -> Int) {
    Box(Modifier.fillMaxSize().offset { IntOffset(0, scrollProvider()) })
}
```

### remember 缓存昂贵计算(对应 Demo 2)

```kotlin
// 错误:每次重组都排序
// val sorted = contacts.sortedWith(comparator)

// 正确:remember + key 仅在必要时重排
val sorted = remember(contacts, comparator) {
    contacts.sortedWith(comparator)
}
```

### 反模式:Backwards write(对应 Demo 5)

```kotlin
// ❌ 错误:组合中读取 + 写入 → 无限重组
// @Composable
// fun Bad() {
//     var count by remember { mutableIntStateOf(0) }
//     Text("$count")
//     count++  // backwards write!
// }

// ✅ 正确:写入放进事件回调
@Composable
fun Good() {
    var count by remember { mutableIntStateOf(0) }
    Button(onClick = { count++ }) { Text("Count: $count") }
}
```

## 性能与边界

- **重组频率**:可组合函数被频繁调用(动画每帧 60-120 次);高频重组函数须保持轻量
- **智能跳过边界**:编译器对 lambda / 局部函数 / 内联类等不生成稳定 group key,可能错过优化;尽量用普通 `@Composable` 函数
- **derivedStateOf 开销**:Snapshot 系统 + 缓存有少量开销;对极简计算(加减乘除)用普通表达式即可,过度包装反而慢
- **Lambda 捕获**:lambda 每次重组都创建新对象,可能被 modifier 内部 recomposition 检查识别为"不稳定" → 跳过优化;`remember { lambda }` 可稳定 lambda
- **Backwards write 后果**:未在事件中写入状态,可能导致每帧 N 次重组(N = State 写入频率);严重时 ANR
- **Compose 与 View 混用**:AndroidView / ComposeView 可桥接;性能上 Composable 内嵌传统 View 会引入额外重组屏障,建议顶层切分
- **测试工具**:Layout Inspector + "Recomposition counts" 高亮;`Modifier.composed` 自定义时也可加日志

## 注意事项与常见坑

1. **derivedStateOf 不要无脑套**:内部 Snapshot 追踪开销 > 直接读取;仅在"读取频率 >> 结果变化频率"时才有收益(典型:滚动状态)
2. **remember 缓存读取 State 的派生值是错的**:`val a = remember { state.value + 1 }` 不会随 state 变化;应该用 `derivedStateOf { state.value + 1 }` 或直接表达式
3. **不要在 Composable 顶层用 `LaunchedEffect(Unit)` 替代 `rememberCoroutineScope`**:LaunchedEffect 在重组时不会重启(Unit 不变);需要响应参数变化时传相应 key
4. **lambda modifier 仍触发 Recomposition 标记**:`Modifier.offset { ... }` 内部仍标记 lambda 为 invalid,但跳过函数执行;Layout Inspector 会显示 skip 标记
5. **Backwards write 编译器不会警告**:必须人工识别(组合末尾修改已读 state)
6. **derivedStateOf 必须配合 remember**:单独使用每次重组都新建 derivedStateOf 实例,失去缓存意义
7. **Compose 编译器版本必须匹配 Kotlin 版本**:Kotlin 1.9.x 对应 Compose Compiler 1.5.x;Kotlin 2.0+ 用 Compose Compiler Plugin(独立 Gradle 插件)
8. **重组不等于性能问题**:被 Compose 智能跳过的 Composable 函数不会执行;只看 Layout Inspector 的实际执行次数
9. **Modifier 顺序敏感**:`Modifier.padding().background()` vs `Modifier.background().padding()` 渲染区域不同;padding 在 background 内 vs 外效果不一样
10. **Compose 1.5+ 强类型 State**:用 `mutableIntStateOf` / `mutableFloatStateOf` 等特化版本替代 `mutableStateOf(0)`,避免装箱开销

## 参考资料(实际阅读过的权威来源)

- [Compose performance best practices | Android Developers](https://developer.android.google.cn/develop/ui/compose/performance/bestpractices) — remember / derivedStateOf / lambda modifier / backwards write 完整最佳实践
- [State and Jetpack Compose | Android Developers](https://developer.android.google.cn/develop/ui/compose/state) — State 类型 + 重组触发机制
- [Lifecycle of composables | Android Developers](https://developer.android.google.cn/develop/ui/compose/lifecycle) — 进入/退出 Composition + 重组时机
- [Jetpack Compose mental model | Android Developers](https://developer.android.google.cn/develop/ui/compose/mental-model) — 重组 = 数据驱动 + 智能跳过
- [Advanced State and Side Effects codelab | Android Developers](https://developer.android.google.cn/codelabs/jetpack-compose-advanced-state-side-effects) — derivedStateOf / LaunchedEffect / rememberCoroutineScope 完整示例
- [Side effects in Compose | Android Developers](https://developer.android.google.cn/develop/ui/compose/side-effects) — derivedStateOf 完整 API 与使用场景
- [androidx.compose.runtime API reference | Android Developers](https://developer.android.google.cn/reference/kotlin/androidx/compose/runtime) — mutableStateOf / remember / derivedStateOf 函数签名
- [Jetpack Compose: Debugging Recomposition | Medium](https://medium.com/androiddevelopers/jetpack-compose-debugging-recomposition-bfcf4a6f8d37) — Recomposition 计数与延迟读取技巧