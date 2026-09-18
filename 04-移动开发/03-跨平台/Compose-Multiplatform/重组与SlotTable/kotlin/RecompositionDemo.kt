// Compose 重组机制的代码侧观察:编译器把 @Composable 函数改写成什么样子,
// 以及四类 group 各自对应什么写法。
//
// 权威依据(2026-09-18 实读):
//   * androidx `SlotTable.kt` 字段注释(经 github.com/CarGuo/gsy_flutter_book 引用而读到);
//   * androidx commit 01940843 的 API dump(SlotTable/SlotReader/SlotWriter 的方法面);
//   * deepwiki 对 JetBrains compose-multiplatform-core 的解读(四类 group 的语义);
//   * androiddevkit.com 的 Compose 编译器转换示例(startRestartGroup/跳过逻辑的生成形态)。
// 后两者是第三方整理,本文件把与它们相关的结论都标注了出来。
//
// 本机无 Kotlin/Compose 工具链,不做编译,供人工审查。

package demo.compose

import androidx.compose.runtime.Composable
import androidx.compose.runtime.Composer
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue

// ---------------------------------------------------------------------------
// 1. 编译器把 @Composable 改写成什么
// ---------------------------------------------------------------------------
//
// 下面这个函数:
//
//   @Composable
//   fun Greeting(name: String) { Text("Hi $name") }
//
// 大致会被改写成(androiddevkit 给出的形态):
//
//   fun Greeting(name: String, $composer: Composer, $changed: Int) {
//       $composer.startRestartGroup(...)
//       if ($changed and 0b1 == 0 && $composer.skipping) {
//           $composer.skipToGroupEnd()          // 参数没变 -> 整段跳过
//       } else {
//           Text("Hi $name", $composer)
//       }
//       $composer.endRestartGroup()?.updateScope { Greeting(name, it, $changed) }
//   }
//
// 四个可直接观察的后果:
//   * 每个 composable 多出 `$composer` 与 `$changed` 两个隐藏参数;
//   * "能不能跳过"由 `$changed` 位掩码 + `$composer.skipping` 共同决定;
//   * 跳过时用 `skipToGroupEnd()` —— 它依赖 group 头部里记着的子树规模,
//     这正是 SlotTable 模型里 skip_to_group_end(index) == index + groupSize(index) 的那条;
//   * `updateScope { ... }` 把"怎么重新执行这个 scope"注册下来,
//     与 RecomposeScope.invalidate() 配对使用。

// ---------------------------------------------------------------------------
// 2. Restart group:重组的最小单位
// ---------------------------------------------------------------------------

@Composable
fun CounterRow(count: Int, label: String) {
    // 每个非 inline 的 @Composable 函数大致对应一个 restart group。
    // 只读 count 的部分与只读 label 的部分各自成为独立 scope,互不牵连。
    CountText(count)
    LabelText(label)
}

@Composable
private fun CountText(count: Int) {
    // 只在 count 变化时重组
    println("count = $count")
}

@Composable
private fun LabelText(label: String) {
    // label 不变时整个函数被跳过(skipToGroupEnd)
    println("label = $label")
}

// ---------------------------------------------------------------------------
// 3. Replaceable group:控制流 —— 只能原地插入/删除/替换,不能移动
// ---------------------------------------------------------------------------

@Composable
fun Conditional(flag: Boolean) {
    if (flag) {
        Text0("on")
    } else {
        Text0("off")
    }
    // 编译器会在这里插入 replaceable group:分支切换时旧 group 被删、新 group 被插,
    // 但**不允许**把这段搬到别处 —— 位置变了就没有身份可言。
}

@Composable
private fun Text0(s: String) = println(s)

// ---------------------------------------------------------------------------
// 4. Movable group:key() —— 位置 + dataKey 双标识
// ---------------------------------------------------------------------------

@Composable
fun ItemList(items: List<String>) {
    items.forEach { item ->
        // 不加 key:身份只看"第几个",列表头部插入一项会让后面全部重新组合。
        // 加 key:身份由 (位置, item) 共同决定,重排时 group 可以整体搬家。
        key(item) {
            Text0(item)
        }
    }
}

// ---------------------------------------------------------------------------
// 5. Node group:真正的 UI 节点
// ---------------------------------------------------------------------------
//
// 布局/绘制节点的创建与更新走另一条路径:
//   * `createNode` 只在**首次组合**(inserting == true)时调用;
//   * 重组时用 `useNode` 复用已有节点;
//   * 属性更新通过记录 `apply { ... }` 在组合结束、changes 应用之后统一执行。
//
// 由此得到一条重要推论(第三方整理里明确写过):
// **重组被中断时,composable 里执行过的副作用不会反映到 SlotTable** ——
// 因为 applyChanges 必须发生在一次成功的组合之后。
// 所以 composable 必须是幂等、无副作用的,副作用要走 SideEffect / DisposableEffect。

// ---------------------------------------------------------------------------
// 6. 状态与快照:谁被读过,谁负责失效
// ---------------------------------------------------------------------------

@Composable
fun SnapshotDemo() {
    var count by remember { mutableStateOf(0) }
    // 读取发生在哪个 scope 内,就由哪个 scope 订阅这次读:
    //   Composition 侧维护 "状态对象 -> 读取它的 scope 集合" 的映射,
    //   状态写入时只让这些 scope 失效,再由 Recomposer 在下一帧统一重跑。
    //   因此"把状态读在尽可能低的位置"才有意义 —— 读的位置就是失效的粒度。
    println("count = $count")
}

// 快照提供事务式隔离:一次组合在独立快照里进行,子快照能看见父快照,父看不见子的改动,
// 直到 apply()。这也是"组合中途被打断不会留下半成品"的机制来源(第三方整理口径)。

// ---------------------------------------------------------------------------
// 7. 与 SlotTable 结构模型(../python/)的对应关系
// ---------------------------------------------------------------------------
//
//   startRestartGroup / endRestartGroup   ↔ start_group / end_group
//   跳过整段 scope                          ↔ skip_to_group_end(index)
//   同一个 remember 在多次重组间返回同值      ↔ 该 group 的槽区(位置记忆,positional memoization)
//   锚点(Anchor)随间隙移动被维护           ↔ move_slots_gap_to 里的锚点平移
//   删掉一个 group 时锚点失效                ↔ AnchorInvalidated
//
// 一句话:Compose 用"一棵用一维数组 + 间隙表达的树"换来了"重组时几乎不搬数据"。
