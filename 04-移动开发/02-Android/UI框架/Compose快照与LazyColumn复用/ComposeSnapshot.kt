// Compose 快照 + LazyColumn 的 Kotlin 等价实现（与 snapshot_core.py / main.py 对偶）。

object ComposeSnapshot {

    const val PREEXISTING_SNAPSHOT_ID = 1

    // ---- 1. StateRecord / StateObject ----
    class StateRecord(val snapshotId: Int, val value: Any?)

    class StateObject(initial: Any?, val merge: ((StateRecord, StateRecord, StateRecord) -> StateRecord?)? = null) {
        val records = mutableListOf(StateRecord(PREEXISTING_SNAPSHOT_ID, initial))

        fun readable(snapshotId: Int, invalid: Set<Int> = emptySet()): StateRecord? =
            records.filter { it.snapshotId <= snapshotId && it.snapshotId !in invalid }
                .maxByOrNull { it.snapshotId }

        fun write(snapshotId: Int, value: Any?) {
            records.add(StateRecord(snapshotId, value))
            records.sortBy { it.snapshotId }
        }
    }

    sealed class ApplyResult {
        data object Success : ApplyResult()
        class Failure(val snapshot: MutableSnapshot) : ApplyResult()
    }

    class GlobalSnapshot(var snapshotId: Int)

    class MutableSnapshot(
        val snapshotId: Int,
        val takenFrom: Int,
        private val system: SnapshotSystem,
    ) {
        val modified = LinkedHashMap<StateObject, StateRecord>()
        var disposed = false

        fun set(state: StateObject, value: Any?) {
            check(!disposed) { "Cannot use a disposed snapshot" }
            modified[state] = StateRecord(snapshotId, value)
        }

        fun apply(nextSnapshotId: Int): ApplyResult {
            check(!disposed) { "Cannot use a disposed snapshot" }
            if (modified.isEmpty()) return ApplyResult.Success   // 空修改永不失败

            val plan = LinkedHashMap<StateObject, Any?>()
            for ((state, local) in modified) {
                val previous = state.readable(takenFrom)
                if (previous == null || previous.snapshotId == PREEXISTING_SNAPSHOT_ID) {
                    plan[state] = local.value                    // ① 初始记录不判冲突
                    continue
                }
                val globalCurrent = state.readable(system.global.snapshotId)
                if (globalCurrent === previous) {
                    plan[state] = local.value                    // ② 无人写过 ⇒ 无碰撞
                    continue
                }
                val merged = state.merge?.invoke(previous, globalCurrent!!, local)
                    ?: return ApplyResult.Failure(this)          // ③ 合并不了 → 失败
                plan[state] = when (merged) {
                    local -> local.value                         // 本地掩盖冲突
                    globalCurrent -> globalCurrent.value         // 回退到全局值
                    else -> merged.value                         // 协商值
                }
            }
            plan.forEach { (state, value) -> state.write(nextSnapshotId, value) }
            disposed = true
            return ApplyResult.Success
        }
    }

    class SnapshotSystem(startId: Int = PREEXISTING_SNAPSHOT_ID) {
        var global = GlobalSnapshot(startId)
        private var nextId = startId + 1

        fun takeMutableSnapshot(): MutableSnapshot =
            MutableSnapshot(nextId++, global.snapshotId, this)
    }
}

// ---- LazyColumn ----

object LazyColumnModel {

    data class MeasureResult(
        val firstIndex: Int,
        val firstOffset: Int,
        val visible: List<Int>,
        val consumedScroll: Float,
        val canScrollForward: Boolean,
        val hasVisibleItems: Boolean,
    )

    fun measureLazyList(
        itemsCount: Int,
        itemSizes: List<Int>,
        viewport: Int,
        firstVisibleItemIndex: Int = 0,
        firstVisibleItemScrollOffset: Int = 0,
        scrollToBeConsumed: Float = 0f,
        spaceBetweenItems: Int = 0,
        beforeContentPadding: Int = 0,
        afterContentPadding: Int = 0,
        beyondBoundsItemCount: Int = 0,
        pinnedIndices: Set<Int> = emptySet(),
    ): MeasureResult {
        if (itemsCount <= 0 || itemSizes.isEmpty()) {
            return MeasureResult(0, 0, emptyList(), 0f, false, false)
        }
        var index = firstVisibleItemIndex
        var offset = firstVisibleItemScrollOffset
        if (index >= itemsCount) { index = itemsCount - 1; offset = 0 }

        var scrollDelta = kotlin.math.round(scrollToBeConsumed).toInt()
        offset -= scrollDelta
        if (index == 0 && offset < 0) {
            scrollDelta += offset
            offset = 0
        }

        val available = viewport + beforeContentPadding + afterContentPadding
        val visible = mutableListOf<Int>()
        var used = -offset
        var i = index
        while (i < itemsCount) {
            val spacing = if (i == itemsCount - 1) 0 else spaceBetweenItems
            visible.add(i)
            used += itemSizes[i % itemSizes.size] + spacing
            i++
            if (used >= available) break
        }
        repeat(beyondBoundsItemCount) {
            if (i < itemsCount) { visible.add(i); i++ }
        }
        pinnedIndices.filter { it in 0 until itemsCount && it !in visible }.forEach(visible::add)
        return MeasureResult(index, offset, visible, scrollDelta.toFloat(), i < itemsCount, true)
    }

    class LazyListState(var firstVisibleItemIndex: Int = 0, var firstVisibleItemScrollOffset: Int = 0) {
        private var lastKnownKey: Any? = null

        fun updateScrollPositionIfTheFirstItemWasMoved(keys: List<Any?>, index: Int): Int {
            if (keys.isEmpty()) { lastKnownKey = null; return 0 }
            if (lastKnownKey == null) {
                lastKnownKey = keys.getOrNull(index) ?: keys.first()
                return if (index < keys.size) index else 0
            }
            if (keys.getOrNull(index) == lastKnownKey) return index
            val newIndex = keys.indexOf(lastKnownKey)
            return if (newIndex >= 0) {
                firstVisibleItemIndex = newIndex
                newIndex
            } else {
                firstVisibleItemIndex = 0
                firstVisibleItemScrollOffset = 0
                0
            }
        }
    }
}
