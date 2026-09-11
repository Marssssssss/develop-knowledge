package com.example.compose

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.Button
import androidx.compose.material.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.IntOffset

/**
 * Jetpack Compose 重组(Recomposition)机制 —— 5 个最小 demo
 *
 * 核心问题:可组合函数(Composable)被频繁调用(动画每帧一次);不必要的重组会浪费 CPU。
 * 5 个 demo 覆盖 mutableStateOf / remember / derivedStateOf / lambda modifier / backwards write 等关键模式。
 *
 * 运行环境:Android 项目 + Jetpack Compose BOM 2024.02.00+,Kotlin 1.9.20+
 *
 * 在 Activity.onCreate() 中通过 setContent { ComposeRecompositionDemo.demo1_basic() } 等调用。
 *
 * 注:Compose 由 Kotlin 编译器插件支持,Java 侧支持有限且非主流,本 demo 仅提供 Kotlin 实现。
 */
object ComposeRecompositionDemo {

    /**
     * Demo 1 — 基本 mutableStateOf + 自动重组
     *
     * 机制:
     *   - mutableStateOf 创建可观察状态,写入会触发依赖它的 Composable 重组
     *   - by setValue / getValue 委托语法(Kotlin 编译器生成)使赋值 = setValue
     *   - 每次 state.value 变化 → Compose 标记最近 Recomposition Scope 为 invalid
     *   - 下次重组时该 scope 内所有读取过 state.value 的 Composable 重新执行
     *   - ⚠️ 仅重组到"读取了该 state 的最小范围"(粒度到 composable 函数)
     */
    @Composable
    fun demo1_basic() {
        var count by remember { mutableIntStateOf(0) }
        Column {
            // 这一行 Text 读取 count,count 变化 → 重组这一行(不是整个 Column)
            Text("Count: $count")
            Button(onClick = { count++ }) { Text("Increment") }
        }
    }

    /**
     * Demo 2 — remember 缓存高开销计算
     *
     * 机制:
     *   - remember(key1, key2, ...) 仅在 key 变化时重新计算 lambda
     *   - 不传 key → 首次组合时计算,后续重组复用(直到 Composable 离开树)
     *   - 与普通 val 的区别:普通 val 每次重组都重新计算
     *   - 适用:列表排序、过滤、复杂对象构造
     *   - ⚠️ 不要用 remember 缓存读取 State 的派生值(用 derivedStateOf 替代)
     */
    @Composable
    fun <T> demo2_rememberSort(items: List<T>, comparator: Comparator<T>, renderItem: @Composable (T) -> Unit) {
        val sorted = remember(items, comparator) {
            items.sortedWith(comparator)  // 只在 items/comparator 变化时排序
        }
        LazyColumn {
            items(sorted.size) { i -> renderItem(sorted[i]) }
        }
    }

    /**
     * Demo 3 — derivedStateOf 限制重组频率
     *
     * 机制:
     *   - 普通表达式:`val showButton = listState.firstVisibleItemIndex > 0` 每次重组都读 → 滚动时频繁重组
     *   - derivedStateOf 包装后,只在"结果变化"时触发下游重组(0→1 边界才更新)
     *   - 内部用 Snapshot 系统追踪依赖 + 缓存;policy 控制何时算"结果变化"(默认结构相等)
     *   - 配合 remember 使用:`remember { derivedStateOf { ... } }`
     *   - ⚠️ 仅"结果在不同值之间"才更新;读取一个恒定不变的状态触发不了下游
     */
    @Composable
    fun demo3_derivedState() {
        val listState = rememberLazyListState()
        LazyColumn(state = listState) {
            items(100) { i -> Text("Item $i") }
        }
        // 每次 firstVisibleItemIndex 变化都触发下游重组 → 滚动每帧一次
        // val showButtonNaive = listState.firstVisibleItemIndex > 0

        // 正确:用 derivedStateOf → 仅 true/false 切换时触发下游
        val showButton by remember {
            derivedStateOf { listState.firstVisibleItemIndex > 0 }
        }
        if (showButton) {
            Button(onClick = { /* scroll to top */ }) { Text("↑") }
        }
    }

    /**
     * Demo 4 — lambda modifier 跳过重组,只重布局
     *
     * 机制:
     *   - Modifier.offset(x: Dp, y: Dp) 接收 Dp 参数 → Compose 必须先重组才能拿到 offset → 进入 Composition 阶段
     *   - Modifier.offset { IntOffset(x, y) } 接收 lambda → Compose 在 Layout 阶段才调用 lambda → 跳过 Composition
     *   - 同理:.background(color) vs .drawBehind { drawRect(color) }
     *   - 规律:频繁变化的状态传入修饰符时,优先用 lambda 版本(进入 Layout/Draw 阶段而非 Composition 阶段)
     */
    @Composable
    fun demo4_lambdaModifier(scrollProvider: () -> Int) {
        // 错误:scrollProvider() 读取在 Composable body → 每次 scroll 变化触发 Box 重组
        // Box(Modifier.offset(x = 0.dp, y = scrollProvider().toDp()))

        // 正确:lambda offset → 跳过 Composition,直接在 Layout 阶段读 scrollProvider
        Box(
            Modifier
                .fillMaxSize()
                .offset { IntOffset(x = 0, y = scrollProvider()) }
        ) {
            Text("Title (smooth)")
        }
    }

    /**
     * Demo 5 — Backwards write 反模式(组合中写状态)
     *
     * 反模式:
     *   - 在 Composable body 中读取 state 后又写入 state → 每次重组触发新的写入 → 进入无限重组循环
     *   - Compose 核心假设:可组合函数不向已读状态写入数据
     *
     * 正确:
     *   - 把写入放进事件处理(onClick / LaunchedEffect)
     *   - 用 SideEffect / DisposableEffect 等受控副作用 API
     */
    @Composable
    fun demo5_backwardsWrite() {
        var count by remember { mutableIntStateOf(0) }
        Column {
            Button(onClick = { count++ }) { Text("Increment") }
            Text("Count: $count")
            // ❌ 错误示例(注释掉,避免无限循环):
            // count++  // backwards write,读取后写入 → 每帧重组 + 写入 + 重组 ...
        }
    }

    /**
     * Demo 5b — Draw 阶段读 color(同 Demo 4 思路,扩展到 draw 阶段)
     *
     * 适用:动画中的颜色切换、滚动驱动的高频绘制
     */
    @Composable
    fun demo5_drawPhase(colorProvider: () -> Color) {
        // 错误:.background(color) → 每次 color 变化都重组 Box
        // Box(Modifier.fillMaxSize().background(colorProvider()))

        // 正确:.drawBehind 在 Draw 阶段读 color → 跳过 Composition + Layout
        Box(
            Modifier
                .fillMaxSize()
                .drawBehind { drawRect(colorProvider()) }
        )
    }
}