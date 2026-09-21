# Dart 事件循环：微任务链表、Timer 堆与零延迟队列

> 目录：`04-移动开发/03-跨平台/Flutter/事件循环与Timer/`
> 语言：Python（`python/main.py` + `python/selfcheck_loop.py`，**35 断言实跑全绿**）/ Go（`go/loop.go` + `go/main.go` 人工审查 + bracket_check + go_sanity + go_crossref）

## 一、简介

Flutter 的 UI 线程就是一个 Dart isolate：一个事件循环 + 两条队列。真正决定「谁先跑」的不是文档里的示意图，而是 `dart:async` 的三段实现：

1. **微任务队列**（`schedule_microtask.dart`）——一根单链表，不是队列；
2. **零延迟计时器**（`timer_impl.dart`）——`Timer(Duration.zero)` 与 `Timer.run` 走另一条 FIFO 链表，且**每个都独占一条消息**；
3. **非零延迟计时器**——放在一棵二叉小顶堆里，按「唤醒时刻 + id」排序。

本 demo 把这三段逐行转写成可执行模型，并把「新的异步任务什么时候才会被执行」变成断言。

## 二、原理

### 2.1 微任务：单链表 + 一个「循环内」标志

```dart
void _scheduleAsyncCallback(_AsyncCallback callback) {
  ...
  if (lastCallback == null) {
    _nextCallback = _lastCallback = newEntry;
    if (!_isInCallbackLoop) { _AsyncRun._scheduleImmediate(_startMicrotaskLoop); }
  } else { lastCallback.next = newEntry; _lastCallback = newEntry; }
}
```

要点：

- 只有**从空变非空**且**当前不在 loop 里**时才排一次 immediate。因此 loop 执行中新增的微任务会在**同一轮**被排空（本 demo A3/A4），loop 退出后新增的才需要新的 immediate（A5）。
- `_startMicrotaskLoop` 的 `finally` 里会检查「排空后是否又有新的」，有就再排一次。

### 2.2 优先级回调：插在上一个优先级之后

`_schedulePriorityAsyncCallback` 只给**错误上报**用。它不是插到队首，而是插到 `_lastPriorityCallback` 之后：

```
已有 [a]，插入 p1           -> p1 -> a
p1 执行中插入 p2、p3         -> p2 -> p3 -> a
```

`_microtaskLoop` 每轮开头把 `_lastPriorityCallback` 置 null，所以「优先级区间」每轮重新划定。

> 源码注释给了一个饥饿的例子：`Timer.run(...)` 里递归 `scheduleMicrotask(foo)` 会让 Timer **永远得不到执行**。微任务排空之前不会让出事件循环。

### 2.3 Timer 堆：二叉小顶堆，容量 7 → 15 → 31

| 属性 | 值 |
| --- | --- |
| 初始容量 | `7` |
| 扩容 | `_list.length * 2 + 1` |
| 父/子下标 | `(i-1) ~/ 2` / `2i+1`、`2i+2` |
| 排序 | 先比 `_wakeupTime`，相等再比 `_id`（同时刻 FIFO） |
| id 回绕 | `_ID_MASK = 0x1fffffff`，源码注释承认回绕后可能碰撞与乱序 |

`remove` 的做法是「用堆尾元素补位，再按比较结果决定上浮还是下沉」。

### 2.4 零延迟计时器：另一条链表，一条消息一个

`_milliSeconds == 0`（含**负数**，建timer 时被当作 0）的计时器不进堆，而是挂到 `_firstZeroTimer` 链表，并且**每个都往 timer port 发一条 `_ZERO_EVENT = 1` 消息**（超时事件是 `_TIMEOUT_EVENT = null`）。

两个取事件的分支：

| 分支 | 取什么 |
| --- | --- |
| `_queueFromZeroEvent` | 先把堆里**比首个零延迟计时器更早**的项取出，再取这个零延迟项 |
| `_queueFromTimeoutEvent` | 有零延迟时同样只取更早的；没有零延迟时才按当前时刻取所有 `_wakeupTime <= now` |

注意 `_queueFromTimeoutEvent` 在**有零延迟计时器时不看当前时刻**（本 demo D2：now=2000，堆里 1500 也不会被取走，因为零延迟是 1000）。

### 2.5 `_runTimers`：快照式派发 + 逾期补偿

- 进入时若堆与零延迟都空 → `_idCount = 0`，回收 id 空间。
- 派发的是**快照列表**：回调里新建的计时器不会在这一轮跑（E7/E8）。
- 每个回调后调用 `_runPendingImmediateCallback()`，**微任务插在每个计时器回调之后**（F1）。
- 周期计时器补偿：`millisecondsOverdue > _milliSeconds` 时

```dart
int missedTicks = millisecondsOverdue ~/ ms;
timer._wakeupTime += missedTicks * ms;
timer._tick += missedTicks;
```

随后 `tick += 1`，再 `_advanceWakeupTime()`（非零周期 `+= ms`，零周期取当前时刻）并重新入队。周期 100ms、唤醒 1100、当前 1350 → `missedTicks = 2`、`tick = 3`、下次唤醒 1400。
- 逾期 ≥ 100ms 且非 `dart.vm.product` 时会额外投递一个性能事件。

## 三、对比

| 维度 | JavaScript | Dart |
| --- | --- | --- |
| 微任务结构 | 队列 | **单链表 + 优先级插入位** |
| `setTimeout(0)` / `Timer(Duration.zero)` | 同一个宏任务队列，按最小堆延时 | **独立 FIFO 链表，一条消息一个** |
| 同时刻排序 | 按插入顺序 | 按 `_id`（有回绕上限） |
| 回调里新建的任务 | 下一轮 | 下一轮（快照式派发） |
| 周期任务逾期 | 每轮只补一次 | `missedTicks` 一次补齐并累加 `tick` |

## 四、环境

- Python 3.13（标准库）
- Go 1.21+（无本机工具链，Go 版只做人工审查与静态检查）

## 五、运行

```bash
cd python && python main.py             # 微任务顺序 / 堆出队 / 零延迟消息数
cd python && python selfcheck_loop.py   # 35 项断言
```

## 六、关键代码

| 文件 | 对应源码 |
| --- | --- |
| `python/main.py:AsyncRuntime` | `sdk/lib/async/schedule_microtask.dart:34-105` |
| `python/main.py:TimerHeap` | `sdk/lib/_internal/vm/lib/timer_impl.dart:22-111` |
| `python/main.py:TimerRuntime._enqueue` | `timer_impl.dart:266-283` |
| `python/main.py:TimerRuntime.queue_from_zero_event` | `timer_impl.dart:298-318` |
| `python/main.py:TimerRuntime.run_timers` | `timer_impl.dart:367-436` |

## 七、性能边界

- 微任务是**不可让出的**：排空微任务链表之前不会处理任何事件，递归 `scheduleMicrotask` 会饿死 Timer（源码注释原话）。
- 零延迟计时器**每个一条消息**，大量 `Timer.run` 会造成消息风暴；非零延迟计时器只在堆顶变化时才向 event handler 更新唤醒时刻（`_notifyEventHandler` 里比较 `_scheduledWakeupTime`）。
- `_handlingCallbacks` 期间 `_notifyEventHandler` 直接返回，避免回调里重复唤醒。
- `remove` 任意元素是 O(log n)，但 `_bubbleUp`/`_bubbleDown` 都要走一次 `compareTo`。

## 八、坑

1. **`Timer(Duration(seconds: -1))` 等于 0 延迟**（`_createTimer` 里负数归零），不是抛异常。
2. **零延迟计时器即使被 cancel 也要留在链表里**——它得「消费掉」自己那条已发出的消息（源码注释）。
3. **微任务里再排微任务不会让出事件循环**，Flutter 里这会直接卡掉一帧。
4. **有零延迟计时器时，`_queueFromTimeoutEvent` 不按当前时刻取**，堆里已经到期的项也要等。
5. **id 会回绕**（`0x1fffffff`），同一毫秒内的顺序在回绕后不再可信。
6. **周期计时器的 `tick` 是「补上的 + 1」**，不是「执行次数」；逾期 250ms / 周期 100ms 时一次回调就让 tick 从 0 变 3。
7. Go 移植时 `(index - 1) ~/ 2` 对非负数等于 `/2`，但 Dart 的 `~/` 是**向零取整**，负数下标不存在但语义别混用。

## 九、参考资料（实际读过）

- `dart-lang/sdk@main` — `sdk/lib/async/schedule_microtask.dart`、`sdk/lib/_internal/vm/lib/timer_impl.dart`、`sdk/lib/isolate/isolate.dart`、`sdk/lib/_internal/vm/lib/isolate_patch.dart`
  （经 `cdn.jsdelivr.net/gh/dart-lang/sdk@main/...` 抓取）
- `schedule_microtask.dart` 文档注释里的 [The Event Loop and Dart](https://dart.dev/articles/event-loop/)
- `Isolate.spawn` 签名默认值取自 `isolate_patch.dart:366`（`paused = false`、`errorsAreFatal = true`）
